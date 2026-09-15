import {
  ApiOutlined,
  AppstoreOutlined,
  CloudUploadOutlined,
  LeftOutlined,
  QuestionCircleOutlined,
  RightOutlined,
  SearchOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { Button, Tooltip } from 'antd'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

export function CADWorkspaceLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const currentKey = location.pathname.split('/')[1] || 'drawings'
  const pathParts = location.pathname.split('/').filter(Boolean)
  const routeDrawingKey = pathParts[0] === 'drawings' && pathParts[1] ? decodeURIComponent(pathParts[1]) : ''
  const commandLabel = routeDrawingKey || (currentKey === 'drawings' ? '图纸库 / CAD 结构化数据' : currentKey === 'uploads' ? '上传图纸 / 处理任务' : 'CAD Knowledge Base')

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark">C</div>
          <div className="brand-name">CAD Intelligence</div>
        </div>

        <div className="titlebar-history" aria-label="页面导航">
          <Tooltip title="后退">
            <Button type="text" icon={<LeftOutlined />} aria-label="后退" onClick={() => navigate(-1)} />
          </Tooltip>
          <Tooltip title="前进">
            <Button type="text" icon={<RightOutlined />} aria-label="前进" onClick={() => navigate(1)} />
          </Tooltip>
        </div>

        <button className="titlebar-command" type="button" onClick={() => navigate('/drawings')} title={commandLabel}>
          <SearchOutlined />
          <span className="titlebar-command-label">{commandLabel}</span>
          <span className="titlebar-command-hint">搜索图纸、实体、问题或命令…</span>
        </button>

        <div className="titlebar-actions">
          <Tooltip title="图纸库">
            <NavLink to="/drawings" className={({ isActive }) => 'titlebar-icon-link' + (isActive ? ' active' : '')} aria-label="图纸库">
              <AppstoreOutlined />
            </NavLink>
          </Tooltip>
          <Tooltip title="上传图纸">
            <NavLink to="/uploads/new" className={({ isActive }) => 'titlebar-icon-link' + (isActive ? ' active' : '')} aria-label="上传图纸">
              <CloudUploadOutlined />
            </NavLink>
          </Tooltip>
          <span className="api-status"><span className="api-status-dot" /> <ApiOutlined /> API 在线</span>
          <Tooltip title="打开 FastAPI 文档">
            <Button type="text" icon={<QuestionCircleOutlined />} aria-label="API 文档" onClick={() => window.open('http://127.0.0.1:8000/docs', '_blank')} />
          </Tooltip>
          <Tooltip title="设置">
            <Button type="text" icon={<SettingOutlined />} aria-label="设置" />
          </Tooltip>
          <div className="user-chip">LS</div>
        </div>
      </header>

      <main className="main-content">
        <Outlet />
      </main>
    </div>
  )
}
