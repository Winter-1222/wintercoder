/**
 * Electron 三层共享的协议类型。
 *
 * 这里只描述霁雪自己的字段，不导入 Python、Anthropic 或其他 SDK 类型。
 * Python 端在 domain/events.py 中维护同一份运行时契约。
 */

/** 协议主版本；两端版本不一致时应拒绝消息，而不是猜测字段含义。 */
export const PROTOCOL_VERSION = 1

/** Python Bridge 从启动到退出可能处于的四种连接状态。 */
export type BridgeStatus = 'starting' | 'ready' | 'offline' | 'error'

/**
 * 跨 Python/Electron 进程传输的统一信封。
 * 固定外层字段负责路由和排序，具体业务字段放在 payload 中。
 */
export interface BridgeEnvelope {
  /** 当前协议版本。 */
  version: number
  /** 事件或命令名称，例如 chat.send、stream_text。 */
  type: string
  /** 一次用户请求的关联 ID，用于把返回事件送到正确消息。 */
  request_id: string
  /** 同一 request_id 下的事件序号，数值越小表示越早产生。 */
  sequence: number
  /** 创建事件时的 UTC ISO 时间字符串。 */
  timestamp: string
  /** 各事件自己的字段；消费前仍需检查具体值的运行时类型。 */
  payload: Record<string, unknown>
}

/** UI 展示和控制输入框所需的 Bridge 状态快照。 */
export interface BridgeState {
  status: BridgeStatus
  /** 面向用户的中文状态说明，不包含密钥或敏感上下文。 */
  detail: string
}

/** Preload 暴露给 Renderer 的完整白名单接口。 */
export interface JixueDesktopApi {
  /** 发送一条聊天命令；真正的参数校验仍由 Main 完成。 */
  sendChat: (requestId: string, text: string) => Promise<void>
  /** 读取 Bridge 当前状态，而不是等待下一次状态变化。 */
  getBridgeState: () => Promise<BridgeState>
  /** 订阅业务事件，返回取消订阅函数。 */
  onBridgeEvent: (listener: (event: BridgeEnvelope) => void) => () => void
  /** 订阅连接状态，返回取消订阅函数。 */
  onBridgeState: (listener: (state: BridgeState) => void) => () => void
}

/**
 * 运行时类型守卫：确认 JSON.parse 得到的 unknown 至少具有合法信封外形。
 * TypeScript 类型只在编译期存在，跨进程数据到达后仍必须实际检查。
 */
export function isBridgeEnvelope(value: unknown): value is BridgeEnvelope {
  if (typeof value !== 'object' || value === null) {
    // null 在 JavaScript 中也会被 typeof 判定为 object，因此需要单独排除。
    return false
  }

  // Partial 允许逐字段检查；通过完整 return 条件后 TypeScript 才收窄为 BridgeEnvelope。
  const candidate = value as Partial<BridgeEnvelope>
  return (
    candidate.version === PROTOCOL_VERSION &&
    typeof candidate.type === 'string' &&
    typeof candidate.request_id === 'string' &&
    typeof candidate.sequence === 'number' &&
    typeof candidate.timestamp === 'string' &&
    typeof candidate.payload === 'object' &&
    candidate.payload !== null
  )
}
