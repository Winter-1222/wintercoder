/** 霁雪聊天界面：订阅 Bridge、发送消息、展示流式结果。 */

import { useEffect, useReducer, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { AgentMode, BridgeEnvelope } from '../../shared/protocol'
import { chatReducer, initialChatState, type UiMessage } from './state'

function MessageView({
  message,
  onPermission
}: {
  message: UiMessage
  onPermission: (message: UiMessage, allow: boolean) => Promise<void>
}): React.JSX.Element {
  if (message.role === 'tool') {
    const permissionBusy =
      message.permissionStatus === 'allowing' || message.permissionStatus === 'denying'
    const label =
      message.permissionStatus === 'pending'
        ? '需要确认'
        : permissionBusy
          ? '正在提交'
          : message.status === 'streaming'
            ? '工具执行中'
            : message.status === 'failed'
              ? '工具失败'
              : '工具完成'
    const showPermission =
      message.status === 'streaming' &&
      (message.permissionStatus === 'pending' || permissionBusy)

    return (
      <article
        className="tool-message"
        data-status={message.status}
        data-permission={message.permissionStatus}
      >
        <span>{label}</span>
        <div>
          <header>
            <strong>{message.name}</strong>
            {message.durationMs !== undefined && <small>{message.durationMs} 毫秒</small>}
          </header>
          {message.input && (
            <details open={showPermission}>
              <summary>查看输入参数</summary>
              <pre>{message.input}</pre>
            </details>
          )}
          {showPermission ? (
            <div className="permission-panel">
              <div>
                <strong>{message.isDestructive ? '可能修改项目' : '需要你的许可'}</strong>
                <p>{message.permissionReason || '此工具需要确认后才能执行。'}</p>
              </div>
              <div className="permission-actions">
                <button
                  className="permission-deny"
                  aria-label={'拒绝 ' + message.name}
                  disabled={permissionBusy}
                  onClick={() => void onPermission(message, false)}
                >
                  {message.permissionStatus === 'denying' ? '拒绝中…' : '拒绝'}
                </button>
                <button
                  className="permission-allow"
                  aria-label={'允许 ' + message.name}
                  disabled={permissionBusy}
                  onClick={() => void onPermission(message, true)}
                >
                  {message.permissionStatus === 'allowing' ? '允许中…' : '允许'}
                </button>
              </div>
            </div>
          ) : (
            <pre className="tool-output">{message.content}</pre>
          )}
        </div>
      </article>
    )
  }
  if (message.role === 'user') {
    return <article className="user-message">{message.content}</article>
  }
  const complete = message.status === 'complete'
  return (
    <article className="assistant-message">
      <span className="avatar">❄</span>
      <div>
        <header>
          <strong>霁雪</strong>
          <small>
            {message.status === 'cancelled'
              ? '已停止'
              : message.status === 'failed'
                ? '回复中断'
                : complete
                  ? '已完成'
                  : '正在回复'}
          </small>
        </header>
        <div className="message-body">
          {complete ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          ) : (
            <pre>{message.content || ' '}</pre>
          )}
          {!complete && message.status !== 'failed' && <span className="cursor" />}
        </div>
      </div>
    </article>
  )
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function number(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

export default function App(): React.JSX.Element {
  const [state, dispatch] = useReducer(chatReducer, initialChatState)
  const [input, setInput] = useState('')
  const [clock, setClock] = useState(Date.now())
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void window.jixue
      .getBridgeState()
      .then((bridgeState) => dispatch({ type: 'bridge_changed', state: bridgeState }))
    const offState = window.jixue.onBridgeState((bridgeState) =>
      dispatch({ type: 'bridge_changed', state: bridgeState })
    )
    const offEvent = window.jixue.onBridgeEvent(handleEvent)
    return () => {
      offState()
      offEvent()
    }
  }, [])

  useEffect(() => {
    if (!state.activeRequestId) return
    const timer = window.setInterval(() => setClock(Date.now()), 100)
    return () => window.clearInterval(timer)
  }, [state.activeRequestId])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [state.messages])

  function handleEvent(event: BridgeEnvelope): void {
    if (event.type === 'bridge.ready') {
      dispatch({ type: 'model_changed', model: text(event.payload.model, 'fake-jixue') })
      const mode = text(event.payload.mode)
      if (mode === 'plan' || mode === 'do') dispatch({ type: 'mode_changed', mode })
    } else if (event.type === 'mode.changed') {
      const mode = text(event.payload.mode)
      if (mode === 'plan' || mode === 'do') dispatch({ type: 'mode_changed', mode })
    } else if (event.type === 'stream_text') {
      dispatch({
        type: 'text_received',
        requestId: event.request_id,
        messageId: text(event.payload.message_id, `assistant_${event.request_id}`),
        text: text(event.payload.text)
      })
    } else if (event.type === 'usage') {
      const usage = object(event.payload.cumulative)
      dispatch({
        type: 'usage_received',
        inputTokens: number(usage.input_tokens),
        outputTokens: number(usage.output_tokens)
      })
    } else if (event.type === 'tool_use') {
      const input = object(event.payload.input)
      dispatch({
        type: 'tool_received',
        requestId: event.request_id,
        toolUseId: text(event.payload.id, `tool_${event.request_id}`),
        name: text(event.payload.name, '未知工具'),
        input: JSON.stringify(input, null, 2),
        error: text(event.payload.error)
      })
    } else if (event.type === 'permission_request') {
      dispatch({
        type: 'permission_requested',
        requestId: event.request_id,
        toolUseId: text(event.payload.id, 'unknown_tool'),
        reason: text(event.payload.reason, '此工具需要确认后才能执行。'),
        isDestructive: event.payload.is_destructive === true
      })
    } else if (event.type === 'permission.resolved') {
      dispatch({
        type: 'permission_resolved',
        requestId: text(event.payload.target_request_id),
        toolUseId: text(event.payload.tool_use_id),
        allow: event.payload.allow === true,
        accepted: event.payload.accepted === true
      })
    } else if (event.type === 'tool_result') {
      dispatch({
        type: 'tool_completed',
        requestId: event.request_id,
        toolUseId: text(event.payload.id, `tool_${event.request_id}`),
        content: text(event.payload.content, '工具没有返回文本'),
        isError: event.payload.is_error === true,
        durationMs: number(event.payload.duration_ms)
      })
    } else if (event.type === 'turn_complete') {
      dispatch({
        type: 'turn_completed',
        requestId: event.request_id,
        iteration: number(event.payload.iteration)
      })
    } else if (event.type === 'loop_complete') {
      dispatch({
        type: 'loop_completed',
        requestId: event.request_id,
        durationMs: number(event.payload.duration_ms),
        model: text(event.payload.model, state.model),
        isError: event.payload.is_error === true,
        cancelled: event.payload.cancelled === true
      })
    } else if (event.type === 'error') {
      dispatch({
        type: 'request_failed',
        requestId: event.request_id,
        message: text(event.payload.message, '请求失败')
      })
    }
  }

  const canSend =
    state.bridge.status === 'ready' && !state.activeRequestId && input.trim().length > 0
  const elapsed = state.startedAt ? (clock - state.startedAt) / 1000 : state.durationMs / 1000

  async function sendMessage(): Promise<void> {
    const message = input.trim()
    if (!canSend || !message) return
    const requestId = `req_${crypto.randomUUID()}`
    dispatch({ type: 'request_started', requestId, text: message, startedAt: Date.now() })
    setInput('')
    try {
      await window.jixue.sendChat(requestId, message)
    } catch (error) {
      dispatch({
        type: 'request_failed',
        requestId,
        message: error instanceof Error ? error.message : String(error)
      })
    }
  }

  async function cancelMessage(): Promise<void> {
    const requestId = state.activeRequestId
    if (!requestId || state.isCancelling) return
    dispatch({ type: 'cancel_requested', requestId })
    try {
      await window.jixue.cancelChat(requestId)
    } catch (error) {
      console.error('发送取消命令失败', error)
      dispatch({ type: 'cancel_failed', requestId })
    }
  }

  async function respondPermission(message: UiMessage, allow: boolean): Promise<void> {
    if (message.permissionStatus !== 'pending') return
    dispatch({
      type: 'permission_submitted',
      requestId: message.requestId,
      toolUseId: message.id,
      allow
    })
    try {
      await window.jixue.respondPermission(message.requestId, message.id, allow)
    } catch (error) {
      console.error('发送权限决定失败', error)
      dispatch({
        type: 'permission_submit_failed',
        requestId: message.requestId,
        toolUseId: message.id
      })
    }
  }

  async function changeMode(mode: AgentMode): Promise<void> {
    if (state.activeRequestId || state.mode === mode) return
    try {
      await window.jixue.setAgentMode(mode)
    } catch (error) {
      console.error('切换 Agent 模式失败', error)
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <h1>霁雪 <span>Jixue</span></h1>
        <p className="sidebar-label">工作区</p>
        <div className="project"><strong>myAgent</strong><small>本地项目</small></div>
        <p className="sidebar-label">对话</p>
        <div className="current-chat">开始构建霁雪</div>
        <div className="bridge-state" data-status={state.bridge.status}>
          <span className="status-dot" />
          <div><strong>Python Bridge</strong><small>{state.bridge.detail}</small></div>
        </div>
      </aside>

      <section className="workspace">
        <header className="titlebar">
          <span>myAgent / <strong>开始构建霁雪</strong></span>
          <span>{state.model}</span>
        </header>

        <section className="conversation" aria-label="对话记录">
          {state.messages.length === 0 ? (
            <div className="empty-state">
              <div>❄</div>
              <h2>今天想一起做什么？</h2>
              <p>发送一条消息，检查霁雪的流式对话链路。</p>
            </div>
          ) : (
            <div className="message-list">
              {state.messages.map((message) => (
                <MessageView
                  key={message.id}
                  message={message}
                  onPermission={respondPermission}
                />
              ))}
            </div>
          )}
          <div ref={endRef} />
        </section>

        <footer className="composer-dock">
          <div className="composer">
            <label htmlFor="message-input" className="sr-only">输入消息</label>
            <textarea
              id="message-input"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  void sendMessage()
                }
              }}
              placeholder={state.bridge.status === 'ready' ? '给霁雪一个任务…' : '正在连接…'}
              disabled={state.bridge.status !== 'ready'}
              rows={2}
            />
            <div className="composer-toolbar">
              <div className="composer-left">
                <div className="mode-switch" aria-label="Agent 模式">
                  <button
                    className={state.mode === 'plan' ? 'active' : ''}
                    onClick={() => void changeMode('plan')}
                    disabled={!!state.activeRequestId}
                    title="只调查并制定计划，不修改文件"
                  >
                    Plan
                  </button>
                  <button
                    className={state.mode === 'do' ? 'active' : ''}
                    onClick={() => void changeMode('do')}
                    disabled={!!state.activeRequestId}
                    title="允许 Agent 使用全部已启用工具"
                  >
                    Do
                  </button>
                </div>
                <div className="run-status">
                  <span className="model-chip">{state.model}</span>
                  <span>
                    {state.isCancelling
                      ? '正在停止'
                      : state.activeRequestId
                        ? `正在第 ${state.iteration + 1} 轮`
                        : `共 ${state.iteration} 轮`}
                  </span>
                  <span>输入 {state.usage.inputTokens}</span>
                  <span>输出 {state.usage.outputTokens}</span>
                  <span>{elapsed.toFixed(1)} 秒</span>
                </div>
              </div>
              {state.activeRequestId ? (
                <button
                  className="cancel-button"
                  aria-label="停止"
                  title="停止当前任务"
                  onClick={() => void cancelMessage()}
                  disabled={state.isCancelling}
                >
                  {state.isCancelling ? '…' : '■'}
                </button>
              ) : (
                <button aria-label="发送" onClick={() => void sendMessage()} disabled={!canSend}>↑</button>
              )}
            </div>
          </div>
          <small className="composer-note">
            {state.mode === 'plan' ? 'Plan：只调查并给出计划' : 'Do：可执行已启用工具'} ·
            Enter 发送 · Shift + Enter 换行
          </small>
        </footer>
      </section>
    </main>
  )
}
