import { AimOutlined, MinusOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Space, Tag, Tooltip } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Circle, Group, Image as KonvaImage, Layer, Line, Rect, Stage, Text } from 'react-konva'
import useImage from 'use-image'
import { API_BASE_URL } from '../../services/api'
import { useViewerStore } from '../../stores/viewerStore'
import type { CADEntity, OCRItem, UnifiedFeature, ViewerData, VisionItem } from '../../types/api'

interface Props {
  data: ViewerData
  onSelect: (id: string, kind: 'entity' | 'ocr' | 'vision' | 'feature') => void
}

type Vec2 = [number, number]

function numberValue(value: unknown, fallback = 0) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function pointValue(value: unknown): number[] | null {
  if (!Array.isArray(value) || value.length < 2) return null
  return [numberValue(value[0]), numberValue(value[1]), numberValue(value[2])]
}

function urlFor(path?: string) {
  if (!path) return ''
  return path.startsWith('http') ? path : API_BASE_URL + path
}

function flattenPoints(points: Vec2[]) {
  return points.reduce<number[]>((result, point) => result.concat(point), [])
}

function polygonBounds(polygon?: number[][] | null) {
  if (!polygon || polygon.length === 0) return null
  const xs = polygon.map((point) => numberValue(point[0]))
  const ys = polygon.map((point) => numberValue(point[1]))
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  return { x: minX, y: minY, width: Math.max(1, Math.max(...xs) - minX), height: Math.max(1, Math.max(...ys) - minY) }
}

function arcPoints(center: number[], radius: number, start: number, end: number, count = 36): Vec2[] {
  let startAngle = start
  let endAngle = end
  if (Math.abs(startAngle) <= Math.PI * 2.1 && Math.abs(endAngle) <= Math.PI * 2.1) {
    startAngle *= 180 / Math.PI
    endAngle *= 180 / Math.PI
  }
  while (endAngle < startAngle) endAngle += 360
  const points: Vec2[] = []
  for (let index = 0; index <= count; index += 1) {
    const angle = (startAngle + (endAngle - startAngle) * index / count) * Math.PI / 180
    points.push([center[0] + radius * Math.cos(angle), center[1] + radius * Math.sin(angle)])
  }
  return points
}

function geometryPoints(entity: CADEntity): Vec2[] {
  const geometry = entity.geometry || {}
  const vertices = geometry.vertices || geometry.points || geometry.coordinates
  if (Array.isArray(vertices)) {
    return vertices
      .map((item) => Array.isArray(item) ? item : (item && typeof item === 'object' ? [(item as Record<string, unknown>).x, (item as Record<string, unknown>).y] : null))
      .map(pointValue)
      .filter((point): point is number[] => Boolean(point))
      .map((point) => [point[0], point[1]])
  }
  return []
}

