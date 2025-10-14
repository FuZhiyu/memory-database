from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, String, DateTime, Text, Integer, ForeignKey, Float, Boolean, BigInteger
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB

from memory_database.database.connection import Base
from memory_database.utils.ulid import generate_ulid


class MessageAttachment(Base):
    __tablename__ = "message_attachment"
    
    id = Column(String, primary_key=True, default=generate_ulid)
    message_id = Column(String, ForeignKey("message.id", ondelete="CASCADE"), nullable=False)
    
    # File locations
    original_path = Column(Text, nullable=False)     # Original iMessage path
    stored_path = Column(Text, nullable=False)       # Our clone at ~/Memories/attachments/...
    filename = Column(Text, nullable=False)
    
    # Basic metadata
    file_size = Column(BigInteger)
    mime_type = Column(Text)
    width = Column(Integer)                          # For images/videos
    height = Column(Integer)
    duration = Column(Float)                         # For videos/audio in seconds
    
    # iMessage reference
    imessage_guid = Column(Text, nullable=False)
    imessage_rowid = Column(Integer)
    attachment_index = Column(Integer, nullable=False)  # Order in message
    
    # Status
    storage_method = Column(Text, nullable=False, default='clone')  # 'clone'|'hardlink'|'copy'
    is_accessible = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Extra metadata
    extra_metadata = Column(JSONB, default=dict)
    
    # Relationships
    message = relationship("Message", back_populates="attachments")