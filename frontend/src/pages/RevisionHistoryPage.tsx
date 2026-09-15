import { ArrowLeftOutlined, HistoryOutlined } from '@ant-design/icons'
import { Button, Card, Empty, Table, Tag } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../services/api'

export function RevisionHistoryPage() {
  const { drawingKey = '' } = useParams()
  const navigate = useNavigate()
  const revisions = useQuery({ queryKey: ['revisions', drawingKey], queryFn: () => api.listRevisions(drawingKey), enabled: Boolean(drawingKey) })

  return (
    <div className="page-container history-page">
      <div className="page-heading compact">
        <div>
          <div className="eyebrow">DRAWING / REVISION HISTORY</div>
          <h1><HistoryOutlined /> 版本历史</h1>
          <p>{drawingKey}</p>
        </div>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/drawings/' + drawingKey)}>返回工作台</Button>
      </div>
      <Card className="table-card">
        <Table
          rowKey="revision_id"
          loading={revisions.isLoading}
          dataSource={revisions.data?.items || []}
          locale={{ emptyText: <Empty description="没有版本记录" /> }}
          onRow={(record) => ({ onClick: () => navigate('/drawings/' + drawingKey + '?revision_id=' + record.revision_id) })}
          columns={[
            { title: '版本', dataIndex: 'revision_sequence', render: (value) => <strong>R{value}</strong> },
            { title: '状态', dataIndex: 'status', render: (value) => <Tag color={value === 'failed' ? 'error' : 'success'}>{value}</Tag> },
            { title: '创建时间', dataIndex: 'created_at', render: (value) => value ? new Date(value).toLocaleString('zh-CN') : '—' },
            { title: 'CAD 实体', dataIndex: 'entity_count' },
            { title: 'OCR', dataIndex: 'ocr_item_count' },
            { title: 'Vision', dataIndex: 'vision_observation_count' },
            { title: 'Features', dataIndex: 'unified_feature_count' },
            { title: '资产', dataIndex: 'asset_count' },
          ]}
        />
      </Card>
    </div>
  )
}
