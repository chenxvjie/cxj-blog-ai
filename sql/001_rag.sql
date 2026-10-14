CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS ai;
CREATE TABLE IF NOT EXISTS ai.document (
 post_id bigint PRIMARY KEY,
 revision bigint NOT NULL CHECK(revision>0),
 title text NOT NULL,
 slug text NOT NULL,
 content_md text NOT NULL,
 content_hash text NOT NULL,
 status text NOT NULL CHECK(status IN ('PUBLISHED','DRAFT','OFFLINE','DELETED')),
 active_revision bigint,
 index_state text NOT NULL DEFAULT 'PENDING',
 model_signature text,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ai.chunk (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 post_id bigint NOT NULL REFERENCES ai.document(post_id),
 revision bigint NOT NULL,
 ordinal integer NOT NULL,
 heading text NOT NULL,
 content text NOT NULL,
 search_vector tsvector NOT NULL,
 embedding vector(1024) NOT NULL,
 UNIQUE(post_id,revision,ordinal)
);
CREATE INDEX IF NOT EXISTS ai_chunk_post_version ON ai.chunk(post_id,revision);
CREATE INDEX IF NOT EXISTS ai_chunk_search ON ai.chunk USING gin(search_vector);
COMMENT ON SCHEMA ai IS 'AI-owned tables. Public blog tables remain managed by Flyway.';
