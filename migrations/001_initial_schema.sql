-- Initial schema for Messages RAG System
-- Based on the design document sections 2 and 14.2

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "btree_gin";
-- CREATE EXTENSION IF NOT EXISTS "vector";  -- TODO: Enable when implementing vector search

-- Core people tables
CREATE TABLE principal (
  id              TEXT PRIMARY KEY,  -- ULID
  display_name    TEXT,
  org             TEXT,
  created_at      TIMESTAMPTZ DEFAULT now(),
  merged_from     TEXT[],  -- Array of ULIDs
  extra           JSONB DEFAULT '{}'
);

CREATE TABLE identity_claim (
  id            TEXT PRIMARY KEY,  -- ULID
  principal_id  TEXT NOT NULL REFERENCES principal(id) ON DELETE CASCADE,
  platform      TEXT NOT NULL,        -- 'email'|'slack'|'imessage'|'messenger'|'signal'|'contacts'|'oidc'
  kind          TEXT NOT NULL,        -- 'email'|'phone'|'user_id'|'username'|'oidc_sub'|'contact_id'|'pgp_fpr'
  value         TEXT NOT NULL,
  normalized    TEXT,
  confidence    REAL DEFAULT 0.9,
  first_seen    TIMESTAMPTZ DEFAULT now(),
  last_seen     TIMESTAMPTZ DEFAULT now(),
  extra         JSONB DEFAULT '{}'
);

CREATE INDEX identity_claim_value_idx ON identity_claim (platform, kind, normalized);
CREATE INDEX identity_claim_principal_idx ON identity_claim (principal_id);

-- Resolution audit log
CREATE TABLE resolution_event (
  id             TEXT PRIMARY KEY,  -- ULID
  happened_at    TIMESTAMPTZ DEFAULT now(),
  actor          TEXT,                -- 'system' or 'user:<id>'
  action         TEXT NOT NULL,       -- 'merge'|'split'|'block'
  from_principal TEXT,
  to_principal   TEXT,
  reason         TEXT,
  score_snapshot JSONB DEFAULT '{}'
);

-- Communication structures
CREATE TABLE channel (
  id          TEXT PRIMARY KEY,  -- ULID
  platform    TEXT NOT NULL,     -- 'email'|'slack'|'imessage'|'messenger'|'signal'
  name        TEXT,
  channel_id  TEXT,              -- Platform-specific ID
  created_at  TIMESTAMPTZ DEFAULT now(),
  extra       JSONB DEFAULT '{}'
);

CREATE TABLE thread (
  id          TEXT PRIMARY KEY,  -- ULID
  channel_id  TEXT REFERENCES channel(id) ON DELETE CASCADE,
  subject     TEXT,
  started_at  TIMESTAMPTZ,
  last_at     TIMESTAMPTZ,
  thread_id   TEXT,              -- Platform-specific thread ID
  extra       JSONB DEFAULT '{}'
);

CREATE TABLE message (
  id          TEXT PRIMARY KEY,  -- ULID
  thread_id   TEXT NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
  sent_at     TIMESTAMPTZ NOT NULL,
  content     TEXT,
  content_type TEXT DEFAULT 'text/plain',
  message_id  TEXT,              -- Platform-specific message ID
  reply_to    TEXT REFERENCES message(id),
  extra       JSONB DEFAULT '{}'
);

-- Person <-> content linking
CREATE TABLE person_message (
  principal_id TEXT REFERENCES principal(id) ON DELETE CASCADE,
  message_id   TEXT REFERENCES message(id) ON DELETE CASCADE,
  role         TEXT NOT NULL,     -- 'sender'|'recipient'|'mentioned'|'quoted'
  confidence   REAL DEFAULT 1.0,
  PRIMARY KEY (principal_id, message_id, role)
);

-- Media assets
CREATE TABLE media_asset (
  id           TEXT PRIMARY KEY,  -- ULID
  source       TEXT NOT NULL,     -- 'photos'|'scans'|'screenshots'|'videos'
  uri          TEXT NOT NULL,     -- file path or s3://
  captured_at  TIMESTAMPTZ,
  sha256       TEXT,
  width        INT,
  height       INT,
  exif         JSONB DEFAULT '{}',   -- EXIF/IPTC/XMP
  ocr_text     TEXT,
  transcript   TEXT,
  extra        JSONB DEFAULT '{}'
);

CREATE TABLE person_media (
  principal_id TEXT REFERENCES principal(id) ON DELETE CASCADE,
  media_id     TEXT REFERENCES media_asset(id) ON DELETE CASCADE,
  evidence     JSONB DEFAULT '{}',   -- face box hashes, EXIF person tag, filename hint
  confidence   REAL DEFAULT 0.7,
  PRIMARY KEY (principal_id, media_id)
);

