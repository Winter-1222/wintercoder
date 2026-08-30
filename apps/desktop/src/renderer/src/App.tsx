/**
 * 霁雪 Renderer 的根组件。
 *
 * 这个文件负责三类事情：
 * 1. 展示侧栏、对话、状态和输入框。
 * 2. 通过 window.jixue 调用 Preload 暴露的安全桌面 API。
 * 3. 把 Bridge 事件转换成 ChatAction，再交给 chatReducer 更新状态。
 *
 * 它不启动 Python，也不直接使用 Electron ipcRenderer；这些高权限能力被隔离在 Main 和 Preload。
 */

import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { BridgeEnvelope } from '../../shared/protocol'
import { chatReducer, initialChatState, type UiMessage } from './state'

/** 返回项目统一使用的雪花图标；aria-hidden 表示读屏软件无需朗读装饰图形。 */
function SnowCrystal(): React.JSX.Element {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 2.8v18.4M4 7.4l16 9.2M4 16.6l16-9.2" />
      <path d="m9.6 4.7 2.4 2.4 2.4-2.4M9.6 19.3l2.4-2.4 2.4 2.4M5.4 9.8l3.3-.9-.9-3.3M18.6 14.2l-3.3.9.9 3.3M5.4 14.2l3.3.9-.9 3.3M18.6 9.8l-3.3-.9.9-3.3" />
    </svg>
  )
}

/** 返回“新增”图标，目前只用于尚未接入行为的新任务入口。 */
function PlusIcon(): React.JSX.Element {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M10 4v12M4 10h12" />
    </svg>
  )
}

/** 返回工作区文件夹图标。 */
function FolderIcon(): React.JSX.Element {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M2.75 5.5A1.75 1.75 0 0 1 4.5 3.75h3l1.5 1.5h6.5A1.75 1.75 0 0 1 17.25 7v7.25A1.75 1.75 0 0 1 15.5 16h-11a1.75 1.75 0 0 1-1.75-1.75V5.5Z" />
    </svg>
  )
}

/** 返回对话图标。 */
function ChatIcon(): React.JSX.Element {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M4 4.25h12A1.75 1.75 0 0 1 17.75 6v7A1.75 1.75 0 0 1 16 14.75H8l-4.7 2.1.7-3.6A1.75 1.75 0 0 1 2.25 11.5V6A1.75 1.75 0 0 1 4 4.25Z" />
    </svg>
  )
}

/**
 * 根据消息角色和状态渲染一条消息。
 *
 * user 消息显示为右侧气泡；assistant 消息带霁雪头像。
 * assistant 在 streaming 阶段使用 pre 保留原始文本，complete 后才交给 ReactMarkdown。
 */
function MessageView({ message }: { message: UiMessage }): React.JSX.Element {
  const isAssistant = message.role === 'assistant'
  const isStreaming = isAssistant && message.status === 'streaming'

  // 用户消息不需要 Markdown，也没有流式状态，使用更简单的气泡结构即可。
  if (!isAssistant) {
    return (
      <article className="message message--user">
        <div className="message__user-bubble">{message.content}</div>
      </article>
    )
  }

  return (
    <article className="message message--assistant">
      <div className="message__avatar" aria-hidden="true">
        <SnowCrystal />
      </div>
      <div className="message__content">
        <header className="message__meta">
          <strong>霁雪</strong>
          <span>
            {message.status === 'streaming'
              ? '正在回复'
              : message.status === 'failed'
                ? '回复中断'
                : '已完成'}
          </span>
        </header>
        <div className={`message__body ${isStreaming ? 'message__body--streaming' : ''}`}>
          {/* 半截 Markdown 可能语法不完整；生成中显示纯文本，完成后才解析完整 Markdown。 */}
          {isStreaming ? (
            <pre>{message.content || ' '}</pre>
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          )}
          {isStreaming && <span className="stream-cursor" aria-label="正在生成" />}
        </div>
      </div>
    </article>
  )
}

/**
 * 从不可信事件 payload 中安全读取数字。
 * BridgeEnvelope 的 payload 在类型层只能写成 unknown，所以 UI 使用前仍需运行时检查。
 */
function readNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

/** 从不可信事件 payload 中安全读取字符串，类型不对时返回备用值。 */
function readString(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback
}

