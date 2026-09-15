import { CheckCircleFilled, ClockCircleOutlined, LoadingOutlined } from '@ant-design/icons'
import { Steps, Tag } from 'antd'
import type { ViewerData } from '../../types/api'

interface Props {
  viewer?: ViewerData
  uploadStatus?: string
  progress?: number
}

export function ProcessingTimeline({ viewer, uploadStatus, progress }: Props) {
  const has = (value: number | undefined) => Boolean(value && value > 0)
  const stages = [
    { title: '文件保存', done: true },
    { title: 'LibreDWG', done: has(viewer?.statistics.entity_count) },
    { title: 'RapidOCR', done: has(viewer?.statistics.ocr_item_count) },
    { title: '坐标对齐', done: Boolean(viewer?.registration?.status) },
    { title: 'Vision', done: has(viewer?.statistics.vision_observation_count), optional: true },
    { title: '融合特征', done: has(viewer?.statistics.feature_count) },
    { title: 'PG / Qdrant', done: has(viewer?.statistics.feature_count) },
  ]
  const finished = stages.filter((stage) => stage.done).length
  const current = uploadStatus === 'processing' ? Math.min(finished, stages.length - 1) : finished

  if (uploadStatus) {
    return (
      <div className="processing-strip">
        <div className="processing-title">
          <span>处理流水线</span>
          <Tag color={uploadStatus === 'failed' ? 'error' : uploadStatus === 'completed' ? 'success' : 'processing'}>
            {uploadStatus}
          </Tag>
          {typeof progress === 'number' && <span className="processing-percent">{progress}%</span>}
        </div>
        <Steps
          size="small"
          current={current}
          items={stages.map((stage) => ({ title: stage.title, status: stage.done ? 'finish' : 'wait' }))}
        />
      </div>
    )
  }

  return (
    <div className="processing-strip">
      <div className="processing-title">
        <span>数据来源链路</span>
        <span className="processing-caption">{finished}/{stages.length} 个阶段已有数据</span>
      </div>
      <div className="stage-row">
        {stages.map((stage) => (
          <div key={stage.title} className={'stage-pill ' + (stage.done ? 'done' : 'pending')}>
            {stage.done ? <CheckCircleFilled /> : stage.optional ? <ClockCircleOutlined /> : <LoadingOutlined />}
            <span>{stage.title}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
