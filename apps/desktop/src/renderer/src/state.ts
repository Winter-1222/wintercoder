/**
 * Renderer 的对话状态与 reducer。
 *
 * 可以把 ChatState 想成“界面当前的总账本”，把 ChatAction 想成“刚刚发生的事情”。
 * chatReducer 不负责请求模型，也不直接操作 HTML；它只根据“旧状态 + action”计算新状态。
 * React 拿到新状态后会自动重新渲染界面，这种写法让状态变化可以独立测试。
 */

import type { BridgeState } from '../../shared/protocol'

/** 一条消息由用户发出，还是由霁雪返回。 */
export type MessageRole = 'user' | 'assistant'

/**
 * 消息在 UI 中所处的生命周期。
 *
 * - streaming：assistant 仍在接收文本增量。
 * - complete：消息已经完整，可以渲染 Markdown。
 * - failed：请求失败；已收到的部分文本仍然保留。
 */
export type MessageStatus = 'streaming' | 'complete' | 'failed'

/** Renderer 为显示一条消息所需的最小数据。 */
export interface UiMessage {
  /** 消息自己的唯一 ID；assistant 收到后端 message_id 后会更新为后端 ID。 */
  id: string
  /** 这条消息属于哪一次用户请求，用于避免多个请求的文本串线。 */
  requestId: string
  /** user 显示为用户气泡，assistant 显示为霁雪回复。 */
  role: MessageRole
  /** 当前已经收到的完整文本；流式事件会不断追加到这里。 */
  content: string
  /** 决定显示流式纯文本、最终 Markdown 还是失败状态。 */
  status: MessageStatus
}

/** 当前这一轮模型调用的输入和输出 Token。 */
export interface TokenUsage {
  inputTokens: number
  outputTokens: number
}

/**
 * 整个聊天页面的状态快照。
 *
 * reducer 每处理一条 action，都会返回一个新的 ChatState 对象，而不是修改旧对象。
 * 这叫“不可变更新”，React 可以据此可靠判断哪些内容需要重新渲染。
 */
export interface ChatState {
  /** Python Bridge 的连接状态，控制输入框能否发送。 */
  bridge: BridgeState
  /** 按显示顺序保存的用户消息与 assistant 消息。 */
  messages: UiMessage[]
  /** 正在运行的请求 ID；null 表示当前没有请求，可再次发送。 */
  activeRequestId: string | null
  /** 当前请求开始时的浏览器时间戳，用于实时计算耗时。 */
  startedAt: number | null
  /** 最近一次完成请求的最终耗时，单位是毫秒。 */
  durationMs: number
  /** 状态栏显示的实际模型名称。 */
  model: string
  /** 最近一次 usage 事件携带的 Token 用量。 */
  usage: TokenUsage
}

/**
 * 所有允许改变 ChatState 的事件。
 *
 * 这是一个“可辨识联合类型”：每种 action 都有唯一的 type，switch 可以据此知道
 * 这一分支还会携带哪些字段。例如 text_received 一定有 text，bridge_changed 一定有 state。
 */
export type ChatAction =
  | { type: 'bridge_changed'; state: BridgeState }
  | { type: 'request_started'; requestId: string; text: string; startedAt: number }
  | { type: 'text_received'; requestId: string; messageId: string; text: string }
  | { type: 'usage_received'; inputTokens: number; outputTokens: number }
  | { type: 'request_completed'; requestId: string; durationMs: number; model: string }
  | { type: 'request_failed'; requestId: string; message: string }

/** 页面第一次打开、尚未收到任何 Bridge 状态时使用的初始值。 */
export const initialChatState: ChatState = {
  bridge: { status: 'starting', detail: '正在连接 Python Bridge' },
  messages: [],
  activeRequestId: null,
  startedAt: null,
  durationMs: 0,
  model: 'fake-jixue',
  usage: { inputTokens: 0, outputTokens: 0 }
}

/**
 * 根据一条 action 计算新的聊天状态，这个函数就是本项目当前的 reducer。
 *
 * reducer 应保持“纯函数”特征：
 * 1. 不调用 API、不读写文件、不启动定时器。
 * 2. 不直接修改传入的 state。
 * 3. 相同 state 和 action 应得到相同结果。
 *
 * @param state action 发生前的旧状态。
 * @param action 刚刚发生的业务事件。
 * @returns React 下一次渲染要使用的新状态。
 */
export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'bridge_changed':
      // Python Bridge 上线、离线或报错时，只替换 bridge 子状态。
      // 其他消息和 Token 保持不变，所以使用 ...state 复制旧状态。
      return { ...state, bridge: action.state }

    case 'request_started':
      // 用户刚按下发送：先在本地立即显示用户消息，再创建一条空的流式 assistant 消息。
      // 这样 UI 不必等待后端第一段文字，用户会马上看到自己的操作已经生效。
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
      // 后端每返回一个 stream_text，就找到同 requestId 的 assistant 消息并追加文本。
      // map 会创建一个新数组；不匹配的消息原样返回，避免误改其他轮次。
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
      // usage 是状态类事件：本章只展示最近一轮，所以直接用新值替换旧值。
      return {
        ...state,
        usage: {
          inputTokens: action.inputTokens,
          outputTokens: action.outputTokens
        }
      }

    case 'request_completed':
      // turn_complete 表示回复自然结束。清空 activeRequestId 后，发送按钮会重新可用。
      // 对应 assistant 消息改为 complete，MessageView 才会从纯文本切换到 Markdown。
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
      // 错误同样要结束“正在请求”状态，否则输入框会永远保持禁用。
      // 如果已经收到部分回复就保留它；完全没收到文本时才显示错误消息。
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
