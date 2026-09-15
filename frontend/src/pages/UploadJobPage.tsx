import { CheckCircleFilled, CloudUploadOutlined, FileImageOutlined, FileOutlined, LoadingOutlined, UploadOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Form, Input, Progress, Spin, Tag, Upload, message } from 'antd'
import type { UploadFile } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../services/api'

const stages = [
  ['queued', '文件保存'],
  ['cad_parse', 'LibreDWG 解析'],
  ['ocr', 'RapidOCR 识别'],
  ['alignment', 'CAD / OCR 对齐'],
  ['vision', 'Vision 分析'],
  ['fusion', '证据融合'],
  ['database', 'PG / Qdrant 导入'],
]

export function UploadJobPage() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const [form] = Form.useForm()
  const [files, setFiles] = useState<UploadFile[]>([])
  const [submitting, setSubmitting] = useState(false)
  const job = useQuery({
    queryKey: ['upload-job', jobId],
    queryFn: () => api.getUploadJob(jobId || ''),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'completed' || status === 'failed' ? false : 2000
    },
  })

  useEffect(() => {
    if (job.data?.status === 'completed') {
      message.success('图纸处理完成，可以进入工作台')
    }
  }, [job.data?.status])

  const onFinish = async (values: { title?: string; drawingKey?: string }) => {
    const dwg = files.find((file) => file.name.toLowerCase().endsWith('.dwg'))?.originFileObj as File | undefined
    const png = files.find((file) => file.name.toLowerCase().endsWith('.png'))?.originFileObj as File | undefined
    if (!dwg) {
      message.error('请至少选择一个 DWG 文件')
      return
    }
    setSubmitting(true)
    try {
      const response = await api.uploadDrawing({ dwg, png, title: values.title, drawingKey: values.drawingKey })
      navigate('/uploads/' + response.job_id)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '上传失败')
    } finally {
      setSubmitting(false)
    }
  }

  const currentStage = job.data?.current_stage || 'queued'
  const stageIndex = Math.max(0, stages.findIndex(([key]) => currentStage.includes(key)))
  const processedStage = stages.map(([key, label], index) => ({ key, label, done: job.data?.status === 'completed' || index < stageIndex, active: job.data?.status === 'processing' && index === stageIndex }))

  if (!jobId) {
    return (
      <div className="page-container upload-page">
        <div className="page-heading compact">
          <div>
            <div className="eyebrow">INGESTION / NEW UPLOAD</div>
            <h1>导入 CAD 图纸</h1>
            <p>DWG 是必需的，PNG 用于 OCR、视觉理解和证据定位。</p>
          </div>
        </div>
        <Card className="upload-card">
          <Form form={form} layout="vertical" onFinish={onFinish}>
            <div className="upload-drop-zone">
              <Upload.Dragger
                multiple
                accept=".dwg,.png"
                fileList={files}
                beforeUpload={() => false}
                onChange={({ fileList }) => setFiles(fileList)}
                showUploadList={{ showRemoveIcon: true }}
              >
                <p className="ant-upload-drag-icon"><CloudUploadOutlined /></p>
                <p className="ant-upload-text">拖拽 DWG / PNG 到这里，或点击选择文件</p>
                <p className="ant-upload-hint">DWG 必填；PNG 可选，建议上传同名渲染图</p>
              </Upload.Dragger>
            </div>
            <div className="upload-fields">
              <Form.Item name="title" label="图纸名称"><Input placeholder="例如：Bracket Assembly" /></Form.Item>
              <Form.Item name="drawingKey" label="Drawing Key"><Input placeholder="留空则根据文件名生成" /></Form.Item>
            </div>
            <Alert showIcon type="info" message="处理任务将在后台执行，页面会每 2 秒刷新一次状态。" />
            <Button htmlType="submit" type="primary" size="large" icon={<UploadOutlined />} loading={submitting} className="submit-upload">开始处理</Button>
          </Form>
        </Card>
      </div>
    )
  }

  if (job.isLoading) return <div className="center-loading"><Spin size="large" /></div>
  if (job.isError) return <div className="page-container"><Alert type="error" message="无法读取上传任务" description={job.error instanceof Error ? job.error.message : 'Unknown error'} /></div>

  return (
    <div className="page-container upload-page">
      <div className="page-heading compact">
        <div>
          <div className="eyebrow">INGESTION / PROCESSING JOB</div>
          <h1><LoadingOutlined /> 正在处理图纸</h1>
          <p>{job.data?.drawing_key}</p>
        </div>
        {job.data?.status === 'completed' && <Button type="primary" onClick={() => navigate('/drawings/' + job.data?.drawing_key)}>进入图纸工作台</Button>}
      </div>
      <Card className="job-card">
        <div className="job-summary">
          <div><span className="job-label">任务状态</span><Tag color={job.data?.status === 'failed' ? 'error' : job.data?.status === 'completed' ? 'success' : 'processing'}>{job.data?.status}</Tag></div>
          <div><span className="job-label">当前阶段</span><strong>{job.data?.current_stage}</strong></div>
          <div><span className="job-label">任务 ID</span><code>{job.data?.job_id}</code></div>
        </div>
        <Progress percent={job.data?.progress || 0} status={job.data?.status === 'failed' ? 'exception' : job.data?.status === 'completed' ? 'success' : 'active'} />
        <div className="upload-stages">
          {processedStage.map((stage) => (
            <div key={stage.key} className={'upload-stage ' + (stage.done ? 'done' : stage.active ? 'active' : '')}>
              {stage.done ? <CheckCircleFilled /> : stage.active ? <LoadingOutlined /> : <FileOutlined />}
              <span>{stage.label}</span>
            </div>
          ))}
        </div>
        {job.data?.status === 'failed' && <Alert type="error" showIcon message="处理失败" description={job.data.error_message || '请查看后端日志'} />}
        {job.data?.statistics && <pre className="job-json">{JSON.stringify(job.data.statistics, null, 2)}</pre>}
      </Card>
      <div className="upload-note"><FileImageOutlined /> 上传的 DWG / PNG 会保存在后端 data/uploads 下，数据库只保存可追溯资产元数据。</div>
    </div>
  )
}
