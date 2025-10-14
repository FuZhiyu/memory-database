from datetime import datetime
from typing import List, Optional

from sqlalchemy import Column, String, DateTime, Text, Integer, ForeignKey, Float
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB

from memory_database.database.connection import Base
from memory_database.utils.ulid import generate_ulid


class MediaAsset(Base):
    __tablename__ = "media_asset"
    
    id = Column(String, primary_key=True, default=generate_ulid)
    source = Column(Text, nullable=False)     # 'photos'|'scans'|'screenshots'|'videos'
    uri = Column(Text, nullable=False)        # file path or s3://
    captured_at = Column(DateTime)
    sha256 = Column(Text)
    width = Column(Integer)
    height = Column(Integer)
    exif = Column(JSONB, default=dict)        # EXIF/IPTC/XMP
    ocr_text = Column(Text)
    transcript = Column(Text)
    extra = Column(JSONB, default=dict)
    
    # Relationships
    person_links = relationship("PersonMedia", back_populates="media")


class PersonMedia(Base):
    __tablename__ = "person_media"
    
    principal_id = Column(String, ForeignKey("principal.id", ondelete="CASCADE"), primary_key=True)
    media_id = Column(String, ForeignKey("media_asset.id", ondelete="CASCADE"), primary_key=True)
    evidence = Column(JSONB, default=dict)    # face box hashes, EXIF person tag, filename hint
    confidence = Column(Float, default=0.7)
    
    # Relationships
    principal = relationship("Principal", back_populates="media_links")
    media = relationship("MediaAsset", back_populates="person_links")


class DocumentAsset(Base):
    __tablename__ = "document_asset"
    
    id = Column(String, primary_key=True, default=generate_ulid)
    uri = Column(Text, nullable=False)
    title = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    text = Column(Text)
    extra = Column(JSONB, default=dict)
    
    # Relationships
    person_links = relationship("PersonDocument", back_populates="document")


class PersonDocument(Base):
    __tablename__ = "person_document"
    
    principal_id = Column(String, ForeignKey("principal.id", ondelete="CASCADE"), primary_key=True)
    document_id = Column(String, ForeignKey("document_asset.id", ondelete="CASCADE"), primary_key=True)
    role = Column(Text, nullable=False, primary_key=True)  # 'author'|'mentioned'|'recipient'
    confidence = Column(Float, default=0.8)
    
    # Relationships
    principal = relationship("Principal", back_populates="document_links")
    document = relationship("DocumentAsset", back_populates="person_links")