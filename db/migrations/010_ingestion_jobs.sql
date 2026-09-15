BEGIN;

CREATE TABLE IF NOT EXISTS core.ingestion_jobs (
    id                uuid PRIMARY KEY,
    drawing_id        uuid NOT NULL REFERENCES core.drawings(id),
    revision_id       uuid NOT NULL REFERENCES core.drawing_revisions(id),
    drawing_key       text NOT NULL,
    status            text NOT NULL DEFAULT 'queued',
    current_stage     text NOT NULL DEFAULT 'queued',
    progress          integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    source_dwg_path   text NOT NULL,
    source_png_path   text,
    statistics        jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message     text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    started_at        timestamptz,
    finished_at       timestamptz,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('queued', 'processing', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_status_created
    ON core.ingestion_jobs (status, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_revision
    ON core.ingestion_jobs (revision_id);

COMMIT;
