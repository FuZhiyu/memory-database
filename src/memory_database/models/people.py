from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Column, String, DateTime, Text, ARRAY, JSON, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB

from memory_database.database.connection import Base
from memory_database.utils.ulid import generate_ulid


class Principal(Base):
    __tablename__ = "principal"
    
    id = Column(String, primary_key=True, default=generate_ulid)
    display_name = Column(Text)
    org = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    merged_from = Column(ARRAY(String), default=list)
    extra = Column(JSONB, default=dict)
    
    # Relationships
    identity_claims = relationship("IdentityClaim", back_populates="principal")
    message_links = relationship("PersonMessage", back_populates="principal")
    media_links = relationship("PersonMedia", back_populates="principal")
    document_links = relationship("PersonDocument", back_populates="principal")
    events = relationship("PersonEvent", back_populates="principal")
    relationships_a = relationship("Relationship", foreign_keys="Relationship.a_id", back_populates="person_a")
    relationships_b = relationship("Relationship", foreign_keys="Relationship.b_id", back_populates="person_b")


class IdentityClaim(Base):
    """
    Represents an identity claim for a person (Principal).

    Identity claims link platform-specific identifiers to people, enabling
    identity resolution and provenance tracking.

    IMPORTANT - Uniqueness Constraint:
    =====================================
    Database constraint: UNIQUE (principal_id, platform, normalized)

    This means:
    - Each person can have ONE claim per (platform, normalized_value) combination
    - Same email from DIFFERENT platforms = separate claims (preserves provenance)
    - Same email TWICE on same platform = DATABASE ERROR (constraint violation)

    Examples:
    ✅ ALLOWED:
       Person A: {platform='contacts', normalized='john@ex.com'}
       Person A: {platform='imessage', normalized='john@ex.com'}  # Different platform
       Person A: {platform='contacts', normalized='john@work.com'} # Different email

    ❌ BLOCKED BY DATABASE:
       Person A: {platform='contacts', normalized='john@ex.com'}
       Person A: {platform='contacts', normalized='john@ex.com'}  # DUPLICATE!

    When writing code that creates/updates IdentityClaim records:
    1. ALWAYS check for existing (principal_id, platform, normalized) before INSERT
    2. Use ON CONFLICT or application-level duplicate checking
    3. For updates, ensure final (platform, normalized) doesn't collide with existing
    """
    __tablename__ = "identity_claim"

    id = Column(String, primary_key=True, default=generate_ulid)
    principal_id = Column(String, ForeignKey("principal.id", ondelete="CASCADE"), nullable=False)
    platform = Column(Text, nullable=False)  # Source: 'contacts'|'imessage'|'email'|'manual'|'life.md'
    kind = Column(Text, nullable=False)       # Type: 'email'|'phone'|'display_name'|'username'|'alias'|'memory_url'
    value = Column(Text, nullable=False)      # Original value
    normalized = Column(Text)                 # Cleaned/normalized value (used in uniqueness constraint)
    confidence = Column(Float, default=0.9)   # Confidence score (0.0-1.0)
    first_seen = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    extra = Column(JSONB, default=dict)       # Platform-specific metadata

    # CRITICAL: Database-level uniqueness constraint
    # DO NOT modify this without creating a migration and updating all write operations
    __table_args__ = (
        UniqueConstraint('principal_id', 'platform', 'normalized',
                        name='uq_identity_per_platform'),
    )

    # Relationships
    principal = relationship("Principal", back_populates="identity_claims")


class ResolutionEvent(Base):
    __tablename__ = "resolution_event"
    
    id = Column(String, primary_key=True, default=generate_ulid)
    happened_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    actor = Column(Text)                     # 'system' or 'user:<id>'
    action = Column(Text, nullable=False)    # 'merge'|'split'|'block'
    from_principal = Column(String)
    to_principal = Column(String)
    reason = Column(Text)
    score_snapshot = Column(JSONB, default=dict)


class Relationship(Base):
    __tablename__ = "relationship"
    
    id = Column(String, primary_key=True, default=generate_ulid)
    a_id = Column(String, ForeignKey("principal.id", ondelete="CASCADE"), nullable=False)
    b_id = Column(String, ForeignKey("principal.id", ondelete="CASCADE"), nullable=False)
    kind = Column(Text)                      # 'colleague'|'family'|'advisor'|etc
    confidence = Column(Float, default=0.6)
    since = Column(DateTime(timezone=True))
    until = Column(DateTime(timezone=True))
    extra = Column(JSONB, default=dict)
    
    # Relationships
    person_a = relationship("Principal", foreign_keys=[a_id], back_populates="relationships_a")
    person_b = relationship("Principal", foreign_keys=[b_id], back_populates="relationships_b")


class PersonEvent(Base):
    __tablename__ = "person_event"
    
    id = Column(String, primary_key=True, default=generate_ulid)
    principal_id = Column(String, ForeignKey("principal.id", ondelete="CASCADE"), nullable=False)
    happened_at = Column(DateTime(timezone=True), nullable=False)
    kind = Column(Text)                      # 'meeting'|'trip'|'deadline'|etc
    summary = Column(Text)
    source_ref = Column(JSONB, default=dict) # pointers to messages/media/docs
    extra = Column(JSONB, default=dict)
    
    # Relationships
    principal = relationship("Principal", back_populates="events")