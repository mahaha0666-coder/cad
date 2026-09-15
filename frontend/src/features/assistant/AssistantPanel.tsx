import { ArrowUpOutlined, BulbOutlined, RobotOutlined, StopOutlined, UserOutlined } from '@ant-design/icons'
import { Avatar, Button, Input, Spin, Tag } from 'antd'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { api } from '../../services/api'
import type { ChatResponse } from '../../types/api'

interface Message {
  role: 'user' | 'assistant'
  content: string
  response?: ChatResponse
}

interface Props {
  drawingKey: string
  onLocate: (evidence: { id?: string; external_id?: string; status?: string }) => void
}

const suggestions = ['当前图纸有哪些半径尺寸？', '列出所有冲突的识别结果', '图纸中有多少个圆？']

export function AssistantPanel({ drawingKey, onLocate }: Props) {
  const [messages, setMessages] = useState<Message[]>([
    { role: 'assistant', content: '你好，我可以基于当前 Revision 查询 CAD 实体、尺寸、OCR 和融合证据。' },
  ])
  const [value, setValue] = useState('')
  const [sessionId, setSessionId] = useState<string>()
  const [loading, setLoading] = useState(false)

  const send = async (message = value) => {
    const question = message.trim()
    if (!question || loading) return
    setValue('')
    setMessages((items) => items.concat({ role: 'user', content: question }))
    setLoading(true)
    try {
      const response = await api.chat(question, drawingKey, sessionId)
      setSessionId(response.session_id)
      setMessages((items) => items.concat({ role: 'assistant', content: response.answer, response }))
    } catch (error) {
      setMessages((items) => items.concat({
        role: 'assistant',
        content: '请求失败：' + (error instanceof Error ? error.message : '无法连接到 CAD Assistant'),
      }))
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="assistant-panel">
      <div className="assistant-heading">
        <div className="assistant-title">
          <Avatar size={32} className="assistant-avatar" icon={<RobotOutlined />} />
          <div>
            <strong>CAD Assistant</strong>
            <span>精确查询 + 工程证据</span>
          </div>
        </div>
        <Tag color="blue">DeepSeek</Tag>
      </div>

      <div className="assistant-messages">
        {messages.map((message, index) => (
          <div key={index} className={'message-row ' + message.role}>
            <Avatar size={24} icon={message.role === 'assistant' ? <RobotOutlined /> : <UserOutlined />} className={message.role === 'assistant' ? 'message-avatar bot' : 'message-avatar user'} />
            <div className="message-content">
              <div className="message-bubble">
                {message.role === 'assistant' ? <ReactMarkdown>{message.content}</ReactMarkdown> : message.content}
              </div>
              {message.response?.evidence && message.response.evidence.length > 0 && (
                <div className="message-evidence">
                  <div className="evidence-mini-title"><BulbOutlined /> 回答引用</div>
                  {message.response.evidence.slice(0, 5).map((item, evidenceIndex) => (
                    <button key={evidenceIndex} className="evidence-mini-row" onClick={() => onLocate(item)}>
                      <span>{item.external_id || item.id.slice(0, 8)}</span>
                      <Tag color={item.status === 'conflict' ? 'error' : 'success'}>{item.status || 'evidence'}</Tag>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {loading && <div className="assistant-loading"><Spin size="small" /> 正在查询当前图纸…</div>}
      </div>

      <div className="assistant-suggestions">
        {suggestions.map((suggestion) => <button key={suggestion} onClick={() => send(suggestion)}>{suggestion}</button>)}
      </div>

      <div className="assistant-input-wrap">
        <Input.TextArea
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onPressEnter={(event) => { if (!event.shiftKey) { event.preventDefault(); void send() } }}
          placeholder="询问当前图纸，例如：R40 对应哪个尺寸标注？"
          autoSize={{ minRows: 2, maxRows: 4 }}
          disabled={loading}
        />
        <Button type="primary" shape="circle" icon={loading ? <StopOutlined /> : <ArrowUpOutlined />} onClick={() => void send()} disabled={!value.trim() || loading} />
      </div>
      <div className="assistant-disclaimer">回答仅基于当前图纸的可追溯数据，工程结论请以原始图纸和人工复核为准。</div>
    </section>
  )
}
