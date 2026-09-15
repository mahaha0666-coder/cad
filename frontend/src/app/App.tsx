import { Navigate, Route, Routes } from 'react-router-dom'
import { CADWorkspaceLayout } from '../layouts/CADWorkspaceLayout'
import { DrawingsPage } from '../pages/DrawingsPage'
import { DrawingWorkspacePage } from '../pages/DrawingWorkspacePage'
import { RevisionHistoryPage } from '../pages/RevisionHistoryPage'
import { UploadJobPage } from '../pages/UploadJobPage'

export function App() {
  return (
    <Routes>
      <Route element={<CADWorkspaceLayout />}>
        <Route path="/" element={<Navigate to="/drawings" replace />} />
        <Route path="/drawings" element={<DrawingsPage />} />
        <Route path="/drawings/:drawingKey" element={<DrawingWorkspacePage />} />
        <Route path="/drawings/:drawingKey/history" element={<RevisionHistoryPage />} />
        <Route path="/uploads/new" element={<UploadJobPage />} />
        <Route path="/uploads/:jobId" element={<UploadJobPage />} />
      </Route>
    </Routes>
  )
}