-- Document assets
CREATE TABLE document_asset (
  id           TEXT PRIMARY KEY,  -- ULID
  uri          TEXT NOT NULL,
  title        TEXT,
  created_at   TIMESTAMPTZ DEFAULT now(),
  text         TEXT,
  extra        JSONB DEFAULT '{}'
);

CREATE TABLE person_document (
  principal_id TEXT REFERENCES principal(id) ON DELETE CASCADE,
  document_id  TEXT REFERENCES document_asset(id) ON DELETE CASCADE,
  role         TEXT NOT NULL,     -- 'author'|'mentioned'|'recipient'
  confidence   REAL DEFAULT 0.8,
  PRIMARY KEY (principal_id, document_id, role)
);

-- Optional: relationships and events
CREATE TABLE relationship (
  id           TEXT PRIMARY KEY,  -- ULID
  a_id         TEXT NOT NULL REFERENCES principal(id) ON DELETE CASCADE,
  b_id         TEXT NOT NULL REFERENCES principal(id) ON DELETE CASCADE,
  kind         TEXT,              -- 'colleague'|'family'|'advisor'|'client'
  confidence   REAL DEFAULT 0.6,
  since        TIMESTAMPTZ,
  until        TIMESTAMPTZ,
  extra        JSONB DEFAULT '{}',
  CHECK (a_id != b_id)
);

CREATE TABLE person_event (
  id           TEXT PRIMARY KEY,  -- ULID
  principal_id TEXT NOT NULL REFERENCES principal(id) ON DELETE CASCADE,
  happened_at  TIMESTAMPTZ NOT NULL,
  kind         TEXT,              -- 'meeting'|'trip'|'deadline'|'birthday'
  summary      TEXT,
  source_ref   JSONB DEFAULT '{}',    -- pointers to messages/media/docs that support this
  extra        JSONB DEFAULT '{}'
);

-- Message attachments
CREATE TABLE message_attachment (
  id              TEXT PRIMARY KEY,  -- ULID
  message_id      TEXT NOT NULL REFERENCES message(id) ON DELETE CASCADE,
  
  -- File locations
  original_path   TEXT NOT NULL,     -- Original iMessage path
  stored_path     TEXT NOT NULL,     -- Our clone at ~/Memories/attachments/...
  filename        TEXT NOT NULL,
  
  -- Basic metadata
  file_size       BIGINT,
  mime_type       TEXT,
  width           INT,               -- For images/videos
  height          INT,
  duration        REAL,              -- For videos/audio in seconds
  
  -- iMessage reference
  imessage_guid   TEXT NOT NULL,
  imessage_rowid  INT,
  attachment_index INT NOT NULL,     -- Order in message
  
  -- Status
  storage_method  TEXT NOT NULL DEFAULT 'clone',  -- 'clone'|'hardlink'|'copy'
  is_accessible   BOOLEAN DEFAULT true,
  created_at      TIMESTAMPTZ DEFAULT now(),
  
  -- Extra metadata
  extra_metadata  JSONB DEFAULT '{}'
);

-- Chunking and embeddings (for retrieval)
CREATE TABLE chunk (
  id           TEXT PRIMARY KEY,  -- ULID
  source_type  TEXT NOT NULL,     -- 'message'|'document'|'media'
  source_id    TEXT NOT NULL,     -- Points to message/document/media id
  content      TEXT NOT NULL,
  -- embedding    vector(1536),      -- TODO: Add when implementing vector search
  created_at   TIMESTAMPTZ DEFAULT now(),
  participants TEXT[],            -- Array of principal_ids for filtering
  chunk_metadata JSONB DEFAULT '{}'
);

-- Indexes for performance
CREATE INDEX message_sent_at_idx ON message (sent_at);
CREATE INDEX message_thread_idx ON message (thread_id);
CREATE INDEX thread_channel_idx ON thread (channel_id);
CREATE INDEX chunk_source_idx ON chunk (source_type, source_id);
CREATE INDEX chunk_participants_idx ON chunk USING gin(participants);
CREATE INDEX principal_display_name_idx ON principal (display_name);
CREATE INDEX person_event_happened_at_idx ON person_event (happened_at);
CREATE INDEX media_asset_captured_at_idx ON media_asset (captured_at);
CREATE INDEX document_asset_created_at_idx ON document_asset (created_at);
CREATE INDEX message_attachment_message_idx ON message_attachment (message_id);
CREATE INDEX message_attachment_mime_idx ON message_attachment (mime_type);

-- Full text search indexes
CREATE INDEX message_content_fts_idx ON message USING gin(to_tsvector('english', content));
CREATE INDEX document_text_fts_idx ON document_asset USING gin(to_tsvector('english', text));
CREATE INDEX chunk_content_fts_idx ON chunk USING gin(to_tsvector('english', content));