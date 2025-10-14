"""
Attachment manager for storing message attachments using APFS clones.

This module handles the storage of attachments from messages, using
APFS copy-on-write clones when possible to avoid duplicating storage.
"""

import os
import subprocess
import mimetypes
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import structlog

from memory_database.utils.ulid import generate_ulid

logger = structlog.get_logger()


class AttachmentManager:
    """Manages attachment storage with APFS cloning support."""
    
    def __init__(self, base_path: Optional[Path] = None):
        """
        Initialize the attachment manager.
        
        Args:
            base_path: Base directory for storing attachments.
                      Defaults to ~/Memories/attachments
        """
        if base_path is None:
            base_path = Path.home() / "Memories" / "attachments"
        
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        
        self.logger = logger.bind(component="AttachmentManager")
        self.logger.info("Initialized attachment manager", base_path=str(self.base_path))
    
    def store_attachment(
        self, 
        source_path: Path, 
        message_id: str,
        sent_at: datetime,
        attachment_index: int = 0
    ) -> Dict[str, Any]:
        """
        Store an attachment using APFS clone if possible.
        
        Args:
            source_path: Path to the original attachment file
            message_id: ID of the message this attachment belongs to
            sent_at: Timestamp of the message (for organization)
            attachment_index: Index of this attachment in the message
            
        Returns:
            Dictionary with attachment metadata including stored path
        """
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")
        
        # Generate unique ID for this attachment
        attachment_id = generate_ulid()
        
        # Organize by year/month for easier browsing
        year_month = sent_at.strftime("%Y/%m")
        storage_dir = self.base_path / year_month
        storage_dir.mkdir(parents=True, exist_ok=True)
        
        # Create unique filename with ULID prefix
        stored_filename = f"{attachment_id}_{source_path.name}"
        stored_path = storage_dir / stored_filename
        
        # Store the file using the most efficient method
        storage_method = self._store_file(source_path, stored_path)
        
        # Get file metadata
        stat = stored_path.stat()
        mime_type = mimetypes.guess_type(str(source_path))[0]
        
        # Extract dimensions for images/videos if possible
        width, height, duration = self._extract_media_dimensions(stored_path, mime_type)
        
        result = {
            "id": attachment_id,
            "message_id": message_id,
            "original_path": str(source_path),
            "stored_path": str(stored_path),
            "filename": source_path.name,
            "file_size": stat.st_size,
            "mime_type": mime_type,
            "storage_method": storage_method,
            "attachment_index": attachment_index,
            "width": width,
            "height": height,
            "duration": duration
        }
        
        self.logger.info(
            "Stored attachment",
            attachment_id=attachment_id,
            filename=source_path.name,
            size=stat.st_size,
            method=storage_method,
            mime_type=mime_type
        )
        
        return result
    
    def _store_file(self, source: Path, destination: Path) -> str:
        """
        Store a file using the most efficient method available.
        
        Args:
            source: Source file path
            destination: Destination file path
            
        Returns:
            Storage method used ('clone', 'hardlink', or 'copy')
        """
        # Try APFS clone first (most efficient)
        try:
            subprocess.run(
                ["cp", "-c", str(source), str(destination)],
                check=True,
                capture_output=True,
                text=True
            )
            return "clone"
        except subprocess.CalledProcessError as e:
            self.logger.debug(
                "APFS clone failed, trying hardlink",
                error=e.stderr,
                source=str(source),
                dest=str(destination)
            )
        
        # Try hardlink (still efficient, same filesystem required)
        try:
            os.link(source, destination)
            return "hardlink"
        except OSError as e:
            self.logger.debug(
                "Hardlink failed, falling back to copy",
                error=str(e),
                source=str(source),
                dest=str(destination)
            )
        
        # Fall back to regular copy
        import shutil
        shutil.copy2(source, destination)
        return "copy"
    
    def _extract_media_dimensions(
        self, 
        file_path: Path, 
        mime_type: Optional[str]
    ) -> tuple[Optional[int], Optional[int], Optional[float]]:
        """
        Extract dimensions from image/video files.
        
        Args:
            file_path: Path to the media file
            mime_type: MIME type of the file
            
        Returns:
            Tuple of (width, height, duration)
        """
        if not mime_type:
            return None, None, None
        
        # For now, return None - we can implement actual extraction later
        # using libraries like Pillow for images or ffprobe for videos
        return None, None, None
    
    def verify_attachment(self, stored_path: str) -> bool:
        """
        Verify that an attachment is still accessible.
        
        Args:
            stored_path: Path to the stored attachment
            
        Returns:
            True if the file exists and is readable
        """
        try:
            path = Path(stored_path)
            return path.exists() and path.is_file() and os.access(path, os.R_OK)
        except Exception as e:
            self.logger.error(
                "Failed to verify attachment",
                path=stored_path,
                error=str(e)
            )
            return False
    
    def get_attachment_url(self, stored_path: str) -> str:
        """
        Get a file:// URL for the attachment.
        
        Args:
            stored_path: Path to the stored attachment
            
        Returns:
            file:// URL for the attachment
        """
        path = Path(stored_path).resolve()
        return f"file://{path}"