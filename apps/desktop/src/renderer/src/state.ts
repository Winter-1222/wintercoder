import type { BridgeState } from '../../shared/protocol'

export type MessageRole = 'user' | 'assistant'
export type MessageStatus = 'streaming' | 'complete' | 'failed'

export interface UiMessage {
  id: string
  requestId: string
  role: MessageRole
  content: string
  status: MessageStatus
}

export interface TokenUsage {
  inputTokens: number
  outputTokens: number
}

export interface ChatState {
  bridge: BridgeState
  messages: UiMessage[]
  activeRequestId: string | null
  startedAt: number | null
  durationMs: number
  model: string
  usage: TokenUsage
}

export type ChatAction =
  | { type: 'bridge_changed'; state: BridgeState }
  | { type: 'request_started'; requestId: string; text: string; startedAt: number }
  | { type: 'text_received'; requestId: string; messageId: string; text: string }
  | { type: 'usage_received'; inputTokens: number; outputTokens: number }
  | { type: 'request_completed'; requestId: string; durationMs: number; model: string }
  | { type: 'request_failed'; requestId: string; message: string }

export const initialChatState: ChatState = {
  bridge: { status: 'starting', detail: '正在连接 Python Bridge' },
  messages: [],
  activeRequestId: null,
  startedAt: null,
  durationMs: 0,
  model: 'fake-jixue',
  usage: { inputTokens: 0, outputTokens: 0 }
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'bridge_changed':
      return { ...state, bridge: action.state }

    case 'request_started':
      return {
        ...state,
        activeRequestId: action.requestId,
        startedAt: action.startedAt,
        durationMs: 0,
        messages: [
          ...state.messages,
          {
            id: `user_${action.requestId}`,
            requestId: action.requestId,
            role: 'user',
            content: action.text,
            status: 'complete'
          },
          {
            id: `assistant_${action.requestId}`,
            requestId: action.requestId,
            role: 'assistant',
            content: '',
            status: 'streaming'
          }
        ]
      }

    case 'text_received':
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.requestId === action.requestId && message.role === 'assistant'
            ? {
                ...message,
                id: action.messageId,
                content: message.content + action.text
              }
            : message
        )
      }

    case 'usage_received':
      return {
        ...state,
        usage: {
          inputTokens: action.inputTokens,
          outputTokens: action.outputTokens
        }
      }

    case 'request_completed':
      return {
        ...state,
        activeRequestId: null,
        startedAt: null,
        durationMs: action.durationMs,
        model: action.model,
        messages: state.messages.map((message) =>
          message.requestId === action.requestId && message.role === 'assistant'
            ? { ...message, status: 'complete' }
            : message
        )
      }

    case 'request_failed':
      return {
        ...state,
        activeRequestId: null,
        startedAt: null,
        messages: state.messages.map((message) =>
          message.requestId === action.requestId && message.role === 'assistant'
            ? {
                ...message,
                status: 'failed',
                content: message.content || action.message
              }
            : message
        )
      }
  }
}

