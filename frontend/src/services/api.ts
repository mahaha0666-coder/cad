import type {
  Asset,
  ChatResponse,
  DrawingSummary,
  ListResponse,
  Revision,
  UnifiedFeature,
  UploadJob,
  ViewerData,
} from '../types/api'

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API_BASE_URL + path, init)
  if (!response.ok) {
    let detail = response.status + ' ' + response.statusText
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export const api = {
  listDrawings: (search = '') =>
    request<ListResponse<DrawingSummary>>('/api/drawings?limit=100' + (search ? '&search=' + encodeURIComponent(search) : '')),
  listRevisions: (drawingKey: string) =>
    request<{ drawing: Record<string, unknown>; items: Revision[]; count: number }>(
      '/api/drawings/' + encodeURIComponent(drawingKey) + '/revisions',
    ),
  getViewer: (drawingKey: string, revisionId?: string) =>
    request<ViewerData>(
      '/api/drawings/' + encodeURIComponent(drawingKey) + '/viewer' + (revisionId ? '?revision_id=' + revisionId : ''),
    ),
  listAssets: (drawingKey: string, revisionId?: string) =>
    request<{ items: Asset[]; count: number }>(
      '/api/drawings/' + encodeURIComponent(drawingKey) + '/assets' + (revisionId ? '?revision_id=' + revisionId : ''),
    ),
  listFeatures: (drawingKey: string, revisionId?: string) =>
    request<ListResponse<UnifiedFeature>>(
      '/api/drawings/' + encodeURIComponent(drawingKey) + '/features?limit=500' + (revisionId ? '&revision_id=' + revisionId : ''),
    ),
  uploadDrawing: (data: { dwg: File; png?: File; drawingKey?: string; title?: string }) => {
    const form = new FormData()
    form.append('dwg', data.dwg)
    if (data.png) form.append('png', data.png)
    if (data.drawingKey) form.append('drawing_key', data.drawingKey)
    if (data.title) form.append('title', data.title)
    return request<UploadJob>('/api/drawings/upload', { method: 'POST', body: form })
  },
  getUploadJob: (jobId: string) =>
    request<UploadJob>('/api/drawings/upload/' + jobId),
  chat: (message: string, drawingKey?: string, sessionId?: string) =>
    request<ChatResponse>('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, drawing_key: drawingKey, session_id: sessionId }),
    }),
}
