import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { BridgeEnvelope } from '../../shared/protocol'
import { chatReducer, initialChatState, type UiMessage } from './state'

function SnowCrystal(): React.JSX.Element {
  return (
    <svg viewBox="0 0 40 40" aria-hidden="true">
      <path d="M20 3v34M5.3 11.5l29.4 17M5.3 28.5l29.4-17" />
      <path d="m16 7 4 4 4-4M16 33l4-4 4 4M7.5 15.8l5.5-1.5-1.5-5.5M32.5 24.2 27 25.7l1.5 5.5M7.5 24.2l5.5 1.5-1.5 5.5M32.5 15.8 27 14.3l1.5-5.5" />
    </svg>
  )
}

function MessageView({ message }: { message: UiMessage }): React.JSX.Element {
  const isAssistant = message.role === 'assistant'
  const isStreaming = isAssistant && message.status === 'streaming'

  return (
    <article className={`message message--${message.role}`}>
      <div className="message__rail" aria-hidden="true" />
      <header className="message__meta">
        <span>{isAssistant ? 'JIXUE / 霁雪' : 'OPERATOR / 你'}</span>
        <span>{message.status === 'streaming' ? 'RECEIVING' : message.status.toUpperCase()}</span>
      </header>
      <div className={`message__body ${isStreaming ? 'message__body--streaming' : ''}`}>
        {isStreaming ? (
          <pre>{message.content || ' '}</pre>
        ) : isAssistant ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        ) : (
          <p>{message.content}</p>
        )}
        {isStreaming && <span className="stream-cursor" aria-label="正在生成" />}
      </div>
    </article>
  )
}

function readNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function readString(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback
}

export default function App(): React.JSX.Element {
  const [state, dispatch] = useReducer(chatReducer, initialChatState)
  const [input, setInput] = useState('')
  const [clock, setClock] = useState(Date.now())
  const conversationEnd = useRef<HTMLDivElement>(null)

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
      disposed = true
      removeStateListener()
      removeEventListener()
    }
  }, [])

  useEffect(() => {
    if (!state.activeRequestId) {
      return
    }
    const timer = window.setInterval(() => setClock(Date.now()), 100)
    return () => window.clearInterval(timer)
  }, [state.activeRequestId])

  useEffect(() => {
    conversationEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [state.messages])

  const elapsedSeconds = useMemo(() => {
    if (state.startedAt !== null) {
      return (clock - state.startedAt) / 1000
    }
    return state.durationMs / 1000
  }, [clock, state.durationMs, state.startedAt])

  const canSend =
    state.bridge.status === 'ready' && state.activeRequestId === null && input.trim().length > 0

  function handleBridgeEvent(event: BridgeEnvelope): void {
    if (event.type === 'stream_text') {
      dispatch({
        type: 'text_received',
        requestId: event.request_id,
        messageId: readString(event.payload.message_id, `assistant_${event.request_id}`),
        text: readString(event.payload.text, '')
      })
      return
    }

    if (event.type === 'usage') {
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
      dispatch({
        type: 'request_completed',
        requestId: event.request_id,
        durationMs: readNumber(event.payload.duration_ms),
        model: readString(event.payload.model, 'fake-jixue')
      })
      return
    }

    if (event.type === 'error') {
      dispatch({
        type: 'request_failed',
        requestId: event.request_id,
        message: readString(event.payload.message, '请求失败')
      })
    }
  }

  async function sendMessage(): Promise<void> {
    const text = input.trim()
    if (!canSend || !text) {
      return
    }

    const requestId = `req_${crypto.randomUUID()}`
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
      <header className="titlebar">
        <div className="brand">
          <div className="brand__mark">
            <SnowCrystal />
          </div>
          <div>
            <p className="eyebrow">WINTER AGENT HARNESS · JX/01</p>
            <h1>霁雪 <em>Jixue</em></h1>
          </div>
        </div>
        <div className="bridge-signal" data-status={state.bridge.status}>
          <span className="bridge-signal__pulse" />
          <div>
            <span>PYTHON BRIDGE</span>
            <strong>{state.bridge.detail}</strong>
          </div>
        </div>
      </header>

      <section className="conversation" aria-label="对话记录">
        {state.messages.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state__index">01</div>
            <div className="empty-state__copy">
              <p className="eyebrow">FIRST LIGHT AFTER SNOW</p>
              <h2>雪停之后，<br />让第一句话落下来。</h2>
              <p>
                当前连接的是不产生费用的 FakeLLM。发送一条消息，
                验证 Electron、Python 与流式事件之间的第一条通路。
              </p>
            </div>
            <div className="empty-state__orbit" aria-hidden="true">
              <span>STREAM</span>
              <span>NDJSON</span>
              <span>READY</span>
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

      <section className="status-ledger" aria-label="运行状态">
        <div className="status-ledger__label">
          <span>LIVE INSTRUMENTS</span>
          <strong>实时仪表</strong>
        </div>
        <dl>
          <div>
            <dt>MODEL</dt>
            <dd>{state.model}</dd>
          </div>
          <div>
            <dt>INPUT</dt>
            <dd>{state.usage.inputTokens}<small> tok</small></dd>
          </div>
          <div>
            <dt>OUTPUT</dt>
            <dd>{state.usage.outputTokens}<small> tok</small></dd>
          </div>
          <div>
            <dt>ELAPSED</dt>
            <dd>{elapsedSeconds.toFixed(1)}<small> s</small></dd>
          </div>
        </dl>
      </section>

      <section className="composer">
        <div className="composer__number">MSG<br />001</div>
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
          placeholder={state.bridge.status === 'ready' ? '在雪面上写下第一句话…' : '等待 Python Bridge…'}
          disabled={state.bridge.status !== 'ready'}
          rows={2}
        />
        <div className="composer__actions">
          <span>ENTER 发送<br />SHIFT + ENTER 换行</span>
          <button type="button" onClick={() => void sendMessage()} disabled={!canSend}>
            <span>发送</span>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 12h14M13 6l6 6-6 6" />
            </svg>
          </button>
        </div>
      </section>
    </main>
  )
}
