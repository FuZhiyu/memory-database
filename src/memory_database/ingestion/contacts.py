import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from dateutil.parser import parse as parse_datetime
from tqdm import tqdm

from memory_database.ingestion.base import IngestionSource
from memory_database.utils.normalization import normalize_phone, normalize_email, normalize_identity_value
from memory_database.utils.chinese import contains_chinese, chinese_aliases


class ContactsIngestionSource(IngestionSource):
    """Ingestion source for Google Contacts JSON data."""
    
    def get_platform_name(self) -> str:
        return "contacts"
    
    def count_items(self, source_path: str) -> Optional[int]:
        """Count total contacts in the file."""
        path = Path(source_path)
        
        if not path.exists():
            return None
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return len(data.get('contacts', []))
        except Exception:
            return None
    
    def extract_raw_data(self, source_path: str) -> Iterator[Dict[str, Any]]:
        """Extract contacts from Google Contacts JSON file."""
        path = Path(source_path)
        
        if not path.exists():
            self.logger.error("Contacts file not found", path=str(path))
            return
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            sync_date = data.get('sync_date')
            total_contacts = data.get('total_contacts', 0)
            contacts = data.get('contacts', [])
            
            self.logger.info("Loading contacts from file", 
                           sync_date=sync_date,
                           total_contacts=total_contacts,
                           file=str(path))
            
            for contact in contacts:
                yield contact
                
        except Exception as e:
            self.logger.error("Failed to load contacts file", path=str(path), error=str(e))
    
    def normalize_message(self, raw_contact: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize contact data to a message-like format for processing."""
        # For contacts, we'll create a special "message" that represents the contact record
        return {
            'platform': self.get_platform_name(),
            'contact_id': raw_contact.get('resource_name', ''),
            'display_name': raw_contact.get('display_name', ''),
            'given_name': raw_contact.get('given_name', ''),
            'family_name': raw_contact.get('family_name', ''),
            'middle_name': raw_contact.get('middle_name', ''),
            'nicknames': raw_contact.get('nicknames', []),
            'emails': raw_contact.get('emails', []),
            'phones': raw_contact.get('phones', []),
            'organizations': raw_contact.get('organizations', []),
            'addresses': raw_contact.get('addresses', []),
            'birthdays': raw_contact.get('birthdays', []),
            'biography': raw_contact.get('biography', ''),
            'urls': raw_contact.get('urls', []),
            'relations': raw_contact.get('relations', []),
            'last_modified': self._parse_datetime(raw_contact.get('last_modified')),
            'contact_data': raw_contact  # Store the full contact data
        }
    
    def extract_identities(self, contact_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract identity claims from a contact record."""
        identities = []
        
        # Extract email identities
        for email_info in contact_data.get('emails', []):
            email_addr = email_info.get('address', '').strip()
            if email_addr:
                identities.append({
                    'platform': self.get_platform_name(),
                    'kind': 'email',
                    'value': email_addr,
                    'normalized': normalize_email(email_addr),
                    'confidence': 1.0,
                    'extra': {
                        'type': email_info.get('type', ''),
                        'display_name': email_info.get('display_name', '')
                    }
                })
        
        # Extract phone identities
        for phone_info in contact_data.get('phones', []):
            phone_number = phone_info.get('number', '').strip()
            canonical_number = phone_info.get('canonical', '').strip()
            
            if phone_number:
                # Always use our own normalization for consistency
                normalized = normalize_phone(phone_number)
                
                if normalized:  # Only add if we got a valid normalized number
                    identities.append({
                        'platform': self.get_platform_name(),
                        'kind': 'phone',
                        'value': phone_number,
                        'normalized': normalized,
                        'confidence': 1.0,
                        'extra': {
                            'type': phone_info.get('type', ''),
                            'google_canonical': canonical_number,  # Store for reference only
                            'original': phone_number
                        }
                    })
        
        # Add Google contact ID as an identity
        contact_id = contact_data.get('contact_id')
        if contact_id:
            identities.append({
                'platform': self.get_platform_name(),
                'kind': 'contact_id',
                'value': contact_id,
                'normalized': contact_id.lower(),
                'confidence': 1.0,
                'extra': {}
            })
        
        # Add display name as a potential identity (lower confidence)
        display_name = contact_data.get('display_name', '').strip()
        if display_name and display_name.lower() not in ['unknown', '']:
            identities.append({
                'platform': self.get_platform_name(),
                'kind': 'display_name',
                'value': display_name,
                'normalized': display_name.lower(),
                'confidence': 0.8,  # Lower confidence for name matching
                'extra': {
                    'given_name': contact_data.get('given_name', ''),
                    'family_name': contact_data.get('family_name', ''),
                    'middle_name': contact_data.get('middle_name', '')
                }
            })
        
        return identities
    
    
    def _parse_datetime(self, datetime_str: str) -> Optional[datetime]:
        """Parse ISO datetime string."""
        if not datetime_str:
            return None
        
        try:
            return parse_datetime(datetime_str)
        except Exception as e:
            self.logger.warning("Failed to parse datetime", 
                              datetime_str=datetime_str, error=str(e))
            return None
    
    def get_contact_hash(self, contact_data: Dict[str, Any]) -> str:
        """Generate a hash of the contact data for change detection."""
        import hashlib
        
        # Create a stable hash based on key contact fields
        last_modified = contact_data.get('last_modified')
        last_modified_str = last_modified.isoformat() if last_modified else ''
        
        hash_data = {
            'display_name': contact_data.get('display_name', ''),
            'emails': sorted([e.get('address', '') for e in contact_data.get('emails', [])]),
            'phones': sorted([p.get('canonical', p.get('number', '')) for p in contact_data.get('phones', [])]),
            'last_modified': last_modified_str,
            'organizations': contact_data.get('organizations', []),
            'biography': contact_data.get('biography', ''),
        }
        
        hash_str = json.dumps(hash_data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(hash_str.encode('utf-8')).hexdigest()


class ContactsIncrementalPipeline:
    """Specialized pipeline for incremental contacts import."""
    
    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.logger = None  # Will be set by CLI
        
    def run_incremental_import(self, contacts_path: str):
        """Run incremental import of contacts, only updating changed records."""
        source = ContactsIngestionSource(self.db_manager)
        
        stats = {
            'total_processed': 0,
            'new_contacts': 0,
            'updated_contacts': 0,
            'unchanged_contacts': 0,
            'new_identities': 0,
            'updated_identities': 0
        }
        
        with self.db_manager.get_session() as session:
            from memory_database.models import Principal, IdentityClaim
            
            # Build a lookup of existing contacts by resource_name
            existing_contacts = {}
            existing_claims = session.query(IdentityClaim).filter_by(
                platform='contacts',
                kind='contact_id'
            ).all()
            
            for claim in existing_claims:
                # Get the principal and extract hash from extra data
                principal = session.query(Principal).get(claim.principal_id)
                contact_hash = principal.extra.get('contact_hash') if principal else None
                
                existing_contacts[claim.value] = {
                    'principal_id': claim.principal_id,
                    'contact_claim': claim,
                    'hash': contact_hash,
                    'all_claims': []
                }
            
            # Get all claims for existing contacts
            for contact_id, contact_info in existing_contacts.items():
                principal_claims = session.query(IdentityClaim).filter_by(
                    principal_id=contact_info['principal_id'],
                    platform='contacts'
                ).all()
                contact_info['all_claims'] = principal_claims
            
            self.logger.info("Starting incremental contacts import",
                           existing_contacts=len(existing_contacts))
            
            # Get total count for progress bar
            total_count = source.count_items(contacts_path)
            raw_data_iter = source.extract_raw_data(contacts_path)
            
            if total_count:
                raw_data_iter = tqdm(
                    raw_data_iter,
                    total=total_count,
                    desc="Importing contacts",
                    unit="contacts"
                )
            
            for raw_contact in raw_data_iter:
                try:
                    stats['total_processed'] += 1
                    contact_data = source.normalize_message(raw_contact)
                    
                    contact_id = contact_data.get('contact_id')
                    if not contact_id:
                        self.logger.warning("Contact missing resource_name, skipping")
                        continue
                    
                    # Generate current hash
                    current_hash = source.get_contact_hash(contact_data)
                    
                    # Check if this is a new or existing contact
                    if contact_id in existing_contacts:
                        # Existing contact - check if it needs updating
                        existing_info = existing_contacts[contact_id]
                        existing_hash = existing_info.get('hash')
                        
                        if current_hash == existing_hash:
                            stats['unchanged_contacts'] += 1
                            continue
                        
                        # Update existing contact
                        self._update_existing_contact(
                            session, source, contact_data, existing_info, current_hash, stats
                        )
                        stats['updated_contacts'] += 1
                    else:
                        # New contact
                        self._create_new_contact(session, source, contact_data, current_hash, stats)
                        stats['new_contacts'] += 1
                    
                    if stats['total_processed'] % 100 == 0:
                        session.commit()
                
                except Exception as e:
                    self.logger.error("Failed to process contact",
                                    contact_id=contact_data.get('contact_id'),
                                    error=str(e), exc_info=True)
                    continue
        
        self.logger.info("Incremental contacts import completed", **stats)
        return stats
    
    def _create_new_contact(self, session, source, contact_data, current_hash, stats):
        """Create a new contact and all its identity claims."""
        from memory_database.models import Principal, IdentityClaim
        
        # Create new principal
        display_name = contact_data.get('display_name') or 'Unknown Contact'
        principal = Principal(
            display_name=display_name,
            extra={
                'contact_hash': current_hash,
                'given_name': contact_data.get('given_name'),
                'family_name': contact_data.get('family_name'),
                'middle_name': contact_data.get('middle_name'),
                'biography': contact_data.get('biography'),
                'organizations': contact_data.get('organizations', []),
                'addresses': contact_data.get('addresses', []),
                'birthdays': contact_data.get('birthdays', []),
                'urls': contact_data.get('urls', []),
                'relations': contact_data.get('relations', [])
            }
        )
        session.add(principal)
        session.flush()  # Get the ID
        
        # Create all identity claims
        identities = source.extract_identities(contact_data)
        for identity_data in identities:
            # Check for existing claim with same (principal_id, platform, normalized)
            # This prevents constraint violations
            existing = session.query(IdentityClaim).filter_by(
                principal_id=principal.id,
                platform=identity_data['platform'],
                normalized=identity_data['normalized']
            ).first()

            if existing:
                # Update existing claim instead of creating duplicate
                existing.value = identity_data['value']
                existing.kind = identity_data['kind']  # Update kind if changed
                existing.confidence = identity_data['confidence']
                existing.last_seen = contact_data.get('last_modified') or datetime.utcnow()
                existing.extra = identity_data.get('extra', {})
                stats['updated_identities'] += 1
            else:
                # Create new identity claim
                identity_claim = IdentityClaim(
                    principal_id=principal.id,
                    platform=identity_data['platform'],
                    kind=identity_data['kind'],
                    value=identity_data['value'],
                    normalized=identity_data['normalized'],
                    confidence=identity_data['confidence'],
                    first_seen=contact_data.get('last_modified') or datetime.utcnow(),
                    last_seen=contact_data.get('last_modified') or datetime.utcnow(),
                    extra=identity_data.get('extra', {})
                )
                session.add(identity_claim)
                stats['new_identities'] += 1

        # Auto-add Chinese aliases if display name appears Chinese
        try:
            dn = (principal.display_name or '').strip()
            if dn and contains_chinese(dn):
                zh_alias, en_alias = chinese_aliases(dn)
                for alias in [zh_alias, en_alias]:
                    if not alias:
                        continue
                    norm = normalize_identity_value(alias, 'alias')
                    exists = session.query(IdentityClaim).filter_by(
                        principal_id=principal.id,
                        platform='manual',
                        normalized=norm
                    ).first()
                    if not exists:
                        alias_claim = IdentityClaim(
                            principal_id=principal.id,
                            platform='manual',
                            kind='alias',
                            value=alias,
                            normalized=norm,
                            confidence=0.75,
                        )
                        session.add(alias_claim)
                        stats['new_identities'] += 1
        except Exception:
            # Do not fail ingestion on alias generation issues
            pass
    
    def _update_existing_contact(self, session, source, contact_data, existing_info, current_hash, stats):
        """Update an existing contact with new information."""
        from memory_database.models import Principal, IdentityClaim
        
        principal_id = existing_info['principal_id']
        principal = session.query(Principal).get(principal_id)
        
        if not principal:
            self.logger.error("Principal not found", principal_id=principal_id)
            return
        
        # Update principal information
        display_name = contact_data.get('display_name')
        if display_name:
            principal.display_name = display_name
        
        # Update extra data
        principal.extra.update({
            'contact_hash': current_hash,
            'given_name': contact_data.get('given_name'),
            'family_name': contact_data.get('family_name'),
            'middle_name': contact_data.get('middle_name'),
            'biography': contact_data.get('biography'),
            'organizations': contact_data.get('organizations', []),
            'addresses': contact_data.get('addresses', []),
            'birthdays': contact_data.get('birthdays', []),
            'urls': contact_data.get('urls', []),
            'relations': contact_data.get('relations', [])
        })
        
        # Get current identity claims
        current_identities = source.extract_identities(contact_data)

        # Build lookup of existing claims by (platform, normalized)
        # Note: This matches the database constraint UNIQUE (principal_id, platform, normalized)
        existing_claims = {(claim.platform, claim.normalized): claim
                          for claim in existing_info['all_claims']}

        # Process new/updated identities
        for identity_data in current_identities:
            # Key is (platform, normalized) to match database constraint
            key = (identity_data['platform'], identity_data['normalized'])

            if key in existing_claims:
                # Update existing claim
                existing_claim = existing_claims[key]
                existing_claim.value = identity_data['value']
                existing_claim.kind = identity_data['kind']  # Update kind if changed
                existing_claim.confidence = identity_data['confidence']
                existing_claim.last_seen = contact_data.get('last_modified') or datetime.utcnow()
                existing_claim.extra = identity_data.get('extra', {})
                stats['updated_identities'] += 1
            else:
                # New identity claim - double-check against database to prevent constraint violations
                db_existing = session.query(IdentityClaim).filter_by(
                    principal_id=principal_id,
                    platform=identity_data['platform'],
                    normalized=identity_data['normalized']
                ).first()

                if db_existing:
                    # Found in database but not in our lookup - update it
                    db_existing.value = identity_data['value']
                    db_existing.kind = identity_data['kind']
                    db_existing.confidence = identity_data['confidence']
                    db_existing.last_seen = contact_data.get('last_modified') or datetime.utcnow()
                    db_existing.extra = identity_data.get('extra', {})
                    stats['updated_identities'] += 1
                else:
                    # Truly new identity claim
                    identity_claim = IdentityClaim(
                        principal_id=principal_id,
                        platform=identity_data['platform'],
                        kind=identity_data['kind'],
                        value=identity_data['value'],
                        normalized=identity_data['normalized'],
                        confidence=identity_data['confidence'],
                        first_seen=contact_data.get('last_modified') or datetime.utcnow(),
                        last_seen=contact_data.get('last_modified') or datetime.utcnow(),
                        extra=identity_data.get('extra', {})
                    )
                    session.add(identity_claim)
                    stats['new_identities'] += 1

        # Ensure Chinese aliases exist after updates
        try:
            dn = (principal.display_name or '').strip()
            if dn and contains_chinese(dn):
                zh_alias, en_alias = chinese_aliases(dn)
                for alias in [zh_alias, en_alias]:
                    if not alias:
                        continue
                    norm = normalize_identity_value(alias, 'alias')
                    db_existing = session.query(IdentityClaim).filter_by(
                        principal_id=principal_id,
                        platform='manual',
                        normalized=norm
                    ).first()
                    if not db_existing:
                        alias_claim = IdentityClaim(
                            principal_id=principal_id,
                            platform='manual',
                            kind='alias',
                            value=alias,
                            normalized=norm,
                            confidence=0.75,
                        )
                        session.add(alias_claim)
                        stats['new_identities'] += 1
        except Exception:
            pass
