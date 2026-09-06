/** 霁雪聊天界面：订阅 Bridge、发送消息、展示流式结果。 */

import { useEffect, useReducer, useRef, useState } from 'react'
import { Fragment } from 'react'
import { MessageView } from './MessageView'
import { SubagentView } from './SubagentView'

import type {
  AgentMode,
  BridgeEnvelope,
  McpServerStatus,
  PermissionMode,
  SessionAction,
  SessionInfo,
  SubagentTaskInfo
} from '../../shared/protocol'
import { chatReducer, initialChatState, type UiMessage } from './state'

const PERMISSION_MODE_LABELS: Record<PermissionMode, string> = {
  confirm_edits: '修改需确认',
  ask_all: '每次都询问',
  auto_allow: '自动允许'
}

const MCP_STATUS_LABELS: Record<McpServerStatus, string> = {
  connecting: '连接中',
  connected: '已连接',
  failed: '连接失败'
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
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [sessionError, setSessionError] = useState('')
  const [clock, setClock] = useState(Date.now())
  const sessionRef = useRef('')
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
    if (state.bridge.status === 'ready') void changeSession('current')
  }, [state.bridge.status])

  useEffect(() => {
    if (!state.activeRequestId) return
    const timer = window.setInterval(() => setClock(Date.now()), 100)
    return () => window.clearInterval(timer)
  }, [state.activeRequestId])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [state.messages])

  function handleEvent(event: BridgeEnvelope): void {
    if (event.type === 'subagent.updated') {
      if (event.payload.session_id === sessionRef.current) {
        dispatch({ type: 'subagent_updated', task: event.payload.task as SubagentTaskInfo })
      }
    } else if (event.type === 'error' && event.payload.scope === 'subagent') {
      setSessionError(text(event.payload.message))
    } else if (event.type === 'session.reset') {
      dispatch({ type: 'session_reset' })
      setSessionLoading(true)
    } else if (event.type === 'session.turn') {
      dispatch({ type: 'request_started', requestId: event.request_id,
        text: text(event.payload.text), startedAt: 0 })
    } else if (event.type === 'session.loaded' || event.type === 'session.list') {
      const items = Array.isArray(event.payload.sessions) ? event.payload.sessions : []
      setSessions(items.map((item) => ({ id: text(object(item).id), title: text(object(item).title) })))
      setSessionId(text(event.payload.session_id))
      sessionRef.current = text(event.payload.session_id)
      if (event.type === 'session.loaded') {
        dispatch({ type: 'subagents_loaded', tasks: Array.isArray(event.payload.subagents)
          ? event.payload.subagents as SubagentTaskInfo[] : [] })
        dispatch({ type: 'session_restored', inputTokens: number(event.payload.input_tokens),
          outputTokens: number(event.payload.output_tokens) })
        dispatch({ type: 'model_changed', model: text(event.payload.model) })
        const mode = event.payload.mode
        if (mode === 'plan' || mode === 'do') dispatch({ type: 'mode_changed', mode })
        const permission = event.payload.permission_mode
        if (permission === 'confirm_edits' || permission === 'ask_all' || permission === 'auto_allow') {
          dispatch({ type: 'permission_mode_changed', mode: permission })
        }
        setSessionLoading(false)
      }
    } else if (event.type === 'error' && event.payload.scope === 'session') {
      setSessionError(text(event.payload.message))
      setSessionLoading(false)
    } else if (event.type === 'bridge.ready') {
      dispatch({ type: 'model_changed', model: text(event.payload.model, 'fake-jixue') })
      const mode = text(event.payload.mode)
      if (mode === 'plan' || mode === 'do') dispatch({ type: 'mode_changed', mode })
      const permissionMode = text(event.payload.permission_mode)
      if (
        permissionMode === 'confirm_edits' ||
        permissionMode === 'ask_all' ||
        permissionMode === 'auto_allow'
      ) {
        dispatch({ type: 'permission_mode_changed', mode: permissionMode })
      }
    } else if (event.type === 'mode.changed') {
      const mode = text(event.payload.mode)
      if (mode === 'plan' || mode === 'do') dispatch({ type: 'mode_changed', mode })
    } else if (event.type === 'permission_mode.changed') {
      const mode = text(event.payload.mode)
      if (mode === 'confirm_edits' || mode === 'ask_all' || mode === 'auto_allow') {
        dispatch({ type: 'permission_mode_changed', mode })
      }
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
      if (event.payload.scope === 'storage') setSessionError(text(event.payload.message))
      dispatch({
        type: 'request_failed',
        requestId: event.request_id,
        message: text(event.payload.message, '请求失败')
      })
    }
  }

  const totalUsage = state.subagents.reduce((total, task) => ({
    inputTokens: total.inputTokens + task.usage.input_tokens,
    outputTokens: total.outputTokens + task.usage.output_tokens
  }), state.usage)

  const canSend =
    state.bridge.status === 'ready' && !!sessionId && !sessionLoading &&
    !state.activeRequestId && input.trim().length > 0
  const elapsed = state.startedAt ? (clock - state.startedAt) / 1000 : state.durationMs / 1000
  const connectedMcpCount = state.bridge.mcpServers.filter(
    (server) => server.status === 'connected'
  ).length

  async function changeSession(action: SessionAction, targetId?: string): Promise<void> {
    setSessionLoading(true)
    setSessionError('')
    try {
      await window.jixue.session(action, targetId)
      setInput('')
    } catch (error) {
      setSessionLoading(false)
      setSessionError(error instanceof Error ? error.message : String(error))
    }
  }

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

  async function changePermissionMode(mode: PermissionMode): Promise<void> {
    if (state.activeRequestId || state.permissionMode === mode) return
    try {
      await window.jixue.setPermissionMode(mode)
    } catch (error) {
      console.error('切换权限模式失败', error)
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <h1>霁雪 <span>Jixue</span></h1>
        <p className="sidebar-label">工作区</p>
        <div className="project"><strong>myAgent</strong><small>本地项目</small></div>
        <p className="sidebar-label">对话</p>
        <button className="new-session" onClick={() => void changeSession('new')}
          disabled={!!state.activeRequestId || sessionLoading || state.bridge.status !== 'ready'}>
          ＋ 新建会话
        </button>
        <nav className="session-list" aria-label="会话列表">
          {sessions.map((session) => (
            <button key={session.id} className={session.id === sessionId ? 'current-chat' : ''}
              disabled={!!state.activeRequestId || sessionLoading}
              onClick={() => void changeSession('switch', session.id)} title={session.title}>
              {session.title}
            </button>
          ))}
        </nav>
        {sessionLoading && <small role="status">正在恢复会话…</small>}
        {sessionError && <p className="session-error" role="alert">{sessionError}</p>}
        <div className="bridge-state" data-status={state.bridge.status}>
          <span className="status-dot" />
          <div><strong>Python Bridge</strong><small>{state.bridge.detail}</small></div>
        </div>
        <div className="mcp-panel" aria-label="MCP Server 状态">
          <header>
            <strong>MCP Servers</strong>
            <small>
              {state.bridge.mcpServers.length
                ? connectedMcpCount + '/' + state.bridge.mcpServers.length
                : '未配置'}
            </small>
          </header>
          {state.bridge.mcpServers.map((server) => (
            <div className="mcp-server" data-status={server.status} key={server.name}>
              <span className="mcp-dot" />
              <div title={server.detail}>
                <strong>{server.name}</strong>
                <small>
                  {MCP_STATUS_LABELS[server.status]}
                  {server.status === 'connected' ? ' · ' + server.toolCount + ' 个工具' : ''}
                </small>
              </div>
            </div>
          ))}
        </div>
      </aside>

      <section className="workspace">
        <header className="titlebar">
          <span>myAgent / <strong>{sessions.find((session) => session.id === sessionId)?.title ?? '新会话'}</strong></span>
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
                <Fragment key={message.requestId + '_' + message.id}>
                  <MessageView message={message} onPermission={respondPermission} />
                  {message.role === 'tool' && state.subagents.filter((task) =>
                    task.parent_tool_use_id === message.id && task.request_id === message.requestId
                  ).map((task) => <SubagentView key={task.agent_id} task={task} />)}
                </Fragment>
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
              disabled={state.bridge.status !== 'ready' || sessionLoading || !sessionId}
              rows={2}
            />
            <div className="composer-toolbar">
              <div className="composer-left">
                <div className="mode-switch" aria-label="Agent 模式">
                  <button
                    className={state.mode === 'plan' ? 'active' : ''}
                    onClick={() => void changeMode('plan')}
                    disabled={!!state.activeRequestId || sessionLoading}
                    title="只调查并制定计划，不修改文件"
                  >
                    Plan
                  </button>
                  <button
                    className={state.mode === 'do' ? 'active' : ''}
                    onClick={() => void changeMode('do')}
                    disabled={!!state.activeRequestId || sessionLoading}
                    title="允许 Agent 使用全部已启用工具"
                  >
                    Do
                  </button>
                </div>
                <label htmlFor="permission-mode" className="sr-only">权限模式</label>
                <select
                  id="permission-mode"
                  className="permission-mode-select"
                  value={state.permissionMode}
                  disabled={!!state.activeRequestId || sessionLoading}
                  title="决定哪些工具需要你确认；硬拦截和路径沙箱始终生效"
                  onChange={(event) =>
                    void changePermissionMode(event.target.value as PermissionMode)
                  }
                >
                  <option value="confirm_edits">修改需确认</option>
                  <option value="ask_all">每次都询问</option>
                  <option value="auto_allow">自动允许</option>
                </select>
                <div className="run-status">
                  <span className="model-chip">{state.model}</span>
                  <span>
                    {state.isCancelling
                      ? '正在停止'
                      : state.activeRequestId
                        ? `正在第 ${state.iteration + 1} 轮`
                        : `共 ${state.iteration} 轮`}
                  </span>
                  <span>输入 {totalUsage.inputTokens}</span>
                  <span>输出 {totalUsage.outputTokens}</span>
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
            权限：{PERMISSION_MODE_LABELS[state.permissionMode]} ·
            Enter 发送 · Shift + Enter 换行
          </small>
        </footer>
      </section>
    </main>
  )
}
