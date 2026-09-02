/** React 聊天状态；reducer 只做“旧状态 + 事件 = 新状态”。 */

import type { BridgeState } from '../../shared/protocol'

export interface UiMessage {
  id: string
  requestId: string
  role: 'user' | 'assistant' | 'tool'
  content: string
  status: 'streaming' | 'complete' | 'failed'
  name?: string
  input?: string
  durationMs?: number
}

export interface ChatState {
  bridge: BridgeState
  messages: UiMessage[]
  activeRequestId: string | null
  startedAt: number | null
  durationMs: number
  iteration: number
  model: string
  usage: { inputTokens: number; outputTokens: number }
}

export type ChatAction =
  | { type: 'bridge_changed'; state: BridgeState }
  | { type: 'model_changed'; model: string }
  | { type: 'request_started'; requestId: string; text: string; startedAt: number }
  | { type: 'text_received'; requestId: string; messageId: string; text: string }
  | {
      type: 'tool_received'
      requestId: string
      toolUseId: string
      name: string
      input: string
      error: string
    }
  | {
      type: 'tool_completed'
      requestId: string
      toolUseId: string
      content: string
      isError: boolean
      durationMs: number
    }
  | { type: 'usage_received'; inputTokens: number; outputTokens: number }
  | { type: 'turn_completed'; requestId: string; iteration: number }
  | {
      type: 'loop_completed'
      requestId: string
      durationMs: number
      model: string
      isError: boolean
    }
  | { type: 'request_failed'; requestId: string; message: string }

export const initialChatState: ChatState = {
  bridge: { status: 'starting', detail: '正在连接 Python Bridge' },
  messages: [],
  activeRequestId: null,
  startedAt: null,
  durationMs: 0,
  iteration: 0,
  model: 'fake-jixue',
  usage: { inputTokens: 0, outputTokens: 0 }
}

function updateAssistant(
  messages: UiMessage[],
  requestId: string,
  update: (message: UiMessage) => UiMessage
): UiMessage[] {
  return messages.map((message) =>
    message.requestId === requestId && message.role === 'assistant' ? update(message) : message
  )
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'bridge_changed':
      // Python 进程的启动、在线或错误状态变化时，只替换 Bridge 状态。
      return { ...state, bridge: action.state }
    case 'model_changed':
      // 握手成功后，把后端实际使用的模型名显示到状态栏。
      return { ...state, model: action.model }
    case 'request_started':
      // 先放入用户消息和空的 AI 消息，后续流式文字会追加到这个空位置。
      return {
        ...state,
        activeRequestId: action.requestId,
        startedAt: action.startedAt,
        durationMs: 0,
        iteration: 0,
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
      // 每个 stream_text 只是一小段，必须接到已有内容末尾，不能覆盖前文。
      return {
        ...state,
        messages: updateAssistant(state.messages, action.requestId, (message) => ({
          ...message,
          id: action.messageId,
          content: message.content + action.text
        }))
      }
    case 'tool_received':
      // 先记录模型想调用什么；真正的结果会由后续 tool_completed 更新进来。
      return {
        ...state,
        messages: [
          ...state.messages,
          {
            id: action.toolUseId,
            requestId: action.requestId,
            role: 'tool',
            name: action.name,
            input: action.input,
            content: action.error || '等待工具执行…',
            status: action.error ? 'failed' : 'streaming'
          }
        ]
      }
    case 'tool_completed':
      // tool_use_id 是一次工具调用的唯一编号，用它找到并更新同一张工具卡片。
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.requestId === action.requestId && message.id === action.toolUseId
            ? {
                ...message,
                content: action.content,
                durationMs: action.durationMs,
                status: action.isError ? 'failed' : 'complete'
              }
            : message
        )
      }
    case 'turn_completed':
      // 一轮只代表一次 LLM 请求结束；只记轮数，不能提前解锁输入框。
      if (state.activeRequestId !== action.requestId) return state
      return { ...state, iteration: action.iteration }
    case 'usage_received':
      // 后端给的是整个会话累计值，所以这里直接替换而不是再次相加。
      return {
        ...state,
        usage: { inputTokens: action.inputTokens, outputTokens: action.outputTokens }
      }
    case 'loop_completed':
      // 整个 Agent Loop 结束后才解锁输入框，并进行一次 Markdown 渲染。
      if (state.activeRequestId !== action.requestId) return state
      return {
        ...state,
        activeRequestId: null,
        startedAt: null,
        durationMs: action.durationMs,
        model: action.model,
        messages: updateAssistant(state.messages, action.requestId, (message) => ({
          ...message,
          status: action.isError ? 'failed' : 'complete'
        })).filter(
          (message) =>
            message.requestId !== action.requestId ||
            message.role !== 'assistant' ||
            message.content.length > 0
        )
      }
    case 'request_failed':
      // 请求失败也保留已经收到的文字；若一段都没有，才显示错误消息。
      return {
        ...state,
        activeRequestId: null,
        startedAt: null,
        messages: updateAssistant(state.messages, action.requestId, (message) => ({
          ...message,
          status: 'failed',
          content: message.content || action.message
        }))
      }
  }
}
