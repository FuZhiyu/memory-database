"""
Tests using real (anonymized) iMessage sample database.
This is the primary test suite for iMessage ingestion.
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import shutil
import tempfile
import sqlite3
import os

from src.ingestion.imessage import (
    iMessageIngestionSource,
    iMessageIncrementalPipeline,
)

# Import iMessageDB conditionally
try:
    import imessage_bridge
    iMessageDB = imessage_bridge.IMessageDB
except ImportError:
    iMessageDB = None
from src.database.connection import DatabaseManager, DatabaseSettings
from src.models import Principal, IdentityClaim, Message, Channel, Thread, MessageAttachment, PersonMessage
from tests.mocks.mock_attachment_manager import MockAttachmentManager


class TestiMessageWithRealSample:
    """Tests using real anonymized sample from iMessage database."""
    
    @pytest.fixture(scope="class")
    def sample_db_path(self):
        """Path to the sample database."""
        sample_path = Path("tests/fixtures/test_imessage_sample.db")
        if not sample_path.exists():
            pytest.skip("Sample database not found. Run extract_imessage_sample.py first.")
        return sample_path
    
    @pytest.fixture
    def test_db_copy(self, sample_db_path):
        """Create a temporary copy of the sample database for each test."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
            tmp_path = Path(tmp.name)
            shutil.copy2(sample_db_path, tmp_path)
            yield tmp_path
            # Cleanup
            tmp_path.unlink()
    
    @pytest.fixture
    def test_db_manager(self):
        """Create a test database manager using test PostgreSQL database."""
        from sqlalchemy import text
        
        # Use a test PostgreSQL database
        settings = DatabaseSettings()
        # Override the database name for testing
        original_db = settings.postgres_db
        settings.postgres_db = 'test_memories_rag'
        
        db_manager = DatabaseManager(settings)
        
        # Clean and recreate schema
        with db_manager.get_session() as session:
            # Drop all tables
            session.execute(text("DROP SCHEMA public CASCADE"))
            session.execute(text("CREATE SCHEMA public"))
            session.commit()
        
        # Create tables
        from src.database.connection import Base
        from sqlalchemy import create_engine
        engine = create_engine(settings.database_url)
        Base.metadata.create_all(engine)
        
        yield db_manager
        
        # Cleanup: drop test tables
        with db_manager.get_session() as session:
            session.execute(text("DROP SCHEMA public CASCADE"))
            session.execute(text("CREATE SCHEMA public"))
            session.commit()
    
    @pytest.fixture
    def mock_attachment_manager(self):
        """Mock attachment manager to avoid filesystem operations."""
        return MockAttachmentManager()
    
    def test_sample_database_integrity(self, test_db_copy):
        """Verify the sample database has expected structure and data."""
        conn = sqlite3.connect(str(test_db_copy))
        cursor = conn.cursor()
        
        # Check tables
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
        tables = [row[0] for row in cursor.fetchall()]
        
        required_tables = ['message', 'handle', 'chat', 'attachment']
        for table in required_tables:
            assert table in tables, f"Missing required table: {table}"
        
        # Check data counts
        cursor.execute("SELECT COUNT(*) FROM message")
        message_count = cursor.fetchone()[0]
        assert message_count == 200, f"Expected 200 messages, got {message_count}"
        
        cursor.execute("SELECT COUNT(*) FROM handle")
        handle_count = cursor.fetchone()[0]
        assert handle_count > 0, "No handles in sample"
        
        cursor.execute("SELECT COUNT(*) FROM attachment")
        attachment_count = cursor.fetchone()[0]
        assert attachment_count > 0, "No attachments in sample"
        
        conn.close()
    
    def test_anonymization_quality(self, test_db_copy):
        """Ensure personal data is properly anonymized in sample."""
        conn = sqlite3.connect(str(test_db_copy))
        cursor = conn.cursor()
        
        # Check handles are anonymized
        cursor.execute("SELECT id FROM handle WHERE id IS NOT NULL")
        for row in cursor:
            handle = row[0]
            # Should not contain real phone numbers or emails
            if '@' in handle:
                assert 'example.com' in handle, f"Non-anonymized email found: {handle}"
            elif handle[0] in ['+', '1', '2', '3', '4', '5', '6', '7', '8', '9']:
                # Phone numbers should use 555 prefix (fake numbers)
                assert '+1555' in handle or 'user' in handle, f"Potential real phone: {handle}"
        
        # Check message content
        cursor.execute("SELECT text FROM message WHERE text IS NOT NULL LIMIT 100")
        for row in cursor:
            text = row[0]
            # Basic checks for PII
            assert 'example.com' in text or '@' not in text, "Found non-anonymized email in message"
            # Phone pattern check would go here if needed
        
        conn.close()
    
    def test_imessage_db_connection(self, test_db_copy):
        """Test connecting to sample database with iMessageDB class."""
        # Note: This requires the Rust bridge to be built
        if iMessageDB is None:
            pytest.skip("iMessage bridge not built. Run: cd imessage-bridge && maturin develop")
        
        try:
            imessage_db = iMessageDB(str(test_db_copy))
            
            # Test basic queries
            handles = imessage_db.get_all_handles()
            assert len(handles) > 0, "No handles retrieved"
            
            messages = imessage_db.get_all_messages(limit=10)
            assert len(messages) > 0, "No messages retrieved"
            
            # Test message structure
            first_msg = messages[0]
            assert hasattr(first_msg, 'guid')
            assert hasattr(first_msg, 'text')
            assert hasattr(first_msg, 'date')
            
        except ImportError:
            pytest.skip("iMessage bridge not built. Run: cd imessage-bridge && maturin develop")
    
    def test_full_import_pipeline(self, test_db_manager, test_db_copy, mock_attachment_manager):
        """Test complete import pipeline with real sample data."""
        
        with patch('src.ingestion.imessage.AttachmentManager') as mock_am_class:
            mock_am_class.return_value = mock_attachment_manager
            
            # Mock Path.exists for attachments
            with patch('pathlib.Path.exists', return_value=True):
                pipeline = iMessageIncrementalPipeline(test_db_manager)
                
                # Import all messages from sample
                stats = pipeline.run_incremental_import(
                    db_path=str(test_db_copy),
                    limit=None,
                    known_contacts_only=False
                )
                
                # Verify statistics
                assert stats['total_processed'] == 200
                assert stats['new_messages'] == 200
                assert stats['new_principals'] > 0
                assert stats['new_identities'] > 0
                
                # Verify data in database
                with test_db_manager.get_session() as session:
                    messages = session.query(Message).all()
                    principals = session.query(Principal).all()
                    identities = session.query(IdentityClaim).all()
                    
                    assert len(messages) == 200
                    assert len(principals) > 0
                    assert len(identities) > 0
                    
                    # Check message structure
                    sample_msg = messages[0]
                    assert sample_msg.thread_id is not None
                    assert sample_msg.sent_at is not None
    
    def test_incremental_import_deduplication(self, test_db_manager, test_db_copy, mock_attachment_manager):
        """Test that duplicate messages are not imported twice."""
        
        with patch('src.ingestion.imessage.AttachmentManager') as mock_am_class:
            mock_am_class.return_value = mock_attachment_manager
            
            with patch('pathlib.Path.exists', return_value=True):
                pipeline = iMessageIncrementalPipeline(test_db_manager)
                
                # First import - 50 messages
                stats1 = pipeline.run_incremental_import(
                    db_path=str(test_db_copy),
                    limit=50,
                    known_contacts_only=False
                )
                
                assert stats1['new_messages'] == 50
                
                # Second import - same 50 messages
                stats2 = pipeline.run_incremental_import(
                    db_path=str(test_db_copy),
                    limit=50,
                    known_contacts_only=False
                )
                
                # Should skip all as duplicates
                assert stats2['new_messages'] == 0
                assert stats2['skipped_messages'] == 50
                
                # Verify database has exactly 50 messages
                with test_db_manager.get_session() as session:
                    total = session.query(Message).count()
                    assert total == 50
    
    def test_known_contacts_filtering(self, test_db_manager, test_db_copy, mock_attachment_manager):
        """Test filtering messages by known contacts."""
        
        # Pre-populate with a known contact
        with test_db_manager.get_session() as session:
            principal = Principal(display_name="Known Test User")
            session.add(principal)
            session.flush()
            
            # Add identity matching anonymized pattern
            identity = IdentityClaim(
                principal_id=principal.id,
                platform='imessage',
                kind='phone',
                value='+15550000000',  # Matches anonymized number pattern
                normalized='+15550000000',
                confidence=1.0
            )
            session.add(identity)
            session.commit()
        
        with patch('src.ingestion.imessage.AttachmentManager') as mock_am_class:
            mock_am_class.return_value = mock_attachment_manager
            
            with patch('pathlib.Path.exists', return_value=True):
                pipeline = iMessageIncrementalPipeline(test_db_manager)
                
                # Import with known_contacts_only=True
                stats = pipeline.run_incremental_import(
                    db_path=str(test_db_copy),
                    limit=None,
                    known_contacts_only=True
                )
                
                # Should have skipped unknown contacts
                assert stats['skipped_unknown_contacts'] > 0
                
                # All imported messages should be from known contacts
                with test_db_manager.get_session() as session:
                    messages = session.query(Message).all()
                    
                    for msg in messages:
                        # Check that message has person_message links
                        person_msgs = session.query(PersonMessage).filter_by(
                            message_id=msg.id
                        ).all()
                        assert len(person_msgs) > 0, "Message not linked to any person"
    
    def test_attachment_processing(self, test_db_manager, test_db_copy, mock_attachment_manager):
        """Test that attachments are properly processed from sample."""
        
        # Check sample has attachments
        conn = sqlite3.connect(str(test_db_copy))
        cursor = conn.execute("""
            SELECT COUNT(*) 
            FROM message_attachment_join maj
            JOIN attachment a ON maj.attachment_id = a.ROWID
        """)
        sample_attachment_count = cursor.fetchone()[0]
        conn.close()
        
        if sample_attachment_count == 0:
            pytest.skip("No attachments in sample")
        
        with patch('src.ingestion.imessage.AttachmentManager') as mock_am_class:
            mock_am_class.return_value = mock_attachment_manager
            
            with patch('pathlib.Path.exists', return_value=True):
                pipeline = iMessageIncrementalPipeline(test_db_manager)
                
                stats = pipeline.run_incremental_import(
                    db_path=str(test_db_copy),
                    limit=None,
                    known_contacts_only=False
                )
                
                # Should have processed some attachments
                assert stats.get('attachments_stored', 0) > 0 or stats.get('attachments_failed', 0) > 0
                
                # Check mock was called
                if stats.get('attachments_stored', 0) > 0:
                    assert len(mock_attachment_manager.storage_calls) > 0
                    
                    # Verify database records
                    with test_db_manager.get_session() as session:
                        attachments = session.query(MessageAttachment).all()
                        assert len(attachments) > 0
    
    def test_threading_and_channels(self, test_db_manager, test_db_copy, mock_attachment_manager):
        """Test that messages are properly organized into threads and channels."""
        
        with patch('src.ingestion.imessage.AttachmentManager') as mock_am_class:
            mock_am_class.return_value = mock_attachment_manager
            
            with patch('pathlib.Path.exists', return_value=True):
                pipeline = iMessageIncrementalPipeline(test_db_manager)
                
                stats = pipeline.run_incremental_import(
                    db_path=str(test_db_copy),
                    limit=None,
                    known_contacts_only=False
                )
                
                with test_db_manager.get_session() as session:
                    # Should have at least one channel
                    channels = session.query(Channel).all()
                    assert len(channels) >= 1
                    assert channels[0].platform == 'imessage'
                    
                    # Should have threads
                    threads = session.query(Thread).all()
                    assert len(threads) > 0
                    
                    # Each thread should have messages
                    for thread in threads[:5]:  # Check first 5 threads
                        thread_messages = session.query(Message).filter_by(
                            thread_id=thread.id
                        ).all()
                        
                        if thread_messages:
                            # Thread dates should bracket message dates
                            msg_dates = [m.sent_at for m in thread_messages]
                            assert thread.started_at <= max(msg_dates)
                            assert thread.last_at >= min(msg_dates)