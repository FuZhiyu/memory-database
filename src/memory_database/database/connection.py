import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.declarative import declarative_base
from datetime import timezone
from pydantic_settings import BaseSettings
from pydantic import computed_field


class DatabaseSettings(BaseSettings):
    postgres_host: str
    postgres_port: int = 5432
    postgres_db: str
    postgres_user: str
    postgres_password: str = ""
    log_level: str = "INFO"
    
    @computed_field  # Use computed_field instead of property for Pydantic v2
    @property
    def database_url(self) -> str:
        """Construct database URL from components."""
        if self.postgres_password:
            return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        else:
            return f"postgresql://{self.postgres_user}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    def redacted_database_url(self) -> str:
        """Return a masked version of the database URL without exposing credentials."""
        try:
            url = make_url(self.database_url)
        except Exception:
            return "postgresql://***@***"

        if url.password:
            url = url.set(password="***")

        return str(url)
    
    class Config:
        env_file = ".env"


Base = declarative_base()


class DatabaseManager:
    def __init__(self, settings: DatabaseSettings = None):
        self.settings = settings or DatabaseSettings()
        self.engine = create_engine(
            self.settings.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600
        )
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )
    
    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """Get a database session with automatic cleanup."""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    def create_tables(self):
        """Create all tables in the database."""
        Base.metadata.create_all(bind=self.engine)
    
    def drop_tables(self):
        """Drop all tables in the database."""
        Base.metadata.drop_all(bind=self.engine)
