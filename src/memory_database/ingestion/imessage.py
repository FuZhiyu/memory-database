"""
iMessage ingestion source using direct database access via Rust bridge.

This module provides ingestion capabilities for iMessage data, including
messages, attachments, and participant information, with identity resolution
to link messages to existing principals in the database.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
import structlog
from tqdm import tqdm

from memory_database.ingestion.base import IngestionSource
from memory_database.utils.normalization import normalize_phone, normalize_email, extract_identity_kind
from memory_database.utils.identity_resolver import find_existing_principal, link_or_create_principal
from memory_database.storage.attachment_manager import AttachmentManager

logger = structlog.get_logger()

# Import will be available after building the Rust extension
try:
    import imessage_bridge
except ImportError:
    logger.warning("imessage_bridge not available. Run 'maturin develop' in imessage-bridge/ to build")
    imessage_bridge = None


class iMessageIngestionSource(IngestionSource):
    """Ingestion source for iMessage database."""
    
    def __init__(self, db_manager, db_path: Optional[str] = None):
        """
        Initialize iMessage ingestion source.
        
        Args:
            db_manager: Database manager instance
            db_path: Optional path to iMessage database (uses default if None)
        """
        super().__init__(db_manager)
        self.db_path = db_path
        self.imessage_db = None
        self.handle_cache = {}  # Cache for handle lookups
        self.batch_size = int(os.environ.get("MEMORY_DB_IMESSAGE_BATCH_SIZE", "500"))

        attachments_root = os.environ.get(
            "MEMORY_DB_ATTACHMENTS_ROOT",
            str(Path.home() / "Library" / "Messages" / "Attachments"),
        )
        self.attachments_root = Path(attachments_root).expanduser()
        try:
            self._resolved_attachments_root = self.attachments_root.resolve(strict=False)
        except (OSError, RuntimeError):
            self._resolved_attachments_root = self.attachments_root

        self.attachment_manager = AttachmentManager()  # Initialize attachment manager
        
    def get_platform_name(self) -> str:
        return "imessage"
    
    def count_items(self, source_path: str) -> Optional[int]:
        """Count total messages in the iMessage database."""
        if source_path == 'default':
            source_path = None
        
        if not self.imessage_db:
            self.db_path = source_path
            try:
                self.connect()
            except Exception:
                return None

        return None
    
    def connect(self):
        """Connect to the iMessage database."""
        if not imessage_bridge:
            raise ImportError("imessage_bridge module not available. Build the Rust extension first.")
        
        self.imessage_db = imessage_bridge.IMessageDB(self.db_path)
        self.logger.info("Connected to iMessage database", path=self.imessage_db.path)
        
        # Pre-cache all handles for efficient lookups
        self._cache_handles()
    
    def _cache_handles(self):
        """Cache all handles for efficient participant lookups."""
        if not self.imessage_db:
            return
        
        handles = self.imessage_db.get_all_handles()
        for handle in handles:
            self.handle_cache[handle.rowid] = {
                'id': handle.id,
                'service': handle.service,
                'uncanonicalized_id': handle.uncanonicalized_id
            }
        
        self.logger.info("Cached handles", count=len(self.handle_cache))
    
    def extract_raw_data(self, source_path: str) -> Iterator[Dict[str, Any]]:
        """
        Extract messages from iMessage database.
        
        Args:
            source_path: Path to iMessage database or 'default' for system database
            
        Yields:
            Raw message dictionaries
        """
        if source_path == 'default':
            source_path = None
        
        if not self.imessage_db:
            self.db_path = source_path
            self.connect()
        
        processed = 0

        for msg in self._stream_messages():
            try:
                # Get participants for this message
                participants = self.imessage_db.get_message_participants(msg.rowid)
                
                # Get attachments
                attachments = self.imessage_db.get_message_attachments(msg.rowid)
                
                # Get sender handle if available
                sender_handle = None
                if msg.handle_id:
                    sender_handle = self.handle_cache.get(msg.handle_id)
                
                payload = {
                    'rowid': msg.rowid,
                    'guid': msg.guid,
                    'text': msg.text,
                    'service': msg.service,
                    'handle_id': msg.handle_id,
                    'sender_handle': sender_handle,
                    'subject': msg.subject,
                    'date': msg.date,
                    'date_read': msg.date_read,
                    'date_delivered': msg.date_delivered,
                    'is_from_me': msg.is_from_me,
                    'is_read': msg.is_read,
                    'is_sent': msg.is_sent,
                    'is_delivered': msg.is_delivered,
                    'cache_roomnames': msg.cache_roomnames,
                    'group_title': msg.group_title,
                    'associated_message_guid': msg.associated_message_guid,
                    'associated_message_type': msg.associated_message_type,
                    'thread_originator_guid': msg.thread_originator_guid,
                    'participants': [
                        {
                            'rowid': p.rowid,
                            'id': p.id,
                            'service': p.service,
                            'uncanonicalized_id': p.uncanonicalized_id
                        }
                        for p in participants
                    ],
                    'attachments': [
                        {
                            'rowid': a.rowid,
                            'guid': a.guid,
                            'filename': a.filename,
                            'mime_type': a.mime_type,
                            'transfer_name': a.transfer_name,
                            'total_bytes': a.total_bytes
                        }
                        for a in attachments
                    ]
                }

                processed += 1
                yield payload

            except Exception as e:
                self.logger.error("Failed to process message", 
                                rowid=msg.rowid, error=str(e))
                continue

        self.logger.info("Processed messages from iMessage database", count=processed)
    
    def normalize_message(self, raw_message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize iMessage data to standard message format.
        
        Args:
            raw_message: Raw message from iMessage database
            
        Returns:
            Normalized message dictionary
        """
        # Convert timestamp to datetime
        sent_at = datetime.fromtimestamp(raw_message['date'], tz=timezone.utc)
        
        # Determine sender and recipients
        sender = None
        recipients = []
        
        if raw_message['is_from_me']:
            # Message sent by the user
            sender = 'me@imessage'  # Special identifier for self
            
            # Recipients are the participants
            for participant in raw_message.get('participants', []):
                if participant['id']:
                    recipients.append(participant['id'])
        else:
            # Message received from someone else
            if raw_message.get('sender_handle'):
                sender = raw_message['sender_handle']['id']
            elif raw_message.get('participants'):
                # Use first participant as sender for group messages
                sender = raw_message['participants'][0]['id'] if raw_message['participants'] else None
            
            # User is the recipient
            recipients = ['me@imessage']
        
        # Build content with attachment references
        content = raw_message.get('text', '')
        if raw_message.get('attachments'):
            attachment_refs = []
            for att in raw_message['attachments']:
                if att.get('filename'):
                    attachment_refs.append(f"[Attachment: {att['filename']}]")
                elif att.get('transfer_name'):
                    attachment_refs.append(f"[Attachment: {att['transfer_name']}]")
                else:
                    attachment_refs.append(f"[Attachment: {att.get('mime_type', 'unknown')}]")
            
            if attachment_refs:
                if content:
                    content += '\n' + '\n'.join(attachment_refs)
                else:
                    content = '\n'.join(attachment_refs)
        
        # Determine thread/channel info
        thread_id = raw_message.get('cache_roomnames') or raw_message.get('thread_originator_guid')
        if not thread_id and len(raw_message.get('participants', [])) > 1:
            # Group message without room name - use participant list as thread ID
            participant_ids = sorted([p['id'] for p in raw_message['participants']])
            thread_id = 'group_' + hashlib.md5('_'.join(participant_ids).encode()).hexdigest()[:8]
        elif not thread_id and sender:
            # Direct message - use sender as thread ID
            thread_id = f"dm_{sender}"
        
        return {
            'message_id': raw_message['guid'],
            'sent_at': sent_at,
            'sender': sender,
            'recipients': recipients,
            'content': content,
            'content_type': 'text/plain',
            'subject': raw_message.get('subject'),
            'thread_id': thread_id,
            'channel_name': raw_message.get('group_title') or 'iMessage',
            'reply_to': raw_message.get('associated_message_guid'),
            'extra': {
                'rowid': raw_message['rowid'],
                'service': raw_message['service'],
                'is_read': raw_message['is_read'],
                'is_sent': raw_message['is_sent'],
                'is_delivered': raw_message['is_delivered'],
                'date_read': raw_message['date_read'],
                'date_delivered': raw_message['date_delivered'],
                'associated_message_type': raw_message['associated_message_type'],
                'attachments': raw_message.get('attachments', [])
            }
        }
    
    def extract_identities(self, message: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract identity claims from an iMessage.
        
        Args:
            message: Normalized message dictionary
            
        Returns:
            List of identity claim dictionaries
        """
        identities = []
        
        # Process sender identity
        if sender := message.get('sender'):
            if sender != 'me@imessage':  # Skip self-identifier
                kind = extract_identity_kind(sender)
                identities.append({
                    'platform': self.get_platform_name(),
                    'kind': kind,
                    'value': sender,
                    'normalized': self._normalize_imessage_identity(sender, kind),
                    'confidence': 1.0,
                    'extra': {'role': 'sender'}
                })
        
        # Process recipient identities
        for recipient in message.get('recipients', []):
            if recipient != 'me@imessage':  # Skip self-identifier
                kind = extract_identity_kind(recipient)
                identities.append({
                    'platform': self.get_platform_name(),
                    'kind': kind,
                    'value': recipient,
                    'normalized': self._normalize_imessage_identity(recipient, kind),
                    'confidence': 1.0,
                    'extra': {'role': 'recipient'}
                })
        
        return identities
    
    def _normalize_imessage_identity(self, value: str, kind: str) -> str:
        """
        Normalize an iMessage identity value.
        
        Args:
            value: Raw identity value
            kind: Type of identity
            
        Returns:
            Normalized identity value
        """
        if kind == 'email':
            return normalize_email(value)
        elif kind == 'phone':
            # iMessage often includes country codes
            return normalize_phone(value)
        else:
            return value.lower().strip()
    
    def get_message_hash(self, message: Dict[str, Any]) -> str:
        """
        Generate a hash for message deduplication.
        
        Args:
            message: Normalized message dictionary
            
        Returns:
            Hash string for the message
        """
        # Use GUID as the primary identifier
        if message_id := message.get('message_id'):
            return hashlib.sha256(message_id.encode()).hexdigest()
        
        # Fallback to content + timestamp hash
        hash_data = {
            'sent_at': message.get('sent_at').isoformat() if message.get('sent_at') else '',
            'sender': message.get('sender', ''),
            'content': message.get('content', ''),
            'thread_id': message.get('thread_id', '')
        }

        hash_str = json.dumps(hash_data, sort_keys=True)
        return hashlib.sha256(hash_str.encode()).hexdigest()

    def _stream_messages(self) -> Iterator[Any]:
        """Yield messages in batches to avoid loading the entire corpus into memory."""
        if not self.imessage_db:
            return

        last_timestamp = 0.0
        safety_hatch = 0

        while True:
            try:
                batch = self.imessage_db.query_messages_after(last_timestamp, self.batch_size)
            except Exception as exc:
                self.logger.error("Failed to stream messages", error=str(exc))
                break

            if not batch:
                break

            for message in batch:
                message_ts = getattr(message, 'date', None)
                if message_ts is not None and message_ts > last_timestamp:
                    last_timestamp = message_ts
                yield message

            if len(batch) < self.batch_size:
                break

            safety_hatch += 1
            if safety_hatch > 1_000_000:
                self.logger.error(
                    "Aborting message stream; exceeded safety threshold",
                    last_timestamp=last_timestamp,
                )
                break

    def _is_within_attachments_root(self, path: Path) -> bool:
        try:
            resolved_candidate = path.resolve(strict=False)
            resolved_root = self._resolved_attachments_root.resolve(strict=False)
        except (OSError, RuntimeError):
            return False

        try:
            resolved_candidate.relative_to(resolved_root)
            return True
        except ValueError:
            return False

    def _resolve_path_within_root(self, candidate: Path) -> Optional[Path]:
        expanded = candidate.expanduser()
        try:
            resolved = expanded.resolve(strict=False)
        except (OSError, RuntimeError):
            self.logger.warning(
                "Failed to resolve attachment path",
                requested=str(candidate),
            )
            return None

        if not self._is_within_attachments_root(resolved):
            self.logger.warning(
                "Attachment path rejected (outside allowed root)",
                requested=str(candidate),
                root=str(self.attachments_root),
            )
            return None

        if resolved.exists() and resolved.is_file():
            return resolved

        return None

    def _safe_filename(self, name: str) -> str:
        return Path(name).name
    
    def resolve_attachment_path(self, guid: str, filename: Optional[str] = None) -> Optional[Path]:
        """
        Resolve iMessage attachment path from GUID.
        
        Args:
            guid: Attachment GUID from iMessage database
            filename: Optional filename to help locate the attachment
            
        Returns:
            Path to the attachment file or None if not found
        """
        attachments_root = self.attachments_root
        safe_filename = None

        if filename:
            safe_filename = self._safe_filename(filename)
            direct_candidate = Path(filename)
            if direct_candidate.is_absolute() or filename.startswith('~/'):
                safe_direct = self._resolve_path_within_root(direct_candidate)
                if safe_direct:
                    return safe_direct

                self.logger.warning(
                    "Attachment path rejected or missing",
                    guid=guid,
                    filename=filename,
                )

        # iMessage stores attachments in a structure like:
        # Attachments/xx/xx/guid/filename
        # Where xx are derived from the GUID
        
        # Parse GUID to determine directory structure
        parts = guid.split("-")
        
        if len(parts) >= 2:
            # Use hash part to determine subdirectories
            hash_part = parts[1] if parts[0] in ['bp', 'sms', 'iMessage'] else parts[0]
            
            if len(hash_part) >= 2:
                # Take first 2 chars for first level
                dir1 = hash_part[:2]
                # Take next 2 chars for second level, or use '00' if not enough chars
                dir2 = hash_part[2:4] if len(hash_part) >= 4 else "00"
                
                # Check the most likely location
                possible_path = attachments_root / dir1 / dir2 / guid

                if possible_path.exists() and possible_path.is_dir() and self._is_within_attachments_root(possible_path):
                    # If we have a filename, look for it specifically
                    if safe_filename:
                        candidate = possible_path / safe_filename
                        safe_candidate = self._resolve_path_within_root(candidate)
                        if safe_candidate:
                            return safe_candidate
                    else:
                        # Return the first file in the directory
                        for item in possible_path.iterdir():
                            if not item.is_file():
                                continue
                            candidate = item.resolve(strict=False)
                            if self._is_within_attachments_root(candidate):
                                return candidate
        
        # Fallback: search for the GUID directory
        self.logger.debug("Searching for attachment", guid=guid, filename=filename)
        
        try:
            for path in attachments_root.rglob(f"*/{guid}"):
                if path.is_dir() and self._is_within_attachments_root(path):
                    if safe_filename:
                        candidate = path / safe_filename
                        safe_candidate = self._resolve_path_within_root(candidate)
                        if safe_candidate:
                            return safe_candidate
                    else:
                        # Return the first file in the directory
                        for item in path.iterdir():
                            if not item.is_file():
                                continue
                            candidate = item.resolve(strict=False)
                            if self._is_within_attachments_root(candidate):
                                return candidate
        except Exception as e:
            self.logger.error("Error searching for attachment", guid=guid, error=str(e))
        
        return None


class iMessageIncrementalPipeline:
    """Pipeline for incremental iMessage import with identity resolution."""

    DEFAULT_REWIND_SECONDS = 30

    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.logger = structlog.get_logger()

    def _infer_last_ingested_state(self, session) -> Optional[Dict[str, Any]]:
        """Inspect the database to locate the most recent iMessage that was imported."""
        from memory_database.models import Message, Thread, Channel

        last_message = (
            session.query(Message)
            .join(Thread)
            .join(Channel)
            .filter(Channel.platform == 'imessage')
            .order_by(Message.sent_at.desc())
            .limit(1)
            .one_or_none()
        )

        if not last_message or not last_message.sent_at:
            return None

        sent_at = last_message.sent_at
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)

        extra = last_message.extra or {}

        return {
            'sent_at': sent_at,
            'timestamp': sent_at.timestamp(),
            'rowid': extra.get('rowid'),
            'message_guid': last_message.message_id,
        }

    def run_incremental_import(
        self,
        db_path: Optional[str] = None,
        last_sync_timestamp: Optional[float] = None,
        limit: Optional[int] = None,
        known_contacts_only: bool = False,
        rewind_seconds: Optional[int] = None,
    ):
        """
        Run incremental import of iMessages with identity resolution.
        
        Args:
            db_path: Path to iMessage database (None for default)
            last_sync_timestamp: Unix timestamp of last sync (None for all messages)
            limit: Maximum number of messages to import (None for all)
            known_contacts_only: If True, only import messages from existing contacts
            
        Returns:
            Import statistics dictionary
        """
        from memory_database.models import Message, Channel, Thread, PersonMessage, MessageAttachment
        from sqlalchemy import and_
        
        source = iMessageIngestionSource(self.db_manager, db_path)
        source.connect()

        if rewind_seconds is None:
            rewind_seconds = self.DEFAULT_REWIND_SECONDS

        stats = {
            'total_processed': 0,
            'new_messages': 0,
            'skipped_messages': 0,
            'skipped_unknown_contacts': 0,
            'new_principals': 0,
            'linked_principals': 0,
            'new_identities': 0,
            'attachments_stored': 0,
            'attachments_failed': 0,
            'candidate_messages': 0,
            'auto_detected_last_sync': None,
            'auto_detected_last_rowid': None,
            'last_sync_timestamp_used': None,
            'query_start_timestamp': None,
            'ingest_rewind_seconds': rewind_seconds,
            'processed_through_timestamp': None,
        }

        auto_state = None
        if last_sync_timestamp is None:
            # Determine the latest message previously ingested so we can resume from there.
            with self.db_manager.get_session() as session:
                auto_state = self._infer_last_ingested_state(session)

            if auto_state:
                last_sync_timestamp = auto_state['timestamp']
                stats['auto_detected_last_sync'] = auto_state['timestamp']
                stats['auto_detected_last_rowid'] = auto_state['rowid']
                self.logger.info(
                    "Detected previous iMessage import checkpoint",
                    sent_at=auto_state['sent_at'].isoformat(),
                    rowid=auto_state['rowid'],
                    guid=auto_state['message_guid'],
                )
            else:
                self.logger.info("No existing iMessage messages found; performing full import")

        effective_start = 0.0
        if last_sync_timestamp is not None:
            rewind_seconds = max(rewind_seconds or 0, 0)
            effective_start = max(0.0, last_sync_timestamp - float(rewind_seconds))
        else:
            rewind_seconds = max(rewind_seconds or 0, 0)

        stats['last_sync_timestamp_used'] = last_sync_timestamp
        stats['query_start_timestamp'] = effective_start

        self.logger.info(
            "Starting iMessage incremental import",
            requested_last_sync=last_sync_timestamp,
            query_start=effective_start,
            rewind_seconds=rewind_seconds,
            limit=limit,
            known_contacts_only=known_contacts_only,
        )

        messages = source.imessage_db.query_messages_after(effective_start, limit)
        stats['candidate_messages'] = len(messages)

        if not messages:
            self.logger.info("No new iMessage records detected", query_start=effective_start)
            return stats

        latest_processed = None

        with self.db_manager.get_session() as session:
            # Process each message with progress bar
            message_iter = tqdm(
                messages, 
                desc="Processing iMessages", 
                unit="messages"
            )
            
            for raw_msg in message_iter:
                try:
                    stats['total_processed'] += 1
                    
                    # Convert to raw message format expected by source
                    raw_message = {
                        'rowid': raw_msg.rowid,
                        'guid': raw_msg.guid,
                        'text': raw_msg.text,
                        'service': raw_msg.service,
                        'handle_id': raw_msg.handle_id,
                        'sender_handle': source.handle_cache.get(raw_msg.handle_id) if raw_msg.handle_id else None,
                        'subject': raw_msg.subject,
                        'date': raw_msg.date,
                        'date_read': raw_msg.date_read,
                        'date_delivered': raw_msg.date_delivered,
                        'is_from_me': raw_msg.is_from_me,
                        'is_read': raw_msg.is_read,
                        'is_sent': raw_msg.is_sent,
                        'is_delivered': raw_msg.is_delivered,
                        'cache_roomnames': raw_msg.cache_roomnames,
                        'group_title': raw_msg.group_title,
                        'associated_message_guid': raw_msg.associated_message_guid,
                        'associated_message_type': raw_msg.associated_message_type,
                        'thread_originator_guid': raw_msg.thread_originator_guid,
                        'participants': [],
                        'attachments': []
                    }
                    
                    # Get participants and attachments
                    try:
                        raw_message['participants'] = [
                            {
                                'rowid': p.rowid,
                                'id': p.id,
                                'service': p.service,
                                'uncanonicalized_id': p.uncanonicalized_id
                            }
                            for p in source.imessage_db.get_message_participants(raw_msg.rowid)
                        ]
                        
                        raw_message['attachments'] = [
                            {
                                'rowid': a.rowid,
                                'guid': a.guid,
                                'filename': a.filename,
                                'mime_type': a.mime_type,
                                'transfer_name': a.transfer_name,
                                'total_bytes': a.total_bytes
                            }
                            for a in source.imessage_db.get_message_attachments(raw_msg.rowid)
                        ]
                    except Exception as e:
                        self.logger.warning("Failed to get participants/attachments",
                                          message_id=raw_msg.guid, error=str(e))
                    
                    # Normalize the message
                    normalized = source.normalize_message(raw_message)

                    latest_processed = normalized['sent_at']

                    # Check if message already exists
                    existing_message = session.query(Message).filter_by(
                        message_id=normalized['message_id']
                    ).first()
                    
                    if existing_message:
                        stats['skipped_messages'] += 1
                        continue
                    
                    # Extract identities and resolve principals
                    identities = source.extract_identities(normalized)
                    identity_principals = {}
                    has_known_contact = False
                    
                    for identity in identities:
                        # If filtering for known contacts only, check if principal exists
                        if known_contacts_only:
                            # Only look for existing principals, don't create new ones
                            existing_principal = find_existing_principal(
                                session,
                                [identity],
                                platforms=['contacts']  # Only look in contacts platform
                            )
                            
                            if existing_principal:
                                identity_principals[identity['value']] = existing_principal.id
                                has_known_contact = True
                                stats['linked_principals'] += 1
                            else:
                                # Principal not found in contacts
                                continue
                        else:
                            # Normal behavior: create principal if not exists
                            principal, is_new = link_or_create_principal(
                                session,
                                [identity],
                                display_name=identity['value'],
                                platforms=['contacts', 'imessage'],
                                extra={'source': 'imessage'}
                            )
                            
                            identity_principals[identity['value']] = principal.id
                            
                            if is_new:
                                stats['new_principals'] += 1
                                stats['new_identities'] += 1
                            else:
                                stats['linked_principals'] += 1
                                has_known_contact = True
                    
                    # Skip message if filtering for known contacts and none found
                    if known_contacts_only and not has_known_contact:
                        stats['skipped_unknown_contacts'] += 1
                        continue
                    
                    # Get or create channel
                    channel = session.query(Channel).filter_by(
                        platform='imessage',
                        channel_id='imessage_default'
                    ).first()
                    
                    if not channel:
                        channel = Channel(
                            platform='imessage',
                            name='iMessage',
                            channel_id='imessage_default',
                            extra={}
                        )
                        session.add(channel)
                        session.flush()
                    
                    # Get or create thread
                    thread_id = normalized.get('thread_id', 'default')
                    thread = session.query(Thread).filter_by(
                        channel_id=channel.id,
                        thread_id=thread_id
                    ).first()
                    
                    if not thread:
                        thread = Thread(
                            channel_id=channel.id,
                            subject=normalized.get('subject'),
                            started_at=normalized['sent_at'],
                            last_at=normalized['sent_at'],
                            thread_id=thread_id,
                            extra={'group_title': normalized.get('channel_name')}
                        )
                        session.add(thread)
                        session.flush()
                    else:
                        # Update last_at if newer
                        if normalized['sent_at'] > thread.last_at:
                            thread.last_at = normalized['sent_at']
                    
                    # Create the message
                    message = Message(
                        thread_id=thread.id,
                        sent_at=normalized['sent_at'],
                        content=normalized.get('content', ''),
                        content_type=normalized.get('content_type', 'text/plain'),
                        message_id=normalized['message_id'],
                        reply_to=None,  # Will handle reply relationships later
                        extra=normalized.get('extra', {})
                    )
                    session.add(message)
                    session.flush()
                    
                    # Process attachments if any
                    if raw_message.get('attachments'):
                        for idx, att in enumerate(raw_message['attachments']):
                            try:
                                # Resolve attachment file path
                                attachment_path = source.resolve_attachment_path(
                                    att['guid'], 
                                    att.get('filename') or att.get('transfer_name')
                                )
                                
                                if attachment_path and attachment_path.exists():
                                    # Store the attachment
                                    attachment_data = source.attachment_manager.store_attachment(
                                        source_path=attachment_path,
                                        message_id=message.id,
                                        sent_at=normalized['sent_at'],
                                        attachment_index=idx
                                    )
                                    
                                    # Create database record
                                    db_attachment = MessageAttachment(
                                        id=attachment_data['id'],
                                        message_id=message.id,
                                        original_path=attachment_data['original_path'],
                                        stored_path=attachment_data['stored_path'],
                                        filename=attachment_data['filename'],
                                        file_size=attachment_data['file_size'],
                                        mime_type=attachment_data['mime_type'],
                                        width=attachment_data['width'],
                                        height=attachment_data['height'],
                                        duration=attachment_data['duration'],
                                        imessage_guid=att['guid'],
                                        imessage_rowid=att.get('rowid'),
                                        attachment_index=idx,
                                        storage_method=attachment_data['storage_method'],
                                        extra_metadata={
                                            'transfer_name': att.get('transfer_name'),
                                            'total_bytes': att.get('total_bytes')
                                        }
                                    )
                                    session.add(db_attachment)
                                    
                                    self.logger.info(
                                        "Stored attachment",
                                        message_id=message.id,
                                        filename=attachment_data['filename'],
                                        method=attachment_data['storage_method']
                                    )
                                    stats['attachments_stored'] += 1
                                else:
                                    self.logger.warning(
                                        "Attachment file not found",
                                        guid=att['guid'],
                                        filename=att.get('filename')
                                    )
                                    stats['attachments_failed'] += 1
                            except Exception as e:
                                self.logger.error(
                                    "Failed to process attachment",
                                    guid=att['guid'],
                                    error=str(e),
                                    exc_info=True
                                )
                                stats['attachments_failed'] += 1
                    
                    # Create person-message links
                    if sender := normalized.get('sender'):
                        if sender != 'me@imessage' and sender in identity_principals:
                            person_message = PersonMessage(
                                principal_id=identity_principals[sender],
                                message_id=message.id,
                                role='sender',
                                confidence=1.0
                            )
                            session.add(person_message)
                    
                    for recipient in normalized.get('recipients', []):
                        if recipient != 'me@imessage' and recipient in identity_principals:
                            person_message = PersonMessage(
                                principal_id=identity_principals[recipient],
                                message_id=message.id,
                                role='recipient',
                                confidence=1.0
                            )
                            session.add(person_message)
                    
                    stats['new_messages'] += 1
                    
                    # Commit periodically
                    if stats['total_processed'] % 100 == 0:
                        session.commit()
                
                except Exception as e:
                    self.logger.error("Failed to process message",
                                    guid=raw_msg.guid,
                                    error=str(e),
                                    exc_info=True)
                    continue
            
            # Final commit
            session.commit()

        if latest_processed:
            if latest_processed.tzinfo is None:
                latest_processed = latest_processed.replace(tzinfo=timezone.utc)
            stats['processed_through_timestamp'] = latest_processed.timestamp()

        self.logger.info("iMessage import completed", **stats)
        return stats