export function CADViewer({ data, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 800, height: 520 })
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState<Vec2>([0, 0])
  const selectedId = useViewerStore((state) => state.selectedId)
  const selectedKind = useViewerStore((state) => state.selectedKind)
  const activeTab = useViewerStore((state) => state.activeTab)
  const showCad = useViewerStore((state) => state.showCad)
  const showOcr = useViewerStore((state) => state.showOcr)
  const showVision = useViewerStore((state) => state.showVision)
  const imageAsset = data.assets.find((asset) => asset.asset_role === 'png') || data.assets.find((asset) => asset.media_type === 'image/png')
  const imageUrl = urlFor(imageAsset?.content_url)
  const [image] = useImage(imageUrl, 'anonymous')

  useEffect(() => {
    const element = containerRef.current
    if (!element) return
    const update = () => setSize({ width: Math.max(320, element.clientWidth), height: Math.max(280, element.clientHeight) })
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const imageWidth = Number(data.annotations.ocr.find((item) => item.image_width)?.image_width || 1920)
  const imageHeight = Number(data.annotations.ocr.find((item) => item.image_height)?.image_height || 1080)
  const bounds = data.drawing.cad_bounds || []
  const matrix = data.registration.cad_to_image_matrix as number[][] | undefined
  const cadScale = useMemo(() => {
    if (matrix?.[0]?.[0] !== undefined && matrix?.[1]?.[0] !== undefined) {
      return Math.sqrt(matrix[0][0] ** 2 + matrix[1][0] ** 2)
    }
    return bounds.length >= 4 ? imageWidth / Math.max(1, bounds[2] - bounds[0]) : 1
  }, [bounds, imageWidth, matrix])

  const fit = Math.min((size.width - 32) / imageWidth, (size.height - 32) / imageHeight) * zoom
  const origin: Vec2 = [
    (size.width - imageWidth * fit) / 2 + pan[0],
    (size.height - imageHeight * fit) / 2 + pan[1],
  ]

  const imagePoint = (point: number[]): Vec2 => [origin[0] + point[0] * fit, origin[1] + point[1] * fit]
  const cadPoint = (point: number[]): Vec2 => {
    if (matrix && matrix.length >= 2 && matrix[0]?.length >= 3 && matrix[1]?.length >= 3) {
      return imagePoint([
        matrix[0][0] * point[0] + matrix[0][1] * point[1] + matrix[0][2],
        matrix[1][0] * point[0] + matrix[1][1] * point[1] + matrix[1][2],
      ])
    }
    if (bounds.length >= 4) {
      return imagePoint([
        (point[0] - bounds[0]) / Math.max(1, bounds[2] - bounds[0]) * imageWidth,
        imageHeight - (point[1] - bounds[1]) / Math.max(1, bounds[3] - bounds[1]) * imageHeight,
      ])
    }
    return imagePoint(point)
  }

  const featureColor = (status: string) => ({
    confirmed: '#36cfc9',
    conflict: '#ff4d4f',
    ambiguous: '#faad14',
    cad_only: '#8aa2b8',
    ocr_only: '#f5c84b',
  }[status] || '#36cfc9')

  const visibleCad = activeTab === 'overview' || activeTab === 'cad'
  const visibleOcr = activeTab === 'overview' || activeTab === 'ocr' || activeTab === 'fusion'
  const visibleVision = activeTab === 'overview' || activeTab === 'vision' || activeTab === 'fusion'
  const visibleFeatures = activeTab === 'overview' || activeTab === 'fusion'

  return (
    <div className="viewer-card">
      <div className="viewer-toolbar">
        <div className="viewer-toolbar-title">
          <span className="live-dot" />
          <span>图纸视图</span>
          <Tag bordered={false}>{data.drawing.cad_unit || '工程坐标'}</Tag>
        </div>
        <Space size={4}>
          <Tooltip title="缩小"><Button type="text" icon={<MinusOutlined />} onClick={() => setZoom((value) => Math.max(0.5, value - 0.1))} /></Tooltip>
          <span className="zoom-readout">{Math.round(zoom * 100)}%</span>
          <Tooltip title="放大"><Button type="text" icon={<PlusOutlined />} onClick={() => setZoom((value) => Math.min(3, value + 0.1))} /></Tooltip>
          <Tooltip title="重置视图"><Button type="text" icon={<ReloadOutlined />} onClick={() => { setZoom(1); setPan([0, 0]) }} /></Tooltip>
          <Tooltip title="适配画布"><Button type="text" icon={<AimOutlined />} onClick={() => { setZoom(1); setPan([0, 0]) }} /></Tooltip>
        </Space>
      </div>
      <div className="viewer-stage" ref={containerRef}>
        {imageUrl ? (
          <Stage
            width={size.width}
            height={size.height}
            draggable
            x={0}
            y={0}
            onDragEnd={(event) => setPan([event.target.x(), event.target.y()])}
          >
            <Layer>
              {image && <KonvaImage image={image} x={origin[0]} y={origin[1]} width={imageWidth * fit} height={imageHeight * fit} />}
              {showCad && visibleCad && data.cad.entities.map((entity) => {
                const geometry = entity.geometry || {}
                const type = entity.type || entity.entity_type
                const isSelected = selectedKind === 'entity' && (selectedId === entity.id || selectedId === entity.cad_id)
                const stroke = isSelected ? '#ffffff' : '#4da7ff'
                const strokeWidth = isSelected ? 3 : 1.2
                const start = pointValue(geometry.start)
                const end = pointValue(geometry.end)
                const center = pointValue(geometry.center)
                if (type === 'LINE' && start && end) {
                  const from = cadPoint(start)
                  const to = cadPoint(end)
                  return <Line key={entity.id} points={[...from, ...to]} stroke={stroke} strokeWidth={strokeWidth} hitStrokeWidth={12} onClick={() => onSelect(entity.id, 'entity')} />
                }
                if ((type === 'CIRCLE' || type === 'ARC') && center) {
                  const radius = numberValue(geometry.radius) * cadScale * fit
                  if (type === 'CIRCLE') {
                    const point = cadPoint(center)
                    return <Circle key={entity.id} x={point[0]} y={point[1]} radius={Math.max(1, radius)} stroke={stroke} strokeWidth={strokeWidth} onClick={() => onSelect(entity.id, 'entity')} />
                  }
                  const points = arcPoints(center, numberValue(geometry.radius), numberValue(geometry.start_angle), numberValue(geometry.end_angle)).map((point) => cadPoint(point))
                  return <Line key={entity.id} points={flattenPoints(points)} stroke={stroke} strokeWidth={strokeWidth} hitStrokeWidth={12} onClick={() => onSelect(entity.id, 'entity')} />
                }
                if (type === 'POLYLINE' || type === 'LWPOLYLINE') {
                  const points = geometryPoints(entity).map((point) => cadPoint(point))
                  if (points.length > 1) {
                    return <Line key={entity.id} points={flattenPoints(points)} closed={Boolean(geometry.closed)} stroke={stroke} strokeWidth={strokeWidth} hitStrokeWidth={12} onClick={() => onSelect(entity.id, 'entity')} />
                  }
                }
                return null
              })}
              {showOcr && visibleOcr && data.annotations.ocr.map((item: OCRItem) => {
                const box = polygonBounds(item.polygon_px)
                if (!box) return null
                const point = imagePoint([box.x, box.y])
                return <Rect key={item.id} x={point[0]} y={point[1]} width={box.width * fit} height={box.height * fit} stroke={selectedKind === 'ocr' && selectedId === item.id ? '#ffffff' : '#f5c84b'} strokeWidth={selectedKind === 'ocr' && selectedId === item.id ? 3 : 1.5} dash={[5, 4]} onClick={() => onSelect(item.id, 'ocr')} />
              })}
              {showVision && visibleVision && data.annotations.vision.map((item: VisionItem) => {
                const box = polygonBounds(item.polygon_px) || (item.bbox_px && { x: item.bbox_px[0], y: item.bbox_px[1], width: item.bbox_px[2], height: item.bbox_px[3] })
                if (!box) return null
                const point = imagePoint([box.x, box.y])
                return <Rect key={item.id} x={point[0]} y={point[1]} width={box.width * fit} height={box.height * fit} stroke={selectedKind === 'vision' && selectedId === item.id ? '#ffffff' : '#9b7cff'} strokeWidth={selectedKind === 'vision' && selectedId === item.id ? 3 : 1.5} dash={[8, 4]} onClick={() => onSelect(item.id, 'vision')} />
              })}
              {visibleFeatures && data.features.map((feature: UnifiedFeature) => {
                if (!feature.pixel_position || feature.pixel_position.length < 2) return null
                const point = imagePoint(feature.pixel_position)
                const active = selectedKind === 'feature' && (selectedId === feature.id || selectedId === feature.external_id)
                return (
                  <Group key={feature.id} x={point[0]} y={point[1]} onClick={() => onSelect(feature.id, 'feature')}>
                    <Circle radius={active ? 8 : 5} fill={featureColor(feature.fusion_status)} opacity={0.95} />
                    <Text x={10} y={-9} text={feature.external_id} fontSize={10} fill={active ? '#ffffff' : featureColor(feature.fusion_status)} />
                  </Group>
                )
              })}
            </Layer>
          </Stage>
        ) : (
          <div className="viewer-empty">
            <span>当前 Revision 没有 PNG 资源</span>
            <small>仍可通过左侧结构树查看 CAD 原生实体</small>
          </div>
        )}
        <div className="viewer-legend">
          <span><i className="legend-dot cad" />CAD</span>
          <span><i className="legend-dot ocr" />OCR</span>
          <span><i className="legend-dot vision" />Vision</span>
          <span><i className="legend-dot fusion" />Fusion</span>
        </div>
      </div>
    </div>
  )
}
