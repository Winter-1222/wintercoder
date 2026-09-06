/** React 聊天状态；reducer 只做“旧状态 + 事件 = 新状态”。 */

import type { AgentMode, BridgeState, PermissionMode } from '../../shared/protocol'

export type PermissionStatus =
  | 'pending'
  | 'allowing'
  | 'denying'
  | 'allowed'
  | 'denied'
  | 'expired'

export interface UiMessage {
  id: string
  requestId: string
  role: 'user' | 'assistant' | 'tool'
  content: string
  status: 'streaming' | 'complete' | 'failed' | 'cancelled'
  name?: string
  input?: string
  durationMs?: number
  permissionStatus?: PermissionStatus
  permissionReason?: string
  isDestructive?: boolean
}

export interface ChatState {
  bridge: BridgeState
  messages: UiMessage[]
  activeRequestId: string | null
  isCancelling: boolean
  startedAt: number | null
  durationMs: number
  iteration: number
  model: string
  mode: AgentMode
  permissionMode: PermissionMode
  usage: { inputTokens: number; outputTokens: number }
}

export type ChatAction =
  | { type: 'session_reset' }
  | { type: 'session_restored'; inputTokens: number; outputTokens: number }
  | { type: 'bridge_changed'; state: BridgeState }
  | { type: 'model_changed'; model: string }
  | { type: 'mode_changed'; mode: AgentMode }
  | { type: 'permission_mode_changed'; mode: PermissionMode }
  | { type: 'request_started'; requestId: string; text: string; startedAt: number }
  | { type: 'cancel_requested'; requestId: string }
  | { type: 'cancel_failed'; requestId: string }
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
      type: 'permission_requested'
      requestId: string
      toolUseId: string
      reason: string
      isDestructive: boolean
    }
  | {
      type: 'permission_submitted'
      requestId: string
      toolUseId: string
      allow: boolean
    }
  | { type: 'permission_submit_failed'; requestId: string; toolUseId: string }
  | {
      type: 'permission_resolved'
      requestId: string
      toolUseId: string
      allow: boolean
      accepted: boolean
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
      cancelled: boolean
    }
  | { type: 'request_failed'; requestId: string; message: string }

export const initialChatState: ChatState = {
  bridge: {
    status: 'starting',
    detail: '正在连接 Python Bridge',
    mcpServers: []
  },
  messages: [],
  activeRequestId: null,
  isCancelling: false,
  startedAt: null,
  durationMs: 0,
  iteration: 0,
  model: 'fake-jixue',
  mode: 'do',
  permissionMode: 'confirm_edits',
  usage: { inputTokens: 0, outputTokens: 0 }
}

function updateTool(
  messages: UiMessage[],
  requestId: string,
  toolUseId: string,
  update: (message: UiMessage) => UiMessage
): UiMessage[] {
  return messages.map((message) =>
    message.requestId === requestId && message.id === toolUseId && message.role === 'tool'
      ? update(message)
      : message
  )
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
    case 'session_reset':
      return { ...initialChatState, bridge: state.bridge, model: state.model }
    case 'session_restored':
      // 回放只恢复显示；任何旧确认都不能再次变成可点击的权限请求。
      return {
        ...state,
        activeRequestId: null,
        isCancelling: false,
        startedAt: null,
        usage: { inputTokens: action.inputTokens, outputTokens: action.outputTokens },
        messages: state.messages.map((message) => ({
          ...message,
          status: message.status === 'streaming' ? 'cancelled' : message.status,
          permissionStatus: message.permissionStatus ? 'expired' : undefined
        }))
      }
    case 'bridge_changed':
      // Python 进程的启动、在线或错误状态变化时，只替换 Bridge 状态。
      return { ...state, bridge: action.state }
    case 'model_changed':
      // 握手成功后，把后端实际使用的模型名显示到状态栏。
      return { ...state, model: action.model }
    case 'mode_changed':
      // 后端确认后再切换高亮，页面状态不会领先于 Agent 的真实模式。
      return { ...state, mode: action.mode }
    case 'permission_mode_changed':
      // 与 Agent 模式一样，只有收到 Python 的确认事件后才更新选择框。
      return { ...state, permissionMode: action.mode }
    case 'request_started':
      // 先放入用户消息和空的 AI 消息，后续流式文字会追加到这个空位置。
      return {
        ...state,
        activeRequestId: action.requestId,
        isCancelling: false,
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
    case 'cancel_requested':
      // 点击停止后先禁用按钮；真正解锁仍要等待后端的 loop_complete。
      if (state.activeRequestId !== action.requestId) return state
      return { ...state, isCancelling: true }
    case 'cancel_failed':
      // 取消命令没能发出时恢复停止按钮，当前任务仍继续运行。
      if (state.activeRequestId !== action.requestId) return state
      return { ...state, isCancelling: false }
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
    case 'permission_requested':
      // permission_request 不会新建卡片，而是把已有 tool_use 卡片切换成“等待确认”。
      return {
        ...state,
        messages: updateTool(
          state.messages,
          action.requestId,
          action.toolUseId,
          (message) => ({
            ...message,
            content: '等待你的确认…',
            permissionStatus: 'pending',
            permissionReason: action.reason,
            isDestructive: action.isDestructive
          })
        )
      }
    case 'permission_submitted':
      // 点击后立刻禁用两个按钮，防止连续点击重复发送同一次决定。
      return {
        ...state,
        messages: updateTool(
          state.messages,
          action.requestId,
          action.toolUseId,
          (message) => ({
            ...message,
            permissionStatus: action.allow ? 'allowing' : 'denying'
          })
        )
      }
    case 'permission_submit_failed':
      // IPC 没发出去时恢复按钮，用户可以再次选择。
      return {
        ...state,
        messages: updateTool(
          state.messages,
          action.requestId,
          action.toolUseId,
          (message) => ({ ...message, permissionStatus: 'pending' })
        )
      }
    case 'permission_resolved':
      // accepted=false 说明任务或按钮已经过期，不能继续操作这张卡片。
      return {
        ...state,
        messages: updateTool(
          state.messages,
          action.requestId,
          action.toolUseId,
          (message) => ({
            ...message,
            permissionStatus: action.accepted
              ? action.allow
                ? 'allowed'
                : 'denied'
              : 'expired',
            // 如果 tool_result 已经先到，就保留真正结果，不能被稍晚到达的回执覆盖。
            content:
              message.status !== 'streaming'
                ? message.content
                : !action.accepted
                  ? '确认已失效'
                  : action.allow
                    ? '已允许，正在执行…'
                    : '已拒绝，正在通知模型…'
          })
        )
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
        isCancelling: false,
        startedAt: null,
        durationMs: action.durationMs,
        model: action.model,
        messages: updateAssistant(state.messages, action.requestId, (message) => ({
          ...message,
          status: action.cancelled ? 'cancelled' : action.isError ? 'failed' : 'complete'
        })).filter(
          (message) =>
            message.requestId !== action.requestId ||
            message.role !== 'assistant' ||
            action.cancelled ||
            message.content.length > 0
        )
      }
    case 'request_failed':
      // 请求失败也保留已经收到的文字；若一段都没有，才显示错误消息。
      return {
        ...state,
        activeRequestId: null,
        isCancelling: false,
        startedAt: null,
        messages: updateAssistant(state.messages, action.requestId, (message) => ({
          ...message,
          status: 'failed',
          content: message.content || action.message
        }))
      }
  }
}
