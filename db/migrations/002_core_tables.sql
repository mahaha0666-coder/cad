BEGIN;

CREATE TABLE IF NOT EXISTS core.drawings (
    id                  uuid PRIMARY KEY,
    drawing_key         text NOT NULL UNIQUE,
    title               text NOT NULL,
    drawing_number      text,
    part_number         text,
    drawing_category    text,
    description         text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.drawing_revisions (
    id                    uuid PRIMARY KEY,
    drawing_id            uuid NOT NULL REFERENCES core.drawings(id),
    revision_code         text,
    revision_sequence     integer NOT NULL DEFAULT 1 CHECK (revision_sequence > 0),
    supersedes_id         uuid REFERENCES core.drawing_revisions(id),
    status                text NOT NULL DEFAULT 'imported',
    cad_unit              text,
    unit_scale            double precision,
    schema_version        text,
    cad_coordinate_system text,
    cad_bounds            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (drawing_id, revision_sequence)
);

CREATE TABLE IF NOT EXISTS core.source_assets (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    asset_role            text NOT NULL,
    relative_path         text NOT NULL,
    file_name              text NOT NULL,
    media_type             text,
    file_size              bigint CHECK (file_size IS NULL OR file_size >= 0),
    sha256                 text NOT NULL,
    schema_version         text,
    metadata               jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (drawing_revision_id, asset_role, sha256)
);

CREATE TABLE IF NOT EXISTS core.processing_runs (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    stage                 text NOT NULL,
    tool_name             text NOT NULL,
    tool_version          text,
    run_fingerprint       text NOT NULL,
    config                jsonb NOT NULL DEFAULT '{}'::jsonb,
    statistics            jsonb NOT NULL DEFAULT '{}'::jsonb,
    status                text NOT NULL DEFAULT 'completed',
    is_current            boolean NOT NULL DEFAULT true,
    started_at            timestamptz,
    finished_at           timestamptz,
    error_message         text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (drawing_revision_id, stage, run_fingerprint)
);

COMMIT;
