BEGIN;

CREATE SCHEMA IF NOT EXISTS vision;

CREATE TABLE IF NOT EXISTS vision.runs (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    image_asset_id        uuid REFERENCES core.source_assets(id),
    model_name            text NOT NULL,
    prompt_version        text NOT NULL,
    detail_level          text,
    image_sha256          text NOT NULL,
    request_config        jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_response          jsonb,
    statistics            jsonb NOT NULL DEFAULT '{}'::jsonb,
    run_fingerprint       text NOT NULL,
    status                text NOT NULL DEFAULT 'completed',
    is_current            boolean NOT NULL DEFAULT true,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (drawing_revision_id, run_fingerprint)
);

CREATE TABLE IF NOT EXISTS vision.observations (
    id                    uuid PRIMARY KEY,
    vision_run_id         uuid NOT NULL REFERENCES vision.runs(id),
    external_id           text NOT NULL,
    observation_type      text NOT NULL,
    description           text NOT NULL,
    region_px             jsonb,
    region_normalized     jsonb,
    candidate_feature     jsonb,
    model_confidence      double precision CHECK (
        model_confidence IS NULL OR model_confidence BETWEEN 0 AND 1
    ),
    status                text NOT NULL DEFAULT 'candidate',
    uncertainty           text,
    properties            jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (vision_run_id, external_id),
    CHECK (status IN ('candidate', 'confirmed', 'conflict', 'ambiguous', 'vision_only'))
);

CREATE TABLE IF NOT EXISTS fusion.feature_vision_observations (
    feature_id            uuid NOT NULL REFERENCES fusion.features(id),
    observation_id        uuid NOT NULL REFERENCES vision.observations(id),
    evidence_role         text NOT NULL DEFAULT 'visual_observation',
    confidence            double precision CHECK (
        confidence IS NULL OR confidence BETWEEN 0 AND 1
    ),
    PRIMARY KEY (feature_id, observation_id, evidence_role)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_current_vision_run
    ON vision.runs (drawing_revision_id)
    WHERE is_current;

CREATE INDEX IF NOT EXISTS ix_vision_runs_revision
    ON vision.runs (drawing_revision_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_vision_observations_run_type
    ON vision.observations (vision_run_id, observation_type);

CREATE INDEX IF NOT EXISTS ix_vision_observations_status
    ON vision.observations (status);

CREATE INDEX IF NOT EXISTS ix_vision_observations_properties
    ON vision.observations USING gin (properties jsonb_path_ops);

CREATE INDEX IF NOT EXISTS ix_feature_vision_observations_observation
    ON fusion.feature_vision_observations (observation_id);

COMMIT;
