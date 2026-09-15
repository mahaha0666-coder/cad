import {
  AppstoreOutlined,
  BranchesOutlined,
  CaretDownOutlined,
  FileImageOutlined,
  HistoryOutlined,
  ApartmentOutlined,
  NumberOutlined,
  NodeIndexOutlined,
  ScanOutlined,
  TableOutlined,
} from '@ant-design/icons'
import { Alert, Button, Divider, Empty, Select, Skeleton, Space, Spin, Switch, Tag, Tooltip } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { AssistantPanel } from '../features/assistant/AssistantPanel'
import { CADViewer } from '../features/cad-viewer/CADViewer'
import { EvidencePanel, type SelectionDetail } from '../features/evidence/EvidencePanel'
import { ProcessingTimeline } from '../features/processing-timeline/ProcessingTimeline'
import { api } from '../services/api'
import { useViewerStore, type ViewerTab } from '../stores/viewerStore'
import type { CADEntity } from '../types/api'

const tabs: { key: ViewerTab; label: string; icon: React.ReactNode }[] = [
  { key: 'overview', label: '概览', icon: <AppstoreOutlined /> },
  { key: 'cad', label: 'CAD', icon: <NodeIndexOutlined /> },
  { key: 'ocr', label: 'OCR', icon: <ScanOutlined /> },
  { key: 'vision', label: 'Vision', icon: <FileImageOutlined /> },
  { key: 'fusion', label: '融合', icon: <BranchesOutlined /> },
]

const entityTypes = ['LINE', 'CIRCLE', 'ARC', 'POLYLINE', 'DIMENSION', 'MTEXT', 'TEXT', 'INSERT']

const DEFAULT_LEFT_PANEL_WIDTH = 274
const DEFAULT_RIGHT_PANEL_WIDTH = 355
const LEFT_PANEL_MIN_WIDTH = 220
const LEFT_PANEL_MAX_WIDTH = 420
const RIGHT_PANEL_MIN_WIDTH = 280
const RIGHT_PANEL_MAX_WIDTH = 500

type ResizeSide = 'left' | 'right'
type ResizeSession = { side: ResizeSide; startX: number; startWidth: number }

function loadPanelWidth(key: string, fallback: number, min: number, max: number) {
  if (typeof window === 'undefined') return fallback
  try {
    const stored = Number(window.localStorage.getItem(key))
    return Number.isFinite(stored) ? clamp(stored, min, max) : fallback
  } catch {
    return fallback
  }
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max)
}

function countTypes(entities: CADEntity[]) {
  return entities.reduce<Record<string, number>>((result, entity) => {
    const type = entity.type || entity.entity_type || 'OTHER'
    result[type] = (result[type] || 0) + 1
    return result
  }, {})
}

function featureStatusColor(status: string) {
  return ({ confirmed: 'success', conflict: 'error', ambiguous: 'warning', cad_only: 'default', ocr_only: 'warning' } as Record<string, string>)[status] || 'processing'
}

