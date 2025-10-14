from datetime import datetime
from typing import List, Optional

from sqlalchemy import Column, String, DateTime, Text, ARRAY, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB

from memory_database.database.connection import Base
from memory_database.utils.ulid import generate_ulid


class Chunk(Base):
    __tablename__ = "chunk"
    
    id = Column(String, primary_key=True, default=generate_ulid)
    source_type = Column(Text, nullable=False)    # 'message'|'document'|'media'
    source_id = Column(String, nullable=False)    # Points to message/document/media id
    content = Column(Text, nullable=False)
    # embedding = Column(Vector(1536))              # TODO: Add when implementing vector search
    created_at = Column(DateTime, default=datetime.utcnow)
    participants = Column(ARRAY(String), default=list)  # Array of principal_ids
    chunk_metadata = Column(JSONB, default=dict)
    
    # Note: Relationships to messages/documents/media are handled manually
    # based on source_type and source_id to avoid complex foreign key constraints