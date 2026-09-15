BEGIN;

CREATE TABLE IF NOT EXISTS cad.layers (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    cad_id                text NOT NULL,
    name                  text NOT NULL,
    handle_json            jsonb,
    source_index           integer,
    UNIQUE (drawing_revision_id, cad_id)
);

CREATE TABLE IF NOT EXISTS cad.blocks (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    cad_id                text NOT NULL,
    name                  text NOT NULL,
    handle_json            jsonb,
    source_index           integer,
    base_point_xyz         double precision[],
    is_anonymous           boolean,
    block_entity_cad_id    text,
    endblk_entity_cad_id   text,
    layout_cad_id          text,
    UNIQUE (drawing_revision_id, cad_id)
);

CREATE TABLE IF NOT EXISTS cad.entities (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    cad_id                text NOT NULL,
    entity_type           text NOT NULL,
    layer_id              uuid REFERENCES cad.layers(id),
    source_index          integer,
    handle_json            jsonb,
    raw_properties         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (drawing_revision_id, cad_id)
);

CREATE TABLE IF NOT EXISTS cad.block_memberships (
    block_id              uuid NOT NULL REFERENCES cad.blocks(id),
    entity_id             uuid NOT NULL REFERENCES cad.entities(id),
    ordinal               integer NOT NULL DEFAULT 0,
    PRIMARY KEY (block_id, entity_id)
);

CREATE TABLE IF NOT EXISTS cad.lines (
    entity_id             uuid PRIMARY KEY REFERENCES cad.entities(id),
    start_x               double precision NOT NULL,
    start_y               double precision NOT NULL,
    start_z               double precision NOT NULL DEFAULT 0,
    end_x                 double precision NOT NULL,
    end_y                 double precision NOT NULL,
    end_z                 double precision NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cad.circles (
    entity_id             uuid PRIMARY KEY REFERENCES cad.entities(id),
    center_x              double precision NOT NULL,
    center_y              double precision NOT NULL,
    center_z              double precision NOT NULL DEFAULT 0,
    radius                double precision NOT NULL CHECK (radius >= 0)
);

CREATE TABLE IF NOT EXISTS cad.arcs (
    entity_id             uuid PRIMARY KEY REFERENCES cad.entities(id),
    center_x              double precision NOT NULL,
    center_y              double precision NOT NULL,
    center_z              double precision NOT NULL DEFAULT 0,
    radius                double precision NOT NULL CHECK (radius >= 0),
    start_angle           double precision NOT NULL,
    end_angle             double precision NOT NULL
);

CREATE TABLE IF NOT EXISTS cad.polylines (
    entity_id             uuid PRIMARY KEY REFERENCES cad.entities(id),
    is_closed             boolean,
    flags                 integer,
    vertices              jsonb NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS cad.texts (
    entity_id             uuid PRIMARY KEY REFERENCES cad.entities(id),
    text_kind             text NOT NULL,
    text_raw              text,
    position_x            double precision,
    position_y            double precision,
    position_z            double precision DEFAULT 0,
    text_height           double precision
);

CREATE TABLE IF NOT EXISTS cad.dimensions (
    entity_id             uuid PRIMARY KEY REFERENCES cad.entities(id),
    dimension_type        text,
    text_raw              text,
    measurement_raw       double precision,
    text_position_x       double precision,
    text_position_y       double precision,
    text_position_z       double precision DEFAULT 0,
    dimension_block_cad_id text
);

CREATE TABLE IF NOT EXISTS cad.inserts (
    entity_id             uuid PRIMARY KEY REFERENCES cad.entities(id),
    block_name             text,
    block_cad_id           text,
    position_x             double precision,
    position_y             double precision,
    position_z             double precision DEFAULT 0,
    rotation               double precision,
    scale_x                double precision,
    scale_y                double precision,
    scale_z                double precision
);

CREATE TABLE IF NOT EXISTS cad.native_annotations (
    id                    uuid PRIMARY KEY,
    drawing_revision_id   uuid NOT NULL REFERENCES core.drawing_revisions(id),
    external_id           text NOT NULL,
    entity_type           text NOT NULL,
    text_raw              text,
    display_text_raw      text,
    text_clean            text,
    text_normalized       text,
    native_dimension_type text,
    native_measurement    double precision,
    parsed_kind           text,
    parsed_value          double precision,
    parsed_unit            text,
    position_x            double precision,
    position_y            double precision,
    position_z            double precision DEFAULT 0,
    display_entity_cad_id text,
    dimension_block_cad_id text,
    normalizer_version    text,
    metadata              jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (drawing_revision_id, external_id)
);

CREATE TABLE IF NOT EXISTS cad.native_annotation_entities (
    annotation_id         uuid NOT NULL REFERENCES cad.native_annotations(id),
    entity_id             uuid NOT NULL REFERENCES cad.entities(id),
    role                  text NOT NULL,
    PRIMARY KEY (annotation_id, entity_id, role)
);

COMMIT;
