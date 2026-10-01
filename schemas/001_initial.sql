-- SPDX-License-Identifier: AGPL-3.0-only
PRAGMA foreign_keys = ON;
CREATE TABLE documents (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, doi TEXT,
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 created_at TEXT NOT NULL
);
CREATE TABLE files (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 local_path TEXT NOT NULL, UNIQUE(document_id, sha256)
);
CREATE TABLE pages (
 id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES files(id),
 page_number INTEGER NOT NULL CHECK(page_number>0),
 source_revision TEXT NOT NULL,
 transform_json TEXT NOT NULL CHECK(json_valid(transform_json)),
 UNIQUE(file_id, source_revision, page_number)
);
CREATE TABLE blocks (
 id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES pages(id),
 kind TEXT NOT NULL CHECK(kind IN ('text','heading','caption','figure','table','formula')),
 source_text TEXT NOT NULL, reading_order INTEGER NOT NULL,
 left REAL NOT NULL CHECK(left>=0 AND left<1),
 top REAL NOT NULL CHECK(top>=0 AND top<1),
 right REAL NOT NULL CHECK(right>left AND right<=1),
 bottom REAL NOT NULL CHECK(bottom>top AND bottom<=1)
);
CREATE TABLE translations (
 id TEXT PRIMARY KEY, block_id TEXT NOT NULL REFERENCES blocks(id),
 target_language TEXT NOT NULL, model TEXT NOT NULL,
 cache_key TEXT NOT NULL UNIQUE, translated_text TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('draft','validated','failed','reviewed'))
);
CREATE TABLE claims (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
 text TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','supported','insufficient_evidence','reviewed'))
);
CREATE TABLE claim_evidence (
 claim_id TEXT NOT NULL REFERENCES claims(id),
 block_id TEXT NOT NULL REFERENCES blocks(id), quote TEXT NOT NULL,
 PRIMARY KEY(claim_id,block_id)
);
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
 state TEXT NOT NULL CHECK(state IN ('queued','running','paused','completed','failed','cancelled')),
 config_json TEXT NOT NULL CHECK(json_valid(config_json))
);
CREATE TABLE job_chunks (
 job_id TEXT NOT NULL REFERENCES jobs(id), block_id TEXT NOT NULL REFERENCES blocks(id),
 state TEXT NOT NULL CHECK(state IN ('queued','running','completed','failed','cancelled')),
 PRIMARY KEY(job_id,block_id)
);
CREATE TABLE citation_styles (
 id TEXT PRIMARY KEY, version TEXT NOT NULL,
 sha256 TEXT NOT NULL CHECK(length(sha256)=64), content TEXT NOT NULL
);
CREATE TABLE audit_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 point TEXT NOT NULL CHECK(point IN ('document_parsed','model_requested','summary_created','citation_exported','artifact_exported')),
 outcome TEXT NOT NULL CHECK(outcome IN ('succeeded','failed','cancelled')),
 created_at TEXT NOT NULL
);
PRAGMA user_version = 1;
