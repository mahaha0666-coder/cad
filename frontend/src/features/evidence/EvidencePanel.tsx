import { CheckCircleFilled, ExclamationCircleFilled, LinkOutlined } from '@ant-design/icons'
import { Descriptions, Empty, Tag } from 'antd'
import type { CADEntity, OCRItem, UnifiedFeature, VisionItem } from '../../types/api'

export type SelectionDetail =
  | { kind: 'feature'; feature: UnifiedFeature }
  | { kind: 'entity'; entity: CADEntity }
  | { kind: 'ocr'; item: OCRItem }
  | { kind: 'vision'; item: VisionItem }

interface Props {
  selection?: SelectionDetail
}

const statusMeta: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  confirmed: { label: '已确认', color: 'success', icon: <CheckCircleFilled /> },
  conflict: { label: '存在冲突', color: 'error', icon: <ExclamationCircleFilled /> },
  ambiguous: { label: '待消歧', color: 'warning', icon: <ExclamationCircleFilled /> },
  cad_only: { label: '仅 CAD', color: 'default', icon: <LinkOutlined /> },
  ocr_only: { label: '仅 OCR', color: 'warning', icon: <LinkOutlined /> },
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3)
  if (Array.isArray(value)) return '[' + value.map((item) => displayValue(item)).join(', ') + ']'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function entityGeometry(entity: CADEntity) {
  const geometry = entity.geometry || {}
  const keys = ['start', 'end', 'center', 'radius', 'start_angle', 'end_angle', 'closed', 'vertices', 'points', 'coordinates']
  return keys.filter((key) => key in geometry).map((key) => ({ key, value: geometry[key] }))
}

function FeatureDetails({ feature }: { feature: UnifiedFeature }) {
  const meta = statusMeta[feature.fusion_status] || statusMeta.confirmed
  const value = feature.canonical_value === null || feature.canonical_value === undefined ? '—' : String(Number(feature.canonical_value.toFixed(3)))
  return (
    <>
      <div className="selected-object-heading">
        <div>
          <strong>{feature.canonical_text || feature.external_id}</strong>
          <span>{feature.feature_type}</span>
        </div>
        <Tag icon={meta.icon} color={meta.color}>{meta.label}</Tag>
      </div>
      <Descriptions column={1} size="small" colon={false}>
        <Descriptions.Item label="Feature ID">{feature.external_id}</Descriptions.Item>
        <Descriptions.Item label="类型">{feature.dimension_kind || feature.feature_type}</Descriptions.Item>
        <Descriptions.Item label="数值">{value}{feature.unit ? ' ' + feature.unit : ''}</Descriptions.Item>
        <Descriptions.Item label="融合分数">{feature.fusion_score ? feature.fusion_score.toFixed(3) : '—'}</Descriptions.Item>
        <Descriptions.Item label="CAD 证据">{feature.cad_evidence_count || 0} 个实体</Descriptions.Item>
        <Descriptions.Item label="Native">{feature.native_evidence_count || 0} 个标注</Descriptions.Item>
        <Descriptions.Item label="OCR">{feature.ocr_evidence_count || 0} 个观察</Descriptions.Item>
        <Descriptions.Item label="Vision">{feature.vision_evidence_count || 0} 个观察</Descriptions.Item>
      </Descriptions>
      {feature.ocr_evidence && feature.ocr_evidence.length > 0 && (
        <div className="evidence-source-list">
          <div className="source-list-title">来源引用</div>
          {feature.ocr_evidence.map((item, index) => <div key={index} className="source-row"><span className="source-dot ocr" />{item.external_id || item.text || 'OCR observation'}</div>)}
          {feature.native_evidence?.map((item, index) => <div key={'native-' + index} className="source-row"><span className="source-dot native" />{item.external_id || item.text || 'Native annotation'}</div>)}
        </div>
      )}
    </>
  )
}

