import { CloudUploadOutlined, DatabaseOutlined, ReloadOutlined, SearchOutlined, TableOutlined, AppstoreOutlined } from '@ant-design/icons'
import { Button, Empty, Input, Segmented, Spin, Statistic, message } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DrawingList } from '../features/drawing-browser/DrawingList'
import { api } from '../services/api'

export function DrawingsPage() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [view, setView] = useState<'grid' | 'list'>('grid')
  const drawings = useQuery({ queryKey: ['drawings', search], queryFn: () => api.listDrawings(search) })

  const totalEntities = drawings.data?.items.reduce((sum, item) => sum + item.entity_count, 0) || 0
  const totalOcr = drawings.data?.items.reduce((sum, item) => sum + item.ocr_item_count, 0) || 0
  const totalFeatures = drawings.data?.items.reduce((sum, item) => sum + item.unified_feature_count, 0) || 0

  return (
    <div className="page-container drawings-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">PROJECT / CAD KNOWLEDGE BASE</div>
          <h1>图纸库</h1>
          <p>浏览已解析图纸、版本和可追溯工程特征。</p>
        </div>
        <Button type="primary" size="large" icon={<CloudUploadOutlined />} onClick={() => navigate('/uploads/new')}>上传图纸</Button>
      </div>

      <div className="overview-stats">
        <div className="overview-stat"><span className="stat-icon blue"><DatabaseOutlined /></span><Statistic title="已接入图纸" value={drawings.data?.count || 0} suffix="张" /></div>
        <div className="overview-stat"><span className="stat-icon cyan"><TableOutlined /></span><Statistic title="CAD 实体" value={totalEntities} /></div>
        <div className="overview-stat"><span className="stat-icon yellow"><SearchOutlined /></span><Statistic title="OCR 观察" value={totalOcr} /></div>
        <div className="overview-stat"><span className="stat-icon purple"><AppstoreOutlined /></span><Statistic title="融合特征" value={totalFeatures} /></div>
      </div>

      <div className="library-toolbar">
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索图纸名称或 Drawing Key"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="library-search"
        />
        <div className="library-toolbar-actions">
          <Button icon={<ReloadOutlined />} onClick={() => void drawings.refetch()} loading={drawings.isFetching}>刷新</Button>
          <Segmented value={view} onChange={(value) => setView(value as 'grid' | 'list')} options={[{ value: 'grid', icon: <AppstoreOutlined /> }, { value: 'list', icon: <TableOutlined /> }]} />
        </div>
      </div>

      {drawings.isError && <Empty description="无法连接 FastAPI，请先启动后端服务" />}
      {drawings.isLoading && <div className="center-loading"><Spin size="large" /></div>}
      {!drawings.isLoading && !drawings.isError && <DrawingList drawings={drawings.data?.items} loading={false} />}
      {view === 'list' && drawings.data?.items.length ? (
        <div className="view-mode-note">当前使用卡片布局展示，列表模式将在下一版加入表格列配置。</div>
      ) : null}
      {drawings.isSuccess && drawings.data.count === 0 && <Button onClick={() => message.info('请使用右上角上传图纸开始')} type="link">导入第一张图纸</Button>}
    </div>
  )
}
