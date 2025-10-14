from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, ARRAY, Float
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB

from memory_database.database.connection import Base
from memory_database.utils.ulid import generate_ulid


class Channel(Base):
    __tablename__ = "channel"
    
    id = Column(String, primary_key=True, default=generate_ulid)
    platform = Column(Text, nullable=False)  # 'email'|'slack'|'imessage'|etc
    name = Column(Text)
    channel_id = Column(Text)                 # Platform-specific ID
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    extra = Column(JSONB, default=dict)
    
    # Relationships
    threads = relationship("Thread", back_populates="channel")


class Thread(Base):
    __tablename__ = "thread"
    
    id = Column(String, primary_key=True, default=generate_ulid)
    channel_id = Column(String, ForeignKey("channel.id", ondelete="CASCADE"))
    subject = Column(Text)
    started_at = Column(DateTime(timezone=True))
    last_at = Column(DateTime(timezone=True))
    thread_id = Column(Text)                  # Platform-specific thread ID
    extra = Column(JSONB, default=dict)
    
    # Relationships
    channel = relationship("Channel", back_populates="threads")
    messages = relationship("Message", back_populates="thread")


class Message(Base):
    __tablename__ = "message"
    
    id = Column(String, primary_key=True, default=generate_ulid)
    thread_id = Column(String, ForeignKey("thread.id", ondelete="CASCADE"), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=False)
    content = Column(Text)
    content_type = Column(Text, default="text/plain")
    message_id = Column(Text)                 # Platform-specific message ID
    reply_to = Column(String, ForeignKey("message.id"))
    extra = Column(JSONB, default=dict)
    
    # Relationships
    thread = relationship("Thread", back_populates="messages")
    replies = relationship("Message", remote_side=[id])
    person_links = relationship("PersonMessage", back_populates="message")
    attachments = relationship("MessageAttachment", back_populates="message")


class PersonMessage(Base):
    __tablename__ = "person_message"
    
    principal_id = Column(String, ForeignKey("principal.id", ondelete="CASCADE"), primary_key=True)
    message_id = Column(String, ForeignKey("message.id", ondelete="CASCADE"), primary_key=True)
    role = Column(Text, nullable=False, primary_key=True)  # 'sender'|'recipient'|'mentioned'|'quoted'
    confidence = Column(Float, default=1.0)
    
    # Relationships
    principal = relationship("Principal", back_populates="message_links")
    message = relationship("Message", back_populates="person_links")