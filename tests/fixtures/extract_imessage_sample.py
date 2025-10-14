#!/usr/bin/env python3
"""
Extract a sample of the iMessage database for testing.
This creates a smaller, anonymized copy suitable for unit tests.
"""
import sqlite3
import shutil
from pathlib import Path
from typing import Optional, Set, List, Tuple
import hashlib
import random
import re


class iMessageSampleExtractor:
    """Extract and anonymize a sample from the iMessage database."""
    
    def __init__(self, source_db: Path, target_db: Path):
        self.source_db = source_db
        self.target_db = target_db
        self.anonymization_map = {}
        
    def extract_sample(
        self,
        message_limit: int = 100,
        anonymize: bool = True,
        preserve_structure: bool = True,
        start_date: Optional[int] = None,
        end_date: Optional[int] = None
    ) -> Path:
        """
        Extract a sample of the iMessage database.
        
        Args:
            message_limit: Maximum number of messages to extract
            anonymize: Whether to anonymize personal data
            preserve_structure: Keep relationships between tables intact
            start_date: Optional start date (Apple epoch nanoseconds)
            end_date: Optional end date (Apple epoch nanoseconds)
            
        Returns:
            Path to the created sample database
        """
        # Remove target if it exists
        if self.target_db.exists():
            self.target_db.unlink()
        
        # Connect to source database
        source_conn = sqlite3.connect(str(self.source_db))
        source_conn.row_factory = sqlite3.Row
        
        # Create new target database with schema only
        target_conn = sqlite3.connect(str(self.target_db))
        
        # Copy schema from source to target
        self._copy_schema(source_conn, target_conn)
        
        try:
            
            # Extract sample data
            message_ids = self._extract_messages(
                source_conn, target_conn, 
                message_limit, start_date, end_date, anonymize
            )
            
            if preserve_structure:
                # Extract related data
                self._extract_related_handles(source_conn, target_conn, message_ids, anonymize)
                self._extract_related_chats(source_conn, target_conn, message_ids, anonymize)
                self._extract_related_attachments(source_conn, target_conn, message_ids, anonymize)
            
            target_conn.commit()
            
            # Vacuum to reduce file size
            target_conn.execute("VACUUM")
            
            print(f"Sample database created: {self.target_db}")
            print(f"Extracted {len(message_ids)} messages")
            
            if anonymize:
                print("Data has been anonymized")
                
        finally:
            source_conn.close()
            target_conn.close()
            
        return self.target_db
    
    def _copy_schema(self, source_conn: sqlite3.Connection, target_conn: sqlite3.Connection):
        """Copy database schema from source to target."""
        # Get the schema SQL from source
        cursor = source_conn.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table', 'index') AND sql IS NOT NULL"
        )
        
        for row in cursor:
            sql = row[0]
            # Skip sqlite internal tables
            if 'sqlite_' not in sql:
                try:
                    target_conn.execute(sql)
                except sqlite3.OperationalError as e:
                    # Table or index might already exist
                    pass
        
        target_conn.commit()
    
    def _clear_data(self, conn: sqlite3.Connection):
        """Clear all data from target database while preserving schema."""
        tables = [
            'message', 'handle', 'chat', 'attachment',
            'chat_message_join', 'chat_handle_join', 
            'message_attachment_join'
        ]
        
        for table in tables:
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                # Table might not exist
                pass
    
    def _extract_messages(
        self,
        source_conn: sqlite3.Connection,
        target_conn: sqlite3.Connection,
        limit: int,
        start_date: Optional[int],
        end_date: Optional[int],
        anonymize: bool
    ) -> Set[int]:
        """Extract messages from source to target."""
        query = "SELECT * FROM message WHERE 1=1"
        params = []
        
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
            
        query += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        
        cursor = source_conn.execute(query, params)
        messages = cursor.fetchall()
        
        message_ids = set()
        
        for msg in messages:
            msg_dict = dict(msg)
            message_ids.add(msg_dict['ROWID'])
            
            if anonymize:
                msg_dict = self._anonymize_message(msg_dict)
            
            # Insert into target (let SQLite handle ROWID or explicitly insert it)
            columns = list(msg_dict.keys())
            
            # Check if we need to handle ROWID specially
            if 'ROWID' in columns:
                # Insert with explicit ROWID
                placeholders = ','.join(['?' for _ in columns])
                values = [msg_dict[col] for col in columns]
                
                try:
                    target_conn.execute(
                        f"INSERT INTO message ({','.join(columns)}) VALUES ({placeholders})",
                        values
                    )
                except sqlite3.IntegrityError:
                    # ROWID conflict, insert without ROWID
                    columns = [c for c in columns if c != 'ROWID']
                    placeholders = ','.join(['?' for _ in columns])
                    values = [msg_dict[col] for col in columns if col != 'ROWID']
                    target_conn.execute(
                        f"INSERT INTO message ({','.join(columns)}) VALUES ({placeholders})",
                        values
                    )
            else:
                placeholders = ','.join(['?' for _ in columns])
                values = [msg_dict[col] for col in columns]
                target_conn.execute(
                    f"INSERT INTO message ({','.join(columns)}) VALUES ({placeholders})",
                    values
                )
        
        return message_ids
    
    def _extract_related_handles(
        self,
        source_conn: sqlite3.Connection,
        target_conn: sqlite3.Connection,
        message_ids: Set[int],
        anonymize: bool
    ):
        """Extract handles related to the sampled messages."""
        # Get unique handle IDs from messages
        handle_ids = set()
        for msg_id in message_ids:
            cursor = source_conn.execute(
                "SELECT handle_id FROM message WHERE ROWID = ? AND handle_id IS NOT NULL",
                (msg_id,)
            )
            result = cursor.fetchone()
            if result:
                handle_ids.add(result[0])
        
        # Also get handles from chat participants
        cursor = source_conn.execute("""
            SELECT DISTINCT chj.handle_id
            FROM chat_message_join cmj
            JOIN chat_handle_join chj ON cmj.chat_id = chj.chat_id
            WHERE cmj.message_id IN ({})
        """.format(','.join('?' * len(message_ids))), list(message_ids))
        
        for row in cursor:
            handle_ids.add(row[0])
        
        # Extract handles
        for handle_id in handle_ids:
            cursor = source_conn.execute(
                "SELECT * FROM handle WHERE ROWID = ?",
                (handle_id,)
            )
            handle = cursor.fetchone()
            
            if handle:
                handle_dict = dict(handle)
                
                if anonymize:
                    handle_dict = self._anonymize_handle(handle_dict)
                
                columns = list(handle_dict.keys())
                placeholders = ','.join(['?' for _ in columns])
                values = [handle_dict[col] for col in columns]
                
                target_conn.execute(
                    f"INSERT INTO handle ({','.join(columns)}) VALUES ({placeholders})",
                    values
                )
    
    def _extract_related_chats(
        self,
        source_conn: sqlite3.Connection,
        target_conn: sqlite3.Connection,
        message_ids: Set[int],
        anonymize: bool
    ):
        """Extract chats and relationships for sampled messages."""
        # Get unique chat IDs
        chat_ids = set()
        cursor = source_conn.execute("""
            SELECT DISTINCT chat_id 
            FROM chat_message_join 
            WHERE message_id IN ({})
        """.format(','.join('?' * len(message_ids))), list(message_ids))
        
        for row in cursor:
            chat_ids.add(row[0])
        
        # Extract chats
        for chat_id in chat_ids:
            cursor = source_conn.execute(
                "SELECT * FROM chat WHERE ROWID = ?",
                (chat_id,)
            )
            chat = cursor.fetchone()
            
            if chat:
                chat_dict = dict(chat)
                
                if anonymize:
                    chat_dict = self._anonymize_chat(chat_dict)
                
                columns = list(chat_dict.keys())
                placeholders = ','.join(['?' for _ in columns])
                values = [chat_dict[col] for col in columns]
                
                target_conn.execute(
                    f"INSERT INTO chat ({','.join(columns)}) VALUES ({placeholders})",
                    values
                )
        
        # Extract chat_message_join
        cursor = source_conn.execute("""
            SELECT * FROM chat_message_join 
            WHERE message_id IN ({})
        """.format(','.join('?' * len(message_ids))), list(message_ids))
        
        for row in cursor:
            target_conn.execute(
                "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
                (row['chat_id'], row['message_id'])
            )
        
        # Extract chat_handle_join for these chats
        cursor = source_conn.execute("""
            SELECT * FROM chat_handle_join 
            WHERE chat_id IN ({})
        """.format(','.join('?' * len(chat_ids))), list(chat_ids))
        
        for row in cursor:
            target_conn.execute(
                "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
                (row['chat_id'], row['handle_id'])
            )
    
    def _extract_related_attachments(
        self,
        source_conn: sqlite3.Connection,
        target_conn: sqlite3.Connection,
        message_ids: Set[int],
        anonymize: bool
    ):
        """Extract attachments for sampled messages."""
        # Get attachment IDs
        attachment_ids = set()
        cursor = source_conn.execute("""
            SELECT attachment_id 
            FROM message_attachment_join 
            WHERE message_id IN ({})
        """.format(','.join('?' * len(message_ids))), list(message_ids))
        
        for row in cursor:
            attachment_ids.add(row[0])
        
        # Extract attachments
        for att_id in attachment_ids:
            cursor = source_conn.execute(
                "SELECT * FROM attachment WHERE ROWID = ?",
                (att_id,)
            )
            attachment = cursor.fetchone()
            
            if attachment:
                att_dict = dict(attachment)
                
                if anonymize:
                    att_dict = self._anonymize_attachment(att_dict)
                
                columns = list(att_dict.keys())
                placeholders = ','.join(['?' for _ in columns])
                values = [att_dict[col] for col in columns]
                
                target_conn.execute(
                    f"INSERT INTO attachment ({','.join(columns)}) VALUES ({placeholders})",
                    values
                )
        
        # Extract message_attachment_join
        cursor = source_conn.execute("""
            SELECT * FROM message_attachment_join 
            WHERE message_id IN ({})
        """.format(','.join('?' * len(message_ids))), list(message_ids))
        
        for row in cursor:
            target_conn.execute(
                "INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?, ?)",
                (row['message_id'], row['attachment_id'])
            )
    
    def _anonymize_message(self, msg: dict) -> dict:
        """Anonymize message content."""
        if msg.get('text'):
            # Fully replace message text with generic placeholder
            # Preserve approximate length to maintain realistic data structure
            text_length = len(msg['text'])

            # Generate placeholder based on length
            if text_length < 20:
                msg['text'] = "Sample message"
            elif text_length < 50:
                msg['text'] = "This is a sample test message."
            elif text_length < 100:
                msg['text'] = "This is a longer sample test message for testing purposes."
            else:
                msg['text'] = "This is a longer sample test message for testing purposes. It contains multiple sentences to simulate real message length and structure."

        # Anonymize cache_roomnames if present
        if msg.get('cache_roomnames'):
            msg['cache_roomnames'] = self._anonymize_identifier(msg['cache_roomnames'])

        # Anonymize account field (contains email like E:user@example.com)
        if msg.get('account'):
            # Preserve E: prefix if present
            if msg['account'].startswith('E:'):
                msg['account'] = 'E:test@example.com'
            elif msg['account'].startswith('P:'):
                msg['account'] = 'P:+15550001234'
            else:
                msg['account'] = 'test@example.com'

        # Anonymize account_guid if present
        if msg.get('account_guid'):
            # Keep it as a valid GUID format
            msg['account_guid'] = '00000000-0000-0000-0000-000000000001'

        # Anonymize destination_caller_id if present
        if msg.get('destination_caller_id'):
            msg['destination_caller_id'] = '+15550001234'

        return msg
    
    def _anonymize_handle(self, handle: dict) -> dict:
        """Anonymize handle information."""
        if handle.get('id'):
            original_id = handle['id']
            
            if original_id not in self.anonymization_map:
                if '@' in original_id:
                    # Email
                    self.anonymization_map[original_id] = f"user{len(self.anonymization_map)}@example.com"
                elif original_id.startswith('+') or original_id[0].isdigit():
                    # Phone number
                    self.anonymization_map[original_id] = f"+1555000{len(self.anonymization_map):04d}"
                else:
                    # Other identifier
                    self.anonymization_map[original_id] = f"user_{len(self.anonymization_map):04d}"
            
            handle['id'] = self.anonymization_map[original_id]
            
        if handle.get('uncanonicalized_id'):
            handle['uncanonicalized_id'] = handle['id']
            
        return handle
    
    def _anonymize_chat(self, chat: dict) -> dict:
        """Anonymize chat information."""
        if chat.get('chat_identifier'):
            chat['chat_identifier'] = self._anonymize_identifier(chat['chat_identifier'])

        if chat.get('guid'):
            # Preserve format but anonymize content
            parts = chat['guid'].split(';')
            if len(parts) >= 3:
                parts[2] = self._anonymize_identifier(parts[2])
                chat['guid'] = ';'.join(parts)

        if chat.get('display_name'):
            chat['display_name'] = f"Group Chat {chat['ROWID']}"

        if chat.get('room_name'):
            chat['room_name'] = f"Room {chat['ROWID']}"

        # Anonymize account_id (GUID)
        if chat.get('account_id'):
            chat['account_id'] = '00000000-0000-0000-0000-000000000001'

        # Anonymize account_login (email address)
        if chat.get('account_login'):
            chat['account_login'] = 'test@example.com'

        # Anonymize last_addressed_handle if present
        if chat.get('last_addressed_handle'):
            chat['last_addressed_handle'] = '+15550001234'

        return chat
    
    def _anonymize_attachment(self, att: dict) -> dict:
        """Anonymize attachment paths but preserve structure."""
        if att.get('filename'):
            # Preserve directory structure but change filename
            path_parts = att['filename'].split('/')
            if len(path_parts) > 1:
                filename = path_parts[-1]
                ext = Path(filename).suffix
                new_filename = f"file_{att['ROWID']}{ext}"
                path_parts[-1] = new_filename
                att['filename'] = '/'.join(path_parts)
        
        if att.get('transfer_name'):
            ext = Path(att['transfer_name']).suffix
            att['transfer_name'] = f"file_{att['ROWID']}{ext}"
            
        return att
    
    def _anonymize_identifier(self, identifier: str) -> str:
        """Anonymize an identifier consistently."""
        if identifier not in self.anonymization_map:
            hash_val = hashlib.md5(identifier.encode()).hexdigest()[:8]
            self.anonymization_map[identifier] = f"id_{hash_val}"
        return self.anonymization_map[identifier]


def main():
    """Extract a sample database for testing."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Extract iMessage database sample for testing')
    parser.add_argument('--source', type=Path, 
                       default=Path.home() / 'Library' / 'Messages' / 'chat.db',
                       help='Source iMessage database path')
    parser.add_argument('--target', type=Path,
                       default=Path('tests/fixtures/test_imessage_sample.db'),
                       help='Target sample database path')
    parser.add_argument('--limit', type=int, default=100,
                       help='Number of messages to extract')
    parser.add_argument('--no-anonymize', action='store_true',
                       help='Skip anonymization (not recommended)')
    
    args = parser.parse_args()
    
    if not args.source.exists():
        print(f"Source database not found: {args.source}")
        return 1
    
    # Create target directory if needed
    args.target.parent.mkdir(parents=True, exist_ok=True)
    
    extractor = iMessageSampleExtractor(args.source, args.target)
    extractor.extract_sample(
        message_limit=args.limit,
        anonymize=not args.no_anonymize,
        preserve_structure=True
    )
    
    return 0


if __name__ == '__main__':
    exit(main())