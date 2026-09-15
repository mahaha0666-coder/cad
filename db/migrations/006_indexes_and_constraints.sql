BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS uq_current_processing_run
    ON core.processing_runs (drawing_revision_id, stage)
    WHERE is_current;

CREATE UNIQUE INDEX IF NOT EXISTS uq_current_ocr_run
    ON ocr.runs (drawing_revision_id)
    WHERE is_current;

CREATE UNIQUE INDEX IF NOT EXISTS uq_current_alignment_run
    ON ocr.alignment_runs (drawing_revision_id)
    WHERE is_current;

CREATE UNIQUE INDEX IF NOT EXISTS uq_current_fusion_run
    ON fusion.runs (drawing_revision_id)
    WHERE is_current;

CREATE INDEX IF NOT EXISTS ix_revisions_drawing
    ON core.drawing_revisions (drawing_id);

CREATE INDEX IF NOT EXISTS ix_assets_revision_role
    ON core.source_assets (drawing_revision_id, asset_role);

CREATE INDEX IF NOT EXISTS ix_processing_revision_stage
    ON core.processing_runs (drawing_revision_id, stage);

CREATE INDEX IF NOT EXISTS ix_layers_revision_name
    ON cad.layers (drawing_revision_id, name);

CREATE INDEX IF NOT EXISTS ix_blocks_revision_name
    ON cad.blocks (drawing_revision_id, name);

CREATE INDEX IF NOT EXISTS ix_entities_revision_type
    ON cad.entities (drawing_revision_id, entity_type);

CREATE INDEX IF NOT EXISTS ix_entities_layer
    ON cad.entities (layer_id);

CREATE INDEX IF NOT EXISTS ix_entities_raw_properties
    ON cad.entities USING gin (raw_properties jsonb_path_ops);

CREATE INDEX IF NOT EXISTS ix_circles_radius
    ON cad.circles (radius);

CREATE INDEX IF NOT EXISTS ix_arcs_radius
    ON cad.arcs (radius);

CREATE INDEX IF NOT EXISTS ix_dimensions_type_measurement
    ON cad.dimensions (dimension_type, measurement_raw);

CREATE INDEX IF NOT EXISTS ix_native_annotations_text
    ON cad.native_annotations (drawing_revision_id, text_normalized);

CREATE INDEX IF NOT EXISTS ix_native_annotations_parsed
    ON cad.native_annotations (drawing_revision_id, parsed_kind, parsed_value);

CREATE INDEX IF NOT EXISTS ix_ocr_items_run_text
    ON ocr.items (ocr_run_id, text_normalized);

CREATE INDEX IF NOT EXISTS ix_ocr_items_confidence
    ON ocr.items (ocr_run_id, confidence);

CREATE INDEX IF NOT EXISTS ix_alignment_matches_status
    ON ocr.alignment_matches (alignment_run_id, status);

CREATE INDEX IF NOT EXISTS ix_features_revision_type
    ON fusion.features (drawing_revision_id, feature_type);

CREATE INDEX IF NOT EXISTS ix_features_status
    ON fusion.features (drawing_revision_id, fusion_status);

CREATE INDEX IF NOT EXISTS ix_features_dimension_value
    ON fusion.features (drawing_revision_id, dimension_kind, canonical_value);

CREATE INDEX IF NOT EXISTS ix_conflicts_revision_status
    ON fusion.conflicts (drawing_revision_id, status);

COMMIT;
