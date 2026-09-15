BEGIN;

CREATE TABLE IF NOT EXISTS fusion.runs (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    alignment_run_id      uuid REFERENCES ocr.alignment_runs(id),
    algorithm_name        text NOT NULL,
    algorithm_version     text,
    config                jsonb NOT NULL DEFAULT '{}'::jsonb,
    statistics            jsonb NOT NULL DEFAULT '{}'::jsonb,
    run_fingerprint       text NOT NULL,
    is_current            boolean NOT NULL DEFAULT true,
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (drawing_revision_id, run_fingerprint)
);

CREATE TABLE IF NOT EXISTS fusion.features (
    id                    uuid PRIMARY KEY,
    fusion_run_id         uuid NOT NULL REFERENCES fusion.runs(id),
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    external_id           text NOT NULL,
    feature_type          text NOT NULL,
    canonical_text        text,
    canonical_text_raw    text,
    dimension_kind        text,
    canonical_value       double precision,
    unit                  text,
    cad_position          jsonb,
    pixel_position        jsonb,
    projected_position    jsonb,
    fusion_status         text NOT NULL,
    fusion_score          double precision CHECK (fusion_score IS NULL OR fusion_score BETWEEN 0 AND 1),
    text_similarity       double precision CHECK (text_similarity IS NULL OR text_similarity BETWEEN 0 AND 1),
    distance_px           double precision,
    metadata              jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (fusion_run_id, external_id)
);

CREATE TABLE IF NOT EXISTS fusion.feature_cad_entities (
    feature_id            uuid NOT NULL REFERENCES fusion.features(id),
    entity_id             uuid NOT NULL REFERENCES cad.entities(id),
    evidence_role         text NOT NULL DEFAULT 'source_entity',
    confidence            double precision CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    PRIMARY KEY (feature_id, entity_id, evidence_role)
);

CREATE TABLE IF NOT EXISTS fusion.feature_native_annotations (
    feature_id            uuid NOT NULL REFERENCES fusion.features(id),
    annotation_id         uuid NOT NULL REFERENCES cad.native_annotations(id),
    evidence_role         text NOT NULL DEFAULT 'native_source',
    confidence            double precision CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    PRIMARY KEY (feature_id, annotation_id, evidence_role)
);

CREATE TABLE IF NOT EXISTS fusion.feature_ocr_items (
    feature_id            uuid NOT NULL REFERENCES fusion.features(id),
    ocr_item_id           uuid NOT NULL REFERENCES ocr.items(id),
    evidence_role         text NOT NULL DEFAULT 'image_observation',
    confidence            double precision CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    PRIMARY KEY (feature_id, ocr_item_id, evidence_role)
);

CREATE TABLE IF NOT EXISTS fusion.conflicts (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    feature_id            uuid NOT NULL REFERENCES fusion.features(id),
    conflict_type         text NOT NULL,
    cad_value             jsonb,
    ocr_value             jsonb,
    status                text NOT NULL DEFAULT 'open',
    resolution            text,
    reviewed_by           text,
    reviewed_at           timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (feature_id, conflict_type)
);

COMMIT;