/**
 * 应用根组件，串起 Bridge 订阅、聊天状态和界面渲染。
 * useReducer 返回当前状态 state 和派发 action 的 dispatch；真正的状态计算在 state.ts。
 */
export default function App(): React.JSX.Element {
  const [state, dispatch] = useReducer(chatReducer, initialChatState)
  const [input, setInput] = useState('')
  const [clock, setClock] = useState(Date.now())
  const conversationEnd = useRef<HTMLDivElement>(null)

  // 组件第一次挂载时读取 Bridge 当前状态，并订阅后续状态和业务事件。
  // 空依赖数组 [] 表示只建立一次订阅；清理函数会在组件卸载时移除监听器。
  useEffect(() => {
    let disposed = false
    void window.jixue.getBridgeState().then((bridgeState) => {
      if (!disposed) {
        dispatch({ type: 'bridge_changed', state: bridgeState })
      }
    })

    const removeStateListener = window.jixue.onBridgeState((bridgeState) => {
      dispatch({ type: 'bridge_changed', state: bridgeState })
    })
    const removeEventListener = window.jixue.onBridgeEvent((event) => handleBridgeEvent(event))

    return () => {
      // StrictMode 在开发环境可能执行挂载检查；完整清理可避免重复订阅和内存泄漏。
      disposed = true
      removeStateListener()
      removeEventListener()
    }
  }, [])

  // 请求运行时每 100ms 更新 clock，促使耗时数字重新计算；请求结束后立刻清理定时器。
  useEffect(() => {
    if (!state.activeRequestId) {
      return
    }
    const timer = window.setInterval(() => setClock(Date.now()), 100)
    return () => window.clearInterval(timer)
  }, [state.activeRequestId])

  // 消息数组变化时把滚动位置移动到末尾，让最新的流式内容保持可见。
  useEffect(() => {
    conversationEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [state.messages])

  // useMemo 缓存派生值：运行中使用当前时间减开始时间，结束后显示后端给出的最终耗时。
  const elapsedSeconds = useMemo(() => {
    if (state.startedAt !== null) {
      return (clock - state.startedAt) / 1000
    }
    return state.durationMs / 1000
  }, [clock, state.durationMs, state.startedAt])

  // 三个条件同时满足才能发送，避免 Bridge 未就绪、并发请求或空消息。
  const canSend =
    state.bridge.status === 'ready' && state.activeRequestId === null && input.trim().length > 0

  /**
   * 把跨进程 BridgeEnvelope 翻译成 Renderer 内部 ChatAction。
   * 这里是“协议层”和“UI 状态层”的转换边界，不直接修改 state。
   */
  function handleBridgeEvent(event: BridgeEnvelope): void {
    if (event.type === 'stream_text') {
      // 每个文本事件只携带一小段内容，reducer 会把它追加到对应 assistant 消息。
      dispatch({
        type: 'text_received',
        requestId: event.request_id,
        messageId: readString(event.payload.message_id, `assistant_${event.request_id}`),
        text: readString(event.payload.text, '')
      })
      return
    }

    if (event.type === 'usage') {
      // payload 是宽泛对象，先确认 turn 真的是对象，再从中安全读取两个 Token 字段。
      const turn =
        typeof event.payload.turn === 'object' && event.payload.turn !== null
          ? (event.payload.turn as Record<string, unknown>)
          : {}
      dispatch({
        type: 'usage_received',
        inputTokens: readNumber(turn.input_tokens),
        outputTokens: readNumber(turn.output_tokens)
      })
      return
    }

    if (event.type === 'turn_complete') {
      // 完成事件会结束计时、解锁发送按钮，并触发最终 Markdown 渲染。
      dispatch({
        type: 'request_completed',
        requestId: event.request_id,
        durationMs: readNumber(event.payload.duration_ms),
        model: readString(event.payload.model, 'fake-jixue')
      })
      return
    }

    if (event.type === 'error') {
      // 可恢复的请求错误进入 reducer；应用本身不退出，用户之后仍可重新发送。
      dispatch({
        type: 'request_failed',
        requestId: event.request_id,
        message: readString(event.payload.message, '请求失败')
      })
    }
  }

  /**
   * 处理发送按钮和 Enter 键：先立即更新本地 UI，再异步把命令交给 Preload。
   * 如果跨进程调用失败，则把异常转换成 request_failed action，而不是让 Promise 错误冒泡。
   */
  async function sendMessage(): Promise<void> {
    const text = input.trim()
    if (!canSend || !text) {
      return
    }

    // 每次点击发送都有独立 requestId，后端返回的所有事件都用它找到正确消息。
    const requestId = `req_${crypto.randomUUID()}`
    // 先派发 request_started，所以即使后端较慢，用户气泡也会立即出现在页面中。
    dispatch({ type: 'request_started', requestId, text, startedAt: Date.now() })
    setInput('')

    try {
      await window.jixue.sendChat(requestId, text)
    } catch (error) {
      dispatch({
        type: 'request_failed',
        requestId,
        message: error instanceof Error ? error.message : String(error)
      })
    }
  }

  return (
    <main className="app-shell">
      {/* 左侧只展示当前工作区、对话和 Bridge 状态；会话管理尚未实现。 */}
      <aside className="sidebar">
        <div className="sidebar__brand">
          <span className="brand-mark"><SnowCrystal /></span>
          <h1>霁雪 <span>Jixue</span></h1>
        </div>

        <div className="new-task" aria-label="新任务入口">
          <PlusIcon />
          <span>新任务</span>
          <kbd>Ctrl N</kbd>
        </div>

        <nav className="sidebar__nav" aria-label="工作区导航">
          <section>
            <p className="sidebar__label">工作区</p>
            <div className="nav-item nav-item--project">
              <FolderIcon />
              <span>
                <strong>myAgent</strong>
                <small>本地项目</small>
              </span>
            </div>
          </section>
          <section>
            <p className="sidebar__label">对话</p>
            <div className="nav-item nav-item--active">
              <ChatIcon />
              <span>开始构建霁雪</span>
            </div>
          </section>
        </nav>

        <div className="sidebar__footer" data-status={state.bridge.status}>
          <span className="status-dot" />
          <div>
            <strong>Python Bridge</strong>
            <small>{state.bridge.detail}</small>
          </div>
        </div>
      </aside>

      <section className="workspace">
        {/* 顶部标题栏允许拖动 Electron 窗口，并显示当前项目、任务和模型。 */}
        <header className="titlebar">
          <div className="breadcrumb">
            <span>myAgent</span>
            <i>/</i>
            <strong>开始构建霁雪</strong>
          </div>
          <div className="titlebar__model">
            <span className="status-dot" data-status={state.bridge.status} />
            {state.model}
          </div>
        </header>

        {/* 没有消息时展示新手提示；有消息后按状态数组顺序渲染。 */}
        <section className="conversation" aria-label="对话记录">
          {state.messages.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state__mark"><SnowCrystal /></div>
              <h2>今天想一起做什么？</h2>
              <p>霁雪已经连接到本地 FakeLLM。先发一条消息，确认桌面端的流式通路。</p>
              <div className="empty-state__hint">
                <span>示例</span>
                介绍一下当前 Agent Harness 的运行链路
              </div>
            </div>
          ) : (
            <div className="message-list">
              {state.messages.map((message) => (
                <MessageView key={message.id} message={message} />
              ))}
            </div>
          )}
          <div ref={conversationEnd} />
        </section>

        {/* 输入区固定在底部，包含文本框、模型/Token/耗时状态和发送按钮。 */}
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
              placeholder={
                state.bridge.status === 'ready' ? '给霁雪一个任务…' : '正在连接 Python Bridge…'
              }
              disabled={state.bridge.status !== 'ready'}
              rows={2}
            />
            <div className="composer__toolbar">
              <div className="run-status" aria-label="运行状态">
                <span className="model-chip">{state.model}</span>
                <span>输入 {state.usage.inputTokens}</span>
                <span>输出 {state.usage.outputTokens}</span>
                <span>{elapsedSeconds.toFixed(1)} 秒</span>
              </div>
              <button type="button" onClick={() => void sendMessage()} disabled={!canSend}>
                <span className="sr-only">发送</span>
                <svg viewBox="0 0 20 20" aria-hidden="true">
                  <path d="M10 15.5v-11M5.5 9 10 4.5 14.5 9" />
                </svg>
              </button>
            </div>
          </div>
          <p className="composer-note">Enter 发送 · Shift + Enter 换行</p>
        </footer>
      </section>
    </main>
  )
}
