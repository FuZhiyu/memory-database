"""
Data ingestion base classes and pipeline orchestration.

IMPORTANT: Identity Claim Uniqueness Constraint
================================================
The database enforces: UNIQUE (principal_id, platform, normalized)

When ingesting identity claims, ensure:
1. Use the correct platform name for this source (e.g., 'contacts', 'imessage', 'email')
2. Check for existing (principal_id, platform, normalized) before creating claims
3. Update last_seen timestamp if claim already exists
4. Same identity from different platforms creates SEPARATE claims (this is intentional)

See _process_identity_claim() for reference implementation.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Iterator, Tuple
from datetime import datetime, timezone
import structlog
from tqdm import tqdm

from memory_database.database.connection import DatabaseManager
from memory_database.models import IdentityClaim, Channel, Thread, Message, PersonMessage
from memory_database.utils.identity_resolver import link_or_create_principal
from memory_database.utils.normalization import normalize_identity_value, extract_identity_kind


logger = structlog.get_logger()


class IngestionSource(ABC):
    """Base class for all data ingestion sources."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self.logger = logger.bind(source=self.__class__.__name__)
    
    @abstractmethod
    def get_platform_name(self) -> str:
        """Return the platform name (e.g., 'email', 'slack', 'imessage')."""
        pass
    
    @abstractmethod
    def extract_raw_data(self, source_path: str) -> Iterator[Dict[str, Any]]:
        """Extract raw data from the source and yield normalized records."""
        pass
    
    def count_items(self, source_path: str) -> Optional[int]:
        """
        Count total items that will be processed.
        
        Returns None if count cannot be determined efficiently.
        Override this method in subclasses where total count is available.
        """
        return None
    
    @abstractmethod
    def normalize_message(self, raw_message: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a raw message to standard format."""
        pass
    
    def extract_identities(self, message: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract identity claims from a message."""
        identities = []
        
        # Extract sender
        if sender := message.get('sender'):
            identities.append({
                'platform': self.get_platform_name(),
                'kind': self._get_identity_kind(sender),
                'value': sender,
                'normalized': self._normalize_identity_value(sender),
                'confidence': 1.0
            })
        
        # Extract recipients
        for recipient in message.get('recipients', []):
            identities.append({
                'platform': self.get_platform_name(),
                'kind': self._get_identity_kind(recipient),
                'value': recipient,
                'normalized': self._normalize_identity_value(recipient),
                'confidence': 1.0
            })
        
        return identities
    
    def _get_identity_kind(self, value: str) -> str:
        """Determine the kind of identity claim based on the value."""
        return extract_identity_kind(value)
    
    def _normalize_identity_value(self, value: str) -> str:
        """Normalize an identity value for deduplication."""
        kind = self._get_identity_kind(value)
        return normalize_identity_value(value, kind)


class IngestionPipeline:
    """Orchestrates the ingestion process across multiple sources."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self.logger = logger.bind(component='pipeline')
        self.sources: List[IngestionSource] = []
    
    def add_source(self, source: IngestionSource):
        """Add an ingestion source to the pipeline."""
        self.sources.append(source)
        self.logger.info("Added ingestion source", source=source.get_platform_name())
    
    def run_ingestion(self, source_paths: Dict[str, str]):
        """Run the full ingestion pipeline.
        
        Args:
            source_paths: Dict mapping platform names to their data paths
        """
        self.logger.info("Starting ingestion pipeline", sources=len(self.sources))
        
        for source in self.sources:
            platform = source.get_platform_name()
            if platform not in source_paths:
                self.logger.warning("No data path provided for source", platform=platform)
                continue
            
            try:
                self._ingest_from_source(source, source_paths[platform])
            except Exception as e:
                self.logger.error("Failed to ingest from source", 
                                platform=platform, error=str(e), exc_info=True)
        
        self.logger.info("Ingestion pipeline completed")
    
    def _ingest_from_source(self, source: IngestionSource, source_path: str):
        """Ingest data from a single source."""
        platform = source.get_platform_name()
        self.logger.info("Starting ingestion", platform=platform, path=source_path)
        
        message_count = 0
        identity_count = 0
        
        # Try to get total count for progress bar
        total_count = source.count_items(source_path)
        
        with self.db_manager.get_session() as session:
            # Create progress bar if we know the total
            raw_data_iter = source.extract_raw_data(source_path)
            if total_count:
                raw_data_iter = tqdm(
                    raw_data_iter,
                    total=total_count,
                    desc=f"Processing {platform}",
                    unit="items"
                )
            
            for raw_message in raw_data_iter:
                try:
                    # Normalize the message
                    normalized = source.normalize_message(raw_message)
                    
                    # Extract and process identity claims
                    identities = source.extract_identities(normalized)
                    identity_principals = {}
                    
                    for identity_data in identities:
                        identity_claim = self._process_identity_claim(session, identity_data)
                        if not identity_claim:
                            continue
                        identity_principals[identity_data['value']] = identity_claim.principal_id
                        identity_count += 1
                    
                    # Store the actual message
                    self._store_message(session, source, normalized, identity_principals)
                    message_count += 1
                    
                    if message_count % 100 == 0:
                        session.commit()
                        if not total_count:  # Only log if not using progress bar
                            self.logger.info("Progress update", 
                                           platform=platform, 
                                           messages=message_count,
                                           identities=identity_count)
                
                except Exception as e:
                    self.logger.error("Failed to process message", 
                                    platform=platform, error=str(e))
                    continue
        
        self.logger.info("Completed ingestion", 
                        platform=platform,
                        total_messages=message_count,
                        total_identities=identity_count)
    
    def _process_identity_claim(self, session, identity_data: Dict[str, Any]):
        """Resolve an identity claim using the shared identity resolver.

        This implementation defers principal selection to
        ``identity_resolver.link_or_create_principal`` so we respect the
        database constraint ``UNIQUE (principal_id, platform, normalized)``
        while still allowing the same normalized value to appear on multiple
        principals across different platforms (shared inboxes, reassigned
        phone numbers, etc.).

        The resolver may return either an existing match or a brand new
        principal. After resolving we ensure a platform-scoped
        ``IdentityClaim`` exists for the principal and update observation
        metadata.
        """
        # Work on a defensive copy so callers keep their original structure
        identity = dict(identity_data)

        value = identity.get('value')
        if not value:
            self.logger.warning("Skipping identity without value", platform=identity.get('platform'))
            return None

        # Ensure kind/platform/normalized fields are populated for resolver
        platform = identity.get('platform') or 'unknown'
        kind = identity.get('kind') or self._get_identity_kind(value)
        normalized = identity.get('normalized') or normalize_identity_value(value, kind)

        identity.update({
            'platform': platform,
            'kind': kind,
            'normalized': normalized
        })

        # Resolve or create the principal via the shared identity resolver logic
        principal, _created = link_or_create_principal(
            session,
            [identity],
            display_name=identity.get('display_name') or value,
            platforms=None,  # Allow cross-platform matches (contacts ↔ ingestion source)
            extra={'source': platform}
        )

        # Make sure there is an identity claim for this (principal, platform, normalized)
        claim = session.query(IdentityClaim).filter_by(
            principal_id=principal.id,
            platform=platform,
            normalized=normalized
        ).first()

        now = datetime.now(timezone.utc)

        if claim:
            # Update observation metadata without clobbering richer values
            claim.value = value or claim.value
            claim.kind = kind or claim.kind
            incoming_confidence = identity.get('confidence')
            if incoming_confidence is not None:
                claim.confidence = max(claim.confidence or 0.0, incoming_confidence)
            claim.last_seen = now
            if identity.get('extra'):
                claim.extra = {**(claim.extra or {}), **identity['extra']}
            return claim

        # Otherwise create a new claim scoped to this principal/platform pair
        claim = IdentityClaim(
            principal_id=principal.id,
            platform=platform,
            kind=kind,
            value=value,
            normalized=normalized,
            confidence=identity.get('confidence', 1.0),
            first_seen=now,
            last_seen=now,
            extra=identity.get('extra', {})
        )
        session.add(claim)
        session.flush()

        return claim
    
    def _store_message(self, session, source: IngestionSource, message_data: Dict[str, Any], identity_principals: Dict[str, str]):
        """Store a message in the database with proper channel/thread structure."""
        platform = source.get_platform_name()
        
        # Get or create channel
        channel = self._get_or_create_channel(session, platform, message_data)
        
        # Get or create thread
        thread = self._get_or_create_thread(session, channel, message_data)
        
        # Check if message already exists (deduplication)
        existing_message = None
        if message_id := message_data.get('message_id'):
            existing_message = session.query(Message).filter_by(
                thread_id=thread.id,
                message_id=message_id
            ).first()
        
        if existing_message:
            self.logger.debug("Message already exists, skipping", message_id=message_id)
            return existing_message
        
        # Create the message
        message = Message(
            thread_id=thread.id,
            sent_at=message_data.get('sent_at') or datetime.now(timezone.utc),
            content=message_data.get('content', ''),
            content_type=message_data.get('content_type', 'text/plain'),
            message_id=message_data.get('message_id'),
            extra=message_data.get('extra', {})
        )
        
        # Handle reply_to relationship
        if reply_to := message_data.get('reply_to'):
            reply_message = session.query(Message).filter_by(
                thread_id=thread.id,
                message_id=reply_to
            ).first()
            if reply_message:
                message.reply_to = reply_message.id
        
        session.add(message)
        session.flush()  # Get the message ID
        
        # Create person-message links
        self._create_person_message_links(session, message, message_data, identity_principals)
        
        return message
    
    def _get_or_create_channel(self, session, platform: str, message_data: Dict[str, Any]) -> Channel:
        """Get or create a channel for the platform."""
        # For email, we typically have one channel per platform
        # For other platforms like Slack, we might have multiple channels
        channel_id = message_data.get('channel_id') or f"{platform}_default"
        
        channel = session.query(Channel).filter_by(
            platform=platform,
            channel_id=channel_id
        ).first()
        
        if not channel:
            channel = Channel(
                platform=platform,
                name=message_data.get('channel_name') or f"{platform.title()} Messages",
                channel_id=channel_id,
                extra={}
            )
            session.add(channel)
            session.flush()
        
        return channel
    
    def _get_or_create_thread(self, session, channel: Channel, message_data: Dict[str, Any]) -> Thread:
        """Get or create a thread within the channel."""
        # For email, threads are typically based on subject or message threading
        subject = message_data.get('subject', '').strip()
        thread_key = message_data.get('thread_id') or subject or 'no_subject'
        
        thread = session.query(Thread).filter_by(
            channel_id=channel.id,
            thread_id=thread_key
        ).first()
        
        if not thread:
            thread = Thread(
                channel_id=channel.id,
                subject=subject if subject else None,
                started_at=message_data.get('sent_at') or datetime.now(timezone.utc),
                last_at=message_data.get('sent_at') or datetime.now(timezone.utc),
                thread_id=thread_key,
                extra={}
            )
            session.add(thread)
            session.flush()
        else:
            # Update last_at if this message is newer
            message_time = message_data.get('sent_at') or datetime.now(timezone.utc)
            if not thread.last_at or message_time > thread.last_at:
                thread.last_at = message_time
        
        return thread
    
    def _create_person_message_links(self, session, message: Message, message_data: Dict[str, Any], identity_principals: Dict[str, str]):
        """Create person-message relationship links."""
        # Link sender
        if sender := message_data.get('sender'):
            if principal_id := identity_principals.get(sender):
                person_message = PersonMessage(
                    principal_id=principal_id,
                    message_id=message.id,
                    role='sender',
                    confidence=1.0
                )
                session.add(person_message)
        
        # Link recipients
        for recipient in message_data.get('recipients', []):
            if principal_id := identity_principals.get(recipient):
                person_message = PersonMessage(
                    principal_id=principal_id,
                    message_id=message.id,
                    role='recipient',
                    confidence=1.0
                )
                session.add(person_message)
