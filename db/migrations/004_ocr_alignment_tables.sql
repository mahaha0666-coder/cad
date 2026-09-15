BEGIN;

CREATE TABLE IF NOT EXISTS ocr.runs (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    image_asset_id        uuid REFERENCES core.source_assets(id),
    engine_name           text NOT NULL,
    engine_version        text,
    backend               text,
    backend_version       text,
    schema_version        text,
    image_width           integer,
    image_height          integer,
    coordinate_system     text,
    preprocessing         jsonb NOT NULL DEFAULT '{}'::jsonb,
    postprocessing        jsonb NOT NULL DEFAULT '{}'::jsonb,
    statistics            jsonb NOT NULL DEFAULT '{}'::jsonb,
    run_fingerprint       text NOT NULL,
    is_current            boolean NOT NULL DEFAULT true,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (drawing_revision_id, run_fingerprint)
);

CREATE TABLE IF NOT EXISTS ocr.items (
    id                    uuid PRIMARY KEY,
    ocr_run_id            uuid NOT NULL REFERENCES ocr.runs(id),
    external_id           text NOT NULL,
    text_raw              text,
    text_normalized       text,
    confidence            double precision CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    parsed_kind           text,
    parsed_value          double precision,
    parsed_unit            text,
    polygon_px            jsonb,
    center_px             jsonb,
    is_merged              boolean NOT NULL DEFAULT false,
    merge_metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (ocr_run_id, external_id)
);

CREATE TABLE IF NOT EXISTS ocr.item_sources (
    merged_item_id        uuid NOT NULL REFERENCES ocr.items(id),
    source_item_id        uuid NOT NULL REFERENCES ocr.items(id),
    ordinal               integer NOT NULL DEFAULT 0,
    PRIMARY KEY (merged_item_id, source_item_id)
);

CREATE TABLE IF NOT EXISTS ocr.alignment_runs (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    native_asset_id       uuid REFERENCES core.source_assets(id),
    ocr_run_id            uuid REFERENCES ocr.runs(id),
    method                text NOT NULL,
    cad_to_image_matrix   double precision[] NOT NULL,
    anchor_count          integer NOT NULL DEFAULT 0,
    inlier_anchor_count   integer NOT NULL DEFAULT 0,
    rmse_px               double precision,
    pixel_content_bounds  jsonb,
    status                text NOT NULL DEFAULT 'registered',
    run_fingerprint       text NOT NULL,
    is_current            boolean NOT NULL DEFAULT true,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (array_length(cad_to_image_matrix, 1) = 9),
    UNIQUE (drawing_revision_id, run_fingerprint)
);

CREATE TABLE IF NOT EXISTS ocr.alignment_anchors (
    id                    uuid PRIMARY KEY,
    alignment_run_id      uuid NOT NULL REFERENCES ocr.alignment_runs(id),
    native_annotation_id  uuid NOT NULL REFERENCES cad.native_annotations(id),
    ocr_item_id           uuid NOT NULL REFERENCES ocr.items(id),
    matched_text          text,
    cad_point             jsonb,
    pixel_point           jsonb,
    is_inlier             boolean,
    error_px              double precision,
    UNIQUE (alignment_run_id, native_annotation_id, ocr_item_id)
);

CREATE TABLE IF NOT EXISTS ocr.alignment_matches (
    id                    uuid PRIMARY KEY,
    alignment_run_id      uuid NOT NULL REFERENCES ocr.alignment_runs(id),
    native_annotation_id  uuid NOT NULL REFERENCES cad.native_annotations(id),
    ocr_item_id           uuid NOT NULL REFERENCES ocr.items(id),
    text_similarity       double precision CHECK (text_similarity IS NULL OR text_similarity BETWEEN 0 AND 1),
    distance_px           double precision,
    score                 double precision CHECK (score IS NULL OR score BETWEEN 0 AND 1),
    status                text NOT NULL,
    UNIQUE (alignment_run_id, native_annotation_id, ocr_item_id)
);

COMMIT;