function EntityDetails({ entity }: { entity: CADEntity }) {
  const type = entity.type || entity.entity_type || 'CAD_ENTITY'
  const geometry = entityGeometry(entity)
  return (
    <>
      <div className="selected-object-heading">
        <div>
          <strong>{type}</strong>
          <span>CAD 原生几何实体</span>
        </div>
        <Tag color="blue">CAD</Tag>
      </div>
      <Descriptions column={1} size="small" colon={false}>
        <Descriptions.Item label="Entity ID">{entity.id || '—'}</Descriptions.Item>
        <Descriptions.Item label="CAD ID">{entity.cad_id || '—'}</Descriptions.Item>
        <Descriptions.Item label="类型">{type}</Descriptions.Item>
        <Descriptions.Item label="图层">{entity.layer || '—'}</Descriptions.Item>
        <Descriptions.Item label="Handle">{displayValue(entity.handle)}</Descriptions.Item>
      </Descriptions>
      <div className="selected-geometry">
        <div className="source-list-title">几何参数</div>
        {geometry.length ? geometry.map(({ key, value }) => (
          <div className="geometry-row" key={key}><span>{key}</span><code>{displayValue(value)}</code></div>
        )) : <div className="geometry-empty">该实体没有可展示的几何字段</div>}
      </div>
    </>
  )
}

function OcrDetails({ item }: { item: OCRItem }) {
  return (
    <>
      <div className="selected-object-heading">
        <div>
          <strong>{item.text_normalized || item.text_raw || 'OCR 文字'}</strong>
          <span>OCR 文字观察</span>
        </div>
        <Tag color="warning">OCR</Tag>
      </div>
      <Descriptions column={1} size="small" colon={false}>
        <Descriptions.Item label="OCR ID">{item.external_id || item.id}</Descriptions.Item>
        <Descriptions.Item label="原始文字">{item.text_raw || '—'}</Descriptions.Item>
        <Descriptions.Item label="规范文字">{item.text_normalized || '—'}</Descriptions.Item>
        <Descriptions.Item label="置信度">{item.confidence === null || item.confidence === undefined ? '—' : item.confidence.toFixed(3)}</Descriptions.Item>
        <Descriptions.Item label="解析类型">{item.parsed_kind || '—'}</Descriptions.Item>
        <Descriptions.Item label="解析数值">{displayValue(item.parsed_value)}{item.parsed_unit ? ' ' + item.parsed_unit : ''}</Descriptions.Item>
        <Descriptions.Item label="像素中心">{displayValue(item.center_px)}</Descriptions.Item>
      </Descriptions>
    </>
  )
}

function VisionDetails({ item }: { item: VisionItem }) {
  return (
    <>
      <div className="selected-object-heading">
        <div>
          <strong>{item.label || item.observation_type || 'Vision 观察'}</strong>
          <span>{item.description || '视觉模型观察结果'}</span>
        </div>
        <Tag color="purple">Vision</Tag>
      </div>
      <Descriptions column={1} size="small" colon={false}>
        <Descriptions.Item label="Vision ID">{item.external_id || item.id}</Descriptions.Item>
        <Descriptions.Item label="观察类型">{item.observation_type || '—'}</Descriptions.Item>
        <Descriptions.Item label="标签">{item.label || '—'}</Descriptions.Item>
        <Descriptions.Item label="置信度">{item.confidence === null || item.confidence === undefined ? '—' : item.confidence.toFixed(3)}</Descriptions.Item>
        <Descriptions.Item label="像素框">{displayValue(item.bbox_px || item.polygon_px)}</Descriptions.Item>
      </Descriptions>
    </>
  )
}

export function EvidencePanel({ selection }: Props) {
  if (!selection) {
    return (
      <div className="evidence-panel empty-evidence">
        <div className="panel-label">选中对象</div>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="点击图纸上的几何、标注或融合特征查看详情" />
      </div>
    )
  }

  return (
    <div className="evidence-panel">
      <div className="panel-label">选中对象</div>
      {selection.kind === 'feature' && <FeatureDetails feature={selection.feature} />}
      {selection.kind === 'entity' && <EntityDetails entity={selection.entity} />}
      {selection.kind === 'ocr' && <OcrDetails item={selection.item} />}
      {selection.kind === 'vision' && <VisionDetails item={selection.item} />}
    </div>
  )
}