export function DrawingWorkspacePage() {
  const { drawingKey = '' } = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const revisionId = searchParams.get('revision_id') || undefined
  const activeTab = useViewerStore((state) => state.activeTab)
  const setActiveTab = useViewerStore((state) => state.setActiveTab)
  const toggleLayer = useViewerStore((state) => state.toggleLayer)
  const showCad = useViewerStore((state) => state.showCad)
  const showOcr = useViewerStore((state) => state.showOcr)
  const showVision = useViewerStore((state) => state.showVision)
  const select = useViewerStore((state) => state.select)
  const selectedId = useViewerStore((state) => state.selectedId)
  const selectedKind = useViewerStore((state) => state.selectedKind)
  const [leftPanelWidth, setLeftPanelWidth] = useState(() => loadPanelWidth('cad-kb.left-panel-width', DEFAULT_LEFT_PANEL_WIDTH, LEFT_PANEL_MIN_WIDTH, LEFT_PANEL_MAX_WIDTH))
  const [rightPanelWidth, setRightPanelWidth] = useState(() => loadPanelWidth('cad-kb.right-panel-width', DEFAULT_RIGHT_PANEL_WIDTH, RIGHT_PANEL_MIN_WIDTH, RIGHT_PANEL_MAX_WIDTH))
  const [viewportWidth, setViewportWidth] = useState(() => (typeof window === 'undefined' ? 1440 : window.innerWidth))
  const workspaceGridRef = useRef<HTMLDivElement>(null)
  const resizeSessionRef = useRef<ResizeSession | null>(null)
  const leftPanelWidthRef = useRef(leftPanelWidth)
  const rightPanelWidthRef = useRef(rightPanelWidth)

  leftPanelWidthRef.current = leftPanelWidth
  rightPanelWidthRef.current = rightPanelWidth

  useEffect(() => {
    const handleWindowResize = () => setViewportWidth(window.innerWidth)
    window.addEventListener('resize', handleWindowResize)
    return () => window.removeEventListener('resize', handleWindowResize)
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem('cad-kb.left-panel-width', String(leftPanelWidth))
      window.localStorage.setItem('cad-kb.right-panel-width', String(rightPanelWidth))
    } catch {
      // Local persistence is optional; the resize interaction still works.
    }
  }, [leftPanelWidth, rightPanelWidth])

  const applyPanelWidth = (side: ResizeSide, nextWidth: number) => {
    const gridWidth = workspaceGridRef.current?.getBoundingClientRect().width || window.innerWidth
    const isRightPanelVisible = window.innerWidth > 920
    const centerMinWidth = window.innerWidth <= 920 ? 340 : window.innerWidth <= 1180 ? 360 : 420
    const minWidth = side === 'left' ? LEFT_PANEL_MIN_WIDTH : RIGHT_PANEL_MIN_WIDTH
    const maxWidth = side === 'left' ? LEFT_PANEL_MAX_WIDTH : RIGHT_PANEL_MAX_WIDTH
    const otherWidth = side === 'left' ? (isRightPanelVisible ? rightPanelWidthRef.current : 0) : leftPanelWidthRef.current
    const availableMax = Math.max(minWidth, gridWidth - centerMinWidth - otherWidth)
    const width = clamp(nextWidth, minWidth, Math.min(maxWidth, availableMax))

    if (side === 'left') {
      leftPanelWidthRef.current = width
      setLeftPanelWidth(width)
    } else {
      rightPanelWidthRef.current = width
      setRightPanelWidth(width)
    }
  }

  useEffect(() => {
    const handlePointerMove = (event: PointerEvent) => {
      const session = resizeSessionRef.current
      if (!session) return
      const delta = event.clientX - session.startX
      applyPanelWidth(session.side, session.startWidth + (session.side === 'left' ? delta : -delta))
    }
    const handlePointerUp = () => {
      resizeSessionRef.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [])

  const beginPanelResize = (side: ResizeSide, event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    resizeSessionRef.current = {
      side,
      startX: event.clientX,
      startWidth: side === 'left' ? leftPanelWidthRef.current : rightPanelWidthRef.current,
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  const handleResizeKeyDown = (side: ResizeSide, event: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 32 : 16
    if (event.key === 'Home') {
      event.preventDefault()
      applyPanelWidth(side, side === 'left' ? DEFAULT_LEFT_PANEL_WIDTH : DEFAULT_RIGHT_PANEL_WIDTH)
      return
    }
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const grows = side === 'left' ? event.key === 'ArrowRight' : event.key === 'ArrowLeft'
    const current = side === 'left' ? leftPanelWidthRef.current : rightPanelWidthRef.current
    applyPanelWidth(side, current + (grows ? step : -step))
  }

  const gridColumns = viewportWidth <= 680
    ? '1fr'
    : viewportWidth <= 920
      ? `${leftPanelWidth}px minmax(340px, 1fr)`
      : `${leftPanelWidth}px minmax(${viewportWidth <= 1180 ? 360 : 420}px, 1fr) ${rightPanelWidth}px`

  const revisions = useQuery({
    queryKey: ['revisions', drawingKey],
    queryFn: () => api.listRevisions(drawingKey),
    enabled: Boolean(drawingKey),
  })
  const viewer = useQuery({
    queryKey: ['viewer', drawingKey, revisionId],
    queryFn: () => api.getViewer(drawingKey, revisionId),
    enabled: Boolean(drawingKey),
  })

  const data = viewer.data
  const counts = useMemo(() => countTypes(data?.cad.entities || []), [data?.cad.entities])
  const selectedObject = useMemo<SelectionDetail | undefined>(() => {
    if (!data || !selectedId || !selectedKind) return undefined
    if (selectedKind === 'feature') {
      const feature = data.features.find((item) => item.id === selectedId || item.external_id === selectedId)
      return feature ? { kind: 'feature', feature } : undefined
    }
    if (selectedKind === 'entity') {
      const entity = data.cad.entities.find((item) => item.id === selectedId || item.cad_id === selectedId || String(item.handle ?? '') === selectedId)
      return entity ? { kind: 'entity', entity } : undefined
    }
    if (selectedKind === 'ocr') {
      const item = data.annotations.ocr.find((candidate) => candidate.id === selectedId || candidate.external_id === selectedId)
      return item ? { kind: 'ocr', item } : undefined
    }
    const item = data.annotations.vision.find((candidate) => candidate.id === selectedId || candidate.external_id === selectedId)
    return item ? { kind: 'vision', item } : undefined
  }, [data, selectedId, selectedKind])

  const locate = (id: string, kind: 'entity' | 'ocr' | 'vision' | 'feature') => select(id, kind)
  const locateEvidence = (item: { id?: string; external_id?: string; status?: string }) => {
    const candidateId = item.id || item.external_id
    if (!candidateId || !data) return
    const feature = data.features.find((candidate) => candidate.id === candidateId || candidate.external_id === candidateId)
    if (feature) return select(feature.id, 'feature')
    const entity = data.cad.entities.find((candidate) => candidate.id === candidateId || candidate.cad_id === candidateId)
    if (entity) return select(entity.id, 'entity')
    const ocr = data.annotations.ocr.find((candidate) => candidate.id === candidateId || candidate.external_id === candidateId)
    if (ocr) return select(ocr.id, 'ocr')
    const vision = data.annotations.vision.find((candidate) => candidate.id === candidateId || candidate.external_id === candidateId)
    if (vision) return select(vision.id, 'vision')
  }

  if (viewer.isLoading) {
    return <div className="center-loading"><Spin size="large" tip="正在加载 Revision Viewer…" /></div>
  }
  if (viewer.isError || !data) {
    return <div className="page-container"><Alert type="error" showIcon message="Viewer 加载失败" description={viewer.error instanceof Error ? viewer.error.message : '请检查 FastAPI 和 PostgreSQL 状态'} /></div>
  }

  return (
    <div className="workbench-page">
      <div className="mode-tabs">
        <div className="mode-context" title={data.drawing.title}>
          <FileImageOutlined />
          <strong>{data.drawing.title}</strong>
        </div>
        <div className="mode-context-divider" />
        {tabs.map((tab) => (
          <button key={tab.key} className={'mode-tab ' + (activeTab === tab.key ? 'active' : '')} onClick={() => setActiveTab(tab.key)}>
            {tab.icon}<span>{tab.label}</span>
          </button>
        ))}
        <div className="mode-tabs-spacer" />
        <span className="revision-status"><span className="status-dot" />R{data.drawing.revision_sequence} · {data.drawing.status}</span>
        <Select
          size="small"
          className="revision-select"
          value={data.drawing.revision_id}
          loading={revisions.isLoading}
          onChange={(value) => setSearchParams(value ? { revision_id: value } : {})}
          options={(revisions.data?.items || []).map((revision) => ({ value: revision.revision_id, label: 'Revision R' + revision.revision_sequence + ' · ' + revision.status }))}
        />
        <Tooltip title="历史版本">
          <Button type="text" size="small" className="history-button" icon={<HistoryOutlined />} aria-label="历史版本" onClick={() => navigate('/drawings/' + drawingKey + '/history')} />
        </Tooltip>
      </div>

      <div ref={workspaceGridRef} className="workspace-grid" style={{ gridTemplateColumns: gridColumns }}>
        <aside className="workspace-left-panel">
          <div className="panel-header">
            <div><span className="panel-kicker">BROWSER</span><strong>图纸结构</strong></div>
            <Tooltip title="刷新 Viewer"><Button type="text" icon={<CaretDownOutlined />} onClick={() => void viewer.refetch()} /></Tooltip>
          </div>
          <div className="scope-card">
            <FileImageOutlined />
            <div><strong>R{data.drawing.revision_sequence} 当前版本</strong><span>{data.assets.length} 个源文件与派生资产</span></div>
          </div>
          <div className="browser-scroll">
            <div className="browser-section">
              <div className="browser-section-heading"><ApartmentOutlined />图层 <span>{data.browser.layers.length}</span></div>
              {data.browser.layers.slice(0, 12).map((layer) => (
                <div className="browser-row" key={layer.id}><span className="visibility-eye" /> <span>{layer.name}</span><em>{layer.entity_count || 0}</em></div>
              ))}
              {!data.browser.layers.length && <div className="browser-empty">无图层记录</div>}
            </div>
            <Divider />
            <div className="browser-section">
              <div className="browser-section-heading"><NodeIndexOutlined />实体类型 <span>{data.cad.entities.length}</span></div>
              {entityTypes.map((type) => (
                <div className="browser-row entity-row" key={type} onClick={() => setActiveTab('cad')}>
                  <span className="entity-bullet" /> <span>{type}</span><em>{counts[type] || 0}</em>
                </div>
              ))}
            </div>
            <Divider />
            <div className="browser-section">
              <div className="browser-section-heading"><NumberOutlined />数据图层</div>
              <div className="layer-toggle-row"><span><span className="legend-dot cad" /> CAD Geometry</span><Switch size="small" checked={showCad} onChange={() => toggleLayer('cad')} /></div>
              <div className="layer-toggle-row"><span><span className="legend-dot ocr" /> OCR Boxes</span><Switch size="small" checked={showOcr} onChange={() => toggleLayer('ocr')} /></div>
              <div className="layer-toggle-row"><span><span className="legend-dot vision" /> Vision Boxes</span><Switch size="small" checked={showVision} onChange={() => toggleLayer('vision')} /></div>
            </div>
            <Divider />
            <div className="browser-section feature-browser">
              <div className="browser-section-heading"><BranchesOutlined />融合特征 <span>{data.features.length}</span></div>
              {data.features.slice(0, 8).map((feature) => (
                <button className={'feature-row ' + (selectedId === feature.id ? 'selected' : '')} key={feature.id} onClick={() => locate(feature.id, 'feature')}>
                  <span className="feature-id">{feature.external_id}</span>
                  <span className="feature-text">{feature.canonical_text || feature.feature_type}</span>
                  <Tag color={featureStatusColor(feature.fusion_status)}>{feature.fusion_status}</Tag>
                </button>
              ))}
            </div>
          </div>
          <div className="left-panel-footer">
            <span>PostgreSQL</span><span>•</span><span>Structured Data</span>
          </div>
          <div
            className="panel-resize-handle panel-resize-handle-left"
            role="separator"
            aria-orientation="vertical"
            aria-label="调整左侧栏宽度"
            aria-valuemin={LEFT_PANEL_MIN_WIDTH}
            aria-valuemax={LEFT_PANEL_MAX_WIDTH}
            aria-valuenow={Math.round(leftPanelWidth)}
            tabIndex={0}
            onPointerDown={(event) => beginPanelResize('left', event)}
            onKeyDown={(event) => handleResizeKeyDown('left', event)}
            onDoubleClick={() => applyPanelWidth('left', DEFAULT_LEFT_PANEL_WIDTH)}
          />
        </aside>

        <section className="workspace-center">
          <CADViewer data={data} onSelect={locate} />
          <ProcessingTimeline viewer={data} />
        </section>

        <aside className="workspace-right-panel">
          <div className="workspace-right-scroll">
            <AssistantPanel drawingKey={drawingKey} onLocate={locateEvidence} />
            <EvidencePanel selection={selectedObject} />
          </div>
          <div
            className="panel-resize-handle panel-resize-handle-right"
            role="separator"
            aria-orientation="vertical"
            aria-label="调整右侧栏宽度"
            aria-valuemin={RIGHT_PANEL_MIN_WIDTH}
            aria-valuemax={RIGHT_PANEL_MAX_WIDTH}
            aria-valuenow={Math.round(rightPanelWidth)}
            tabIndex={0}
            onPointerDown={(event) => beginPanelResize('right', event)}
            onKeyDown={(event) => handleResizeKeyDown('right', event)}
            onDoubleClick={() => applyPanelWidth('right', DEFAULT_RIGHT_PANEL_WIDTH)}
          />
        </aside>
      </div>
    </div>
  )
}
