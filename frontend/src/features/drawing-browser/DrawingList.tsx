import { ArrowRightOutlined, CheckCircleFilled, ClockCircleOutlined, FileImageOutlined, WarningFilled } from '@ant-design/icons'
import { Button, Card, Empty, Skeleton, Tag } from 'antd'
import { useNavigate } from 'react-router-dom'
import type { DrawingSummary } from '../../types/api'

interface Props {
  drawings?: DrawingSummary[]
  loading?: boolean
}

function statusMeta(status?: string | null) {
  if (status === 'processed' || status === 'imported' || status === 'completed') return { label: '已处理', color: 'success', icon: <CheckCircleFilled /> }
  if (status === 'failed') return { label: '失败', color: 'error', icon: <WarningFilled /> }
  return { label: status || '待处理', color: 'processing', icon: <ClockCircleOutlined /> }
}

export function DrawingList({ drawings = [], loading = false }: Props) {
  const navigate = useNavigate()

  if (loading) {
    return <div className="drawing-grid">{[1, 2, 3].map((item) => <Card key={item}><Skeleton active /></Card>)}</div>
  }

  if (!drawings.length) {
    return <Empty description="还没有导入图纸" className="empty-card" />
  }

  return (
    <div className="drawing-grid">
      {drawings.map((drawing) => {
        const status = statusMeta(drawing.revision_status)
        return (
          <Card key={drawing.drawing_id} className="drawing-card" hoverable onClick={() => navigate('/drawings/' + drawing.drawing_key)}>
            <div className="drawing-thumbnail">
              <FileImageOutlined />
              <span>DWG / PNG</span>
            </div>
            <div className="drawing-card-body">
              <div className="drawing-card-title" title={drawing.title}>{drawing.title}</div>
              <div className="drawing-card-key">{drawing.drawing_key}</div>
              <div className="drawing-card-meta">
                <Tag color={status.color} icon={status.icon}>{status.label}</Tag>
                <span>R{drawing.revision_sequence || 1}</span>
              </div>
              <div className="drawing-card-stats">
                <span><strong>{drawing.entity_count}</strong> CAD</span>
                <span><strong>{drawing.ocr_item_count}</strong> OCR</span>
                <span><strong>{drawing.vision_observation_count}</strong> Vision</span>
                <span><strong>{drawing.unified_feature_count}</strong> Features</span>
              </div>
              <Button type="link" className="open-drawing-button" icon={<ArrowRightOutlined />}>打开工作台</Button>
            </div>
          </Card>
        )
      })}
    </div>
  )
}
