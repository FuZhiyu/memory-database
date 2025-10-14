import email
import mailbox
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from email.header import decode_header
from email.utils import parsedate_to_datetime, parseaddr
from tqdm import tqdm

from memory_database.ingestion.base import IngestionSource


class EmailIngestionSource(IngestionSource):
    """Ingestion source for email data (MBOX, EML files)."""
    
    def get_platform_name(self) -> str:
        return "email"
    
    def count_items(self, source_path: str) -> Optional[int]:
        """Count total emails that will be processed."""
        path = Path(source_path)
        count = 0
        
        try:
            if path.is_file():
                if path.suffix.lower() == '.mbox':
                    # Count messages in mbox file
                    mbox = mailbox.mbox(str(path))
                    count = len(mbox)
                elif path.suffix.lower() in ['.eml', '.msg']:
                    count = 1
            elif path.is_dir():
                # Count all email files in directory
                count += len(list(path.rglob("*.eml")))
                # For mbox files, we'd need to open each to count messages
                for mbox_file in path.rglob("*.mbox"):
                    try:
                        mbox = mailbox.mbox(str(mbox_file))
                        count += len(mbox)
                    except Exception:
                        pass
            return count if count > 0 else None
        except Exception:
            return None
    
    def extract_raw_data(self, source_path: str) -> Iterator[Dict[str, Any]]:
        """Extract messages from MBOX or EML files."""
        path = Path(source_path)
        
        if path.is_file():
            if path.suffix.lower() == '.mbox':
                yield from self._extract_from_mbox(str(path))
            elif path.suffix.lower() in ['.eml', '.msg']:
                yield from self._extract_from_eml(str(path))
            else:
                self.logger.warning("Unsupported email file format", path=str(path))
        elif path.is_dir():
            # Process all email files in directory
            for email_file in path.rglob("*.mbox"):
                yield from self._extract_from_mbox(str(email_file))
            for email_file in path.rglob("*.eml"):
                yield from self._extract_from_eml(str(email_file))
        else:
            self.logger.error("Email source path not found", path=str(path))
    
    def _extract_from_mbox(self, mbox_path: str) -> Iterator[Dict[str, Any]]:
        """Extract messages from an MBOX file."""
        try:
            mbox = mailbox.mbox(mbox_path)
            for message in mbox:
                yield self._parse_email_message(message)
        except Exception as e:
            self.logger.error("Failed to read MBOX file", path=mbox_path, error=str(e))
    
    def _extract_from_eml(self, eml_path: str) -> Iterator[Dict[str, Any]]:
        """Extract a message from an EML file."""
        try:
            with open(eml_path, 'rb') as f:
                message = email.message_from_binary_file(f)
                yield self._parse_email_message(message)
        except Exception as e:
            self.logger.error("Failed to read EML file", path=eml_path, error=str(e))
    
    def _parse_email_message(self, message: email.message.Message) -> Dict[str, Any]:
        """Parse an email.Message object into our standard format."""
        return {
            'message_id': message.get('Message-ID', '').strip('<>'),
            'subject': self._decode_header(message.get('Subject', '')),
            'sender': self._parse_address(message.get('From', '')),
            'recipients': self._parse_address_list(message.get('To', '')) + 
                         self._parse_address_list(message.get('Cc', '')),
            'date': self._parse_date(message.get('Date')),
            'content': self._extract_content(message),
            'in_reply_to': message.get('In-Reply-To', '').strip('<>'),
            'references': self._parse_references(message.get('References', '')),
            'headers': dict(message.items()),
            'raw_message': message
        }
    
    def normalize_message(self, raw_message: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize email message to standard format."""
        # Use subject as thread identifier for email threading
        subject = raw_message.get('subject', '').strip()
        thread_id = self._normalize_thread_subject(subject) if subject else 'no_subject'
        
        return {
            'platform': self.get_platform_name(),
            'message_id': raw_message.get('message_id'),
            'thread_id': thread_id,
            'subject': subject,
            'sender': raw_message.get('sender'),
            'recipients': raw_message.get('recipients', []),
            'sent_at': raw_message.get('date'),
            'content': raw_message.get('content'),
            'content_type': 'text/plain',  # TODO: Better content type detection
            'reply_to': raw_message.get('in_reply_to'),
            'references': raw_message.get('references', []),
            'extra': {
                'headers': raw_message.get('headers', {}),
                'subject': subject
            }
        }
    
    def _normalize_thread_subject(self, subject: str) -> str:
        """Normalize email subject for thread grouping."""
        # Remove common reply/forward prefixes
        normalized = subject
        for prefix in ['Re:', 'RE:', 'Fwd:', 'FWD:', 'Fw:', 'FW:']:
            normalized = normalized.replace(prefix, '').strip()
        
        # Remove extra whitespace
        return ' '.join(normalized.split())
    
    def _decode_header(self, header: str) -> str:
        """Decode email header that might be encoded."""
        if not header:
            return ''
        
        decoded_parts = []
        for part, encoding in decode_header(header):
            if isinstance(part, bytes):
                if encoding:
                    try:
                        part = part.decode(encoding)
                    except (UnicodeDecodeError, LookupError):
                        part = part.decode('utf-8', errors='replace')
                else:
                    part = part.decode('utf-8', errors='replace')
            decoded_parts.append(part)
        
        return ''.join(decoded_parts)
    
    def _parse_address(self, address_str: str) -> str:
        """Parse an email address, extracting just the email part."""
        if not address_str:
            return ''
        
        name, email_addr = parseaddr(address_str)
        return email_addr.strip() if email_addr else ''
    
    def _parse_address_list(self, address_str: str) -> List[str]:
        """Parse a comma-separated list of email addresses."""
        if not address_str:
            return []
        
        addresses = []
        for addr in address_str.split(','):
            parsed = self._parse_address(addr.strip())
            if parsed:
                addresses.append(parsed)
        
        return addresses
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse email date header."""
        if not date_str:
            return None
        
        try:
            return parsedate_to_datetime(date_str)
        except (ValueError, TypeError):
            self.logger.warning("Failed to parse date", date_str=date_str)
            return None
    
    def _extract_content(self, message: email.message.Message) -> str:
        """Extract text content from email message."""
        if message.is_multipart():
            content_parts = []
            for part in message.walk():
                if part.get_content_type() == 'text/plain':
                    content = self._get_payload_content(part)
                    if content:
                        content_parts.append(content)
            return '\n\n'.join(content_parts)
        else:
            return self._get_payload_content(message) or ''
    
    def _get_payload_content(self, part: email.message.Message) -> str:
        """Get text content from a message part."""
        try:
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                # Try to decode with the specified charset
                charset = part.get_content_charset() or 'utf-8'
                try:
                    return payload.decode(charset)
                except (UnicodeDecodeError, LookupError):
                    return payload.decode('utf-8', errors='replace')
            elif isinstance(payload, str):
                return payload
            else:
                return ''
        except Exception as e:
            self.logger.warning("Failed to extract content from message part", error=str(e))
            return ''
    
    def _parse_references(self, references_str: str) -> List[str]:
        """Parse References header into list of message IDs."""
        if not references_str:
            return []
        
        # References can contain multiple message IDs in angle brackets
        refs = re.findall(r'<([^>]+)>', references_str)
        return refs if refs else []