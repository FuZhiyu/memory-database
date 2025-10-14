"""
Mock AttachmentManager for testing without filesystem operations.
"""
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
import hashlib


class MockAttachmentManager:
    """Mock attachment manager that simulates storage without filesystem access."""
    
    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = base_path or Path("/tmp/mock_attachments")
        self.stored_attachments = {}
        self.storage_calls = []
    
    def store_attachment(
        self,
        source_path: Path,
        message_id: str,
        sent_at: datetime,
        attachment_index: int
    ) -> Dict[str, Any]:
        """
        Mock storing an attachment.
        
        Returns mock attachment data without actually copying files.
        """
        # Generate mock attachment ID
        attachment_id = hashlib.md5(
            f"{message_id}_{attachment_index}".encode()
        ).hexdigest()[:16]
        
        # Generate mock stored path
        year = sent_at.year
        month = sent_at.month
        filename = source_path.name
        stored_path = self.base_path / str(year) / str(month) / f"{attachment_id}_{filename}"
        
        # Create mock attachment data
        attachment_data = {
            'id': attachment_id,
            'original_path': str(source_path),
            'stored_path': str(stored_path),
            'filename': filename,
            'file_size': 1024 * (attachment_index + 1),  # Mock size
            'mime_type': self._get_mock_mime_type(filename),
            'width': 800 if filename.lower().endswith(('.jpg', '.jpeg', '.png')) else None,
            'height': 600 if filename.lower().endswith(('.jpg', '.jpeg', '.png')) else None,
            'duration': 120.5 if filename.lower().endswith(('.mov', '.mp4')) else None,
            'storage_method': 'mock'
        }
        
        # Track the storage call
        self.storage_calls.append({
            'source_path': source_path,
            'message_id': message_id,
            'sent_at': sent_at,
            'attachment_index': attachment_index,
            'result': attachment_data
        })
        
        # Store in memory
        self.stored_attachments[attachment_id] = attachment_data
        
        return attachment_data
    
    def _get_mock_mime_type(self, filename: str) -> str:
        """Get mock MIME type based on file extension."""
        ext = Path(filename).suffix.lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.mov': 'video/quicktime',
            '.mp4': 'video/mp4',
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
        }
        return mime_types.get(ext, 'application/octet-stream')
    
    def get_stored_attachment(self, attachment_id: str) -> Optional[Dict[str, Any]]:
        """Get a stored attachment by ID."""
        return self.stored_attachments.get(attachment_id)
    
    def clear(self):
        """Clear all stored attachments (for test cleanup)."""
        self.stored_attachments.clear()
        self.storage_calls.clear()
    
    def get_storage_stats(self) -> Dict[str, int]:
        """Get statistics about stored attachments."""
        return {
            'total_stored': len(self.stored_attachments),
            'total_calls': len(self.storage_calls),
            'total_size': sum(
                att['file_size'] for att in self.stored_attachments.values()
            ),
            'images': sum(
                1 for att in self.stored_attachments.values()
                if att['mime_type'].startswith('image/')
            ),
            'videos': sum(
                1 for att in self.stored_attachments.values()
                if att['mime_type'].startswith('video/')
            ),
        }