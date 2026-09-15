export type Point = [number, number, number?]

export interface DrawingSummary {
  drawing_id: string
  drawing_key: string
  title: string
  drawing_number?: string | null
  part_number?: string | null
  drawing_category?: string | null
  revision_id?: string | null
  revision_code?: string | null
  revision_sequence?: number | null
  revision_status?: string | null
  cad_unit?: string | null
  entity_count: number
  native_annotation_count: number
  ocr_item_count: number
  unified_feature_count: number
  vision_observation_count: number
}

export interface Revision {
  revision_id: string
  drawing_id: string
  drawing_key: string
  title: string
  revision_code?: string | null
  revision_sequence: number
  status: string
  cad_unit?: string | null
  unit_scale?: number | null
  schema_version?: string | null
  cad_coordinate_system?: string | null
  cad_bounds?: number[] | null
  created_at?: string
  entity_count: number
  native_annotation_count: number
  ocr_item_count: number
  vision_observation_count: number
  unified_feature_count: number
  semantic_chunk_count: number
  asset_count: number
}

export interface Asset {
  id: string
  drawing_revision_id: string
  asset_role: string
  file_name: string
  media_type?: string | null
  file_size?: number | null
  sha256?: string | null
  schema_version?: string | null
  metadata?: Record<string, unknown>
  created_at?: string
  content_url?: string
}

export interface CADEntity {
  id: string
  cad_id?: string | null
  type: string
  entity_type?: string
  layer?: string | null
  handle?: unknown
  geometry?: Record<string, unknown>
  [key: string]: unknown
}

export interface OCRItem {
  id: string
  external_id: string
  text_raw: string
  text_normalized: string
  confidence?: number | null
  parsed_kind?: string | null
  parsed_value?: number | null
  parsed_unit?: string | null
  polygon_px?: number[][] | null
  center_px?: number[] | null
  image_width?: number | null
  image_height?: number | null
  [key: string]: unknown
}

export interface VisionItem {
  id: string
  external_id?: string
  observation_type?: string
  label?: string | null
  description?: string | null
  confidence?: number | null
  polygon_px?: number[][] | null
  bbox_px?: number[] | null
  [key: string]: unknown
}

export interface LayerItem {
  id: string
  cad_id?: string
  name: string
  entity_count?: number
  [key: string]: unknown
}

export interface BlockItem {
  id: string
  name?: string
  block_name?: string
  entity_count?: number
  [key: string]: unknown
}

export interface EvidenceRef {
  id?: string
  external_id?: string
  cad_id?: string
  entity_type?: string
  text?: string
  role?: string
  polygon_px?: number[][]
  [key: string]: unknown
}

export interface UnifiedFeature {
  id: string
  external_id: string
  feature_type: string
  canonical_text?: string | null
  canonical_text_raw?: string | null
  dimension_kind?: string | null
  canonical_value?: number | null
  unit?: string | null
  cad_position?: number[] | null
  pixel_position?: number[] | null
  projected_position?: number[] | null
  fusion_status: 'confirmed' | 'cad_only' | 'ocr_only' | 'conflict' | 'ambiguous' | string
  fusion_score?: number | null
  text_similarity?: number | null
  distance_px?: number | null
  metadata?: Record<string, unknown>
  cad_evidence_count?: number
  native_evidence_count?: number
  ocr_evidence_count?: number
  vision_evidence_count?: number
  open_conflict_count?: number
  cad_evidence?: EvidenceRef[]
  native_evidence?: EvidenceRef[]
  ocr_evidence?: EvidenceRef[]
  [key: string]: unknown
}

export interface ViewerData {
  drawing: Revision
  assets: Asset[]
  registration: Record<string, unknown>
  browser: { layers: LayerItem[]; blocks: BlockItem[] }
  cad: { entities: CADEntity[] }
  annotations: { native: Record<string, unknown>[]; ocr: OCRItem[]; vision: VisionItem[] }
  features: UnifiedFeature[]
  statistics: Record<string, number>
}

export interface UploadJob {
  job_id: string
  drawing_id: string
  revision_id: string
  drawing_key: string
  status: 'queued' | 'processing' | 'completed' | 'failed' | string
  current_stage: string
  progress: number
  source_dwg_path?: string
  source_png_path?: string | null
  statistics?: Record<string, unknown>
  error_message?: string | null
  status_url?: string
  message?: string
}

export interface ChatResponse {
  session_id: string
  answer: string
  drawing_scope?: Record<string, unknown>
  tool_calls?: { call_id: string; name: string; arguments: Record<string, unknown>; status: string; result_count?: number; error?: string | null }[]
  evidence?: { evidence_type: string; id: string; external_id?: string; status?: string }[]
  warnings?: string[]
}

export interface ListResponse<T> {
  items: T[]
  count: number
  limit?: number
  offset?: number
}
