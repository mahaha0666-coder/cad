BEGIN;

CREATE SCHEMA IF NOT EXISTS semantic;

CREATE TABLE IF NOT EXISTS semantic.index_runs (
    id                    uuid PRIMARY KEY,
    collection_name       text NOT NULL,
    embedding_model       text NOT NULL,
    embedding_dimensions  integer NOT NULL CHECK (embedding_dimensions > 0),
    run_fingerprint       text NOT NULL UNIQUE,
    statistics            jsonb NOT NULL DEFAULT '{}'::jsonb,
    status                text NOT NULL DEFAULT 'running',
    error_message         text,
    started_at            timestamptz NOT NULL DEFAULT now(),
    finished_at           timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS semantic.chunks (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    source_type           text NOT NULL,
    source_id             uuid,
    source_external_id    text NOT NULL,
    chunk_kind            text NOT NULL,
    content               text NOT NULL,
    content_hash          text NOT NULL,
    metadata              jsonb NOT NULL DEFAULT '{}'::jsonb,
    vector_point_id       uuid NOT NULL UNIQUE,
    embedding_model       text NOT NULL,
    last_index_run_id     uuid REFERENCES semantic.index_runs(id),
    is_current            boolean NOT NULL DEFAULT true,
    indexed_at            timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (drawing_revision_id, source_type, source_external_id),
    CHECK (source_type IN ('drawing_revision', 'unified_feature')),
    CHECK (length(btrim(content)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_semantic_chunks_revision_current
    ON semantic.chunks (drawing_revision_id, is_current);

CREATE INDEX IF NOT EXISTS ix_semantic_chunks_source
    ON semantic.chunks (source_type, source_id);

CREATE INDEX IF NOT EXISTS ix_semantic_chunks_metadata
    ON semantic.chunks USING gin (metadata jsonb_path_ops);

COMMIT;
