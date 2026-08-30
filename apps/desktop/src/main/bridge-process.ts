/**
 * Python 子进程适配器。
 *
 * PythonBridge 把“启动进程、写 stdin、读 stdout”这些实现细节封装起来，
 * 上层 Main 只订阅 BridgeEnvelope 和 BridgeState。它不理解聊天内容，也不调用 LLM SDK。
 */

import { randomUUID } from 'node:crypto'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { delimiter, resolve } from 'node:path'

import {
  PROTOCOL_VERSION,
  isBridgeEnvelope,
  type BridgeEnvelope,
  type BridgeState
} from '../shared/protocol'

/** 防止 Python 长时间不换行导致 stdoutBuffer 无限制增长，当前上限是 1 MiB。 */
const MAX_BUFFER_LENGTH = 1024 * 1024

/** 一条完整 Bridge 业务事件到达时调用的回调函数类型。 */
type EventListener = (event: BridgeEnvelope) => void
/** Bridge 上线、离线或出错时调用的回调函数类型。 */
type StateListener = (state: BridgeState) => void

/** 管理一个 Conda Python 子进程及其 NDJSON 通信。 */
export class PythonBridge {
  /** 正在运行的子进程；null 表示尚未启动或已经退出。 */
  private child: ChildProcessWithoutNullStreams | null = null
  /** 暂存 stdout 最后一段尚未遇到换行符的半条消息。 */
  private stdoutBuffer = ''
  /** Main/Renderer 当前可以展示的连接状态快照。 */
  private state: BridgeState = { status: 'offline', detail: '后端尚未启动' }
  /** Set 自动去重，允许 Main 注册多个业务事件观察者。 */
  private readonly eventListeners = new Set<EventListener>()
  /** 连接状态观察者与业务事件观察者分开，避免 UI 从文本事件猜连接状态。 */
  private readonly stateListeners = new Set<StateListener>()
  /** 区分“用户正常退出”与“Python 意外崩溃”，两者给 UI 的提示不同。 */
  private stopping = false
  /** Python 握手声明的实际模型名；发送命令时一并带回，方便后续做模型路由校验。 */
  private activeModelName = 'unknown'

  /** projectRoot 用于设置 Python 工作目录并构造 PYTHONPATH。 */
  constructor(private readonly projectRoot: string) {}

  /** 返回状态副本，防止调用方直接修改 PythonBridge 内部 state 对象。 */
  getState(): BridgeState {
    return { ...this.state }
  }

  /**
   * 注册业务事件监听器，并返回取消订阅函数。
   * React useEffect 清理时会调用返回函数，避免重复监听。
   */
  onEvent(listener: EventListener): () => void {
    this.eventListeners.add(listener)
    return () => this.eventListeners.delete(listener)
  }

  /** 注册 Bridge 状态监听器，并返回取消订阅函数。 */
  onState(listener: StateListener): () => void {
    this.stateListeners.add(listener)
    return () => this.stateListeners.delete(listener)
  }

  /**
   * 在 Conda 的 mycoder 环境中启动 Python Bridge，并绑定三条标准通道。
   * 重复调用 start() 不会创建第二个子进程。
   */
  start(): void {
    if (this.child) {
      // 已有进程时直接返回，防止同一 Main 意外启动多个 Python。
      return
    }

    this.stopping = false
    // 每次启动都等待新的 bridge.ready，不能沿用上一个 Python 进程的模型名。
    this.activeModelName = 'unknown'
    this.setState('starting', '正在唤醒 Python Bridge')

    // 把项目 src 加到 PYTHONPATH，Python 才能在未安装或开发模式下导入 jixue。
    const sourceRoot = resolve(this.projectRoot, 'src')
    const pythonPath = [sourceRoot, process.env.PYTHONPATH].filter(Boolean).join(delimiter)

    // spawn 返回子进程对象；与 exec 不同，它可以持续读写流，适合长连接 Bridge。
    this.child = spawn(
      'conda',
      ['run', '--no-capture-output', '-n', 'mycoder', 'python', '-u', '-m', 'jixue.bridge'],
      {
        cwd: this.projectRoot,
        // 后台进程不额外弹出黑色命令行窗口。
        windowsHide: true,
        env: {
          ...process.env,
          PYTHONIOENCODING: 'utf-8',
          PYTHONPATH: pythonPath
        }
      }
    )

    // 指定编码后 data 回调收到 string，而不是需要手工解码的 Buffer。
    this.child.stdout.setEncoding('utf8')
    this.child.stderr.setEncoding('utf8')
    this.child.stdin.on('error', (error) => {
      // 退出时 stdin 与子进程可能同时关闭；这类竞态不应升级成主进程异常。
      if (!this.stopping) {
        this.setState('error', `Python Bridge 输入通道异常：${error.message}`)
      }
    })
    this.child.stdout.on('data', (chunk: string) => this.consumeStdout(chunk))
    this.child.stderr.on('data', (chunk: string) => {
      // stderr 是诊断通道；永远不要把它当作协议事件解析。
      console.error(`[jixue-python] ${chunk.trimEnd()}`)
    })
    // spawn 只表示操作系统成功创建进程；随后用 hello/ready 确认 Python 协议层也可用。
    this.child.on('spawn', () => this.sendHello())
    this.child.on('error', (error) => {
      // error 常见于 conda 命令不存在或系统无法创建进程，此时可能没有 exit 事件。
      this.setState('error', `无法启动 Python Bridge：${error.message}`)
    })
    this.child.on('exit', (code, signal) => {
      // 先清空引用，用户之后才有机会重新启动一个新进程。
      this.child = null
      if (this.stopping) {
        // stop() 主动触发的退出属于正常状态，不应显示“后端崩溃”。
        this.setState('offline', 'Python Bridge 已关闭')
        return
      }
      this.setState(
        'error',
        `Python Bridge 意外退出（code=${code ?? 'null'}, signal=${signal ?? 'null'}）`
      )
    })
  }

  /**
   * 把一条用户文本包装成 chat.send 命令并写给 Python。
   * 当前 session_id 仍是第一章占位值；model_id 使用握手得到的实际模型名。
   * 本小步的模型选择发生在 Python 进程启动时，后续 UI 模型选择会再升级协议。
   */
  sendChat(requestId: string, text: string): void {
    if (this.state.status !== 'ready') {
      // 未握手完成时拒绝发送，避免命令写入尚未准备好的进程。
      throw new Error('Python Bridge 尚未就绪')
    }

    this.writeEnvelope({
      version: PROTOCOL_VERSION,
      type: 'chat.send',
      request_id: requestId,
      sequence: 0,
      timestamp: new Date().toISOString(),
      payload: {
        session_id: 'session_dev',
        text,
        model_id: this.activeModelName
      }
    })
  }

  /**
   * 关闭 Python Bridge。
   * stdin.end() 先通知 Python 不会再有命令，kill() 兜底确保进程不残留。
   */
  stop(): void {
    this.stopping = true
    const child = this.child
    if (!child) {
      this.setState('offline', 'Python Bridge 已关闭')
      return
    }

    try {
      if (child.stdin.writable) {
        child.stdin.end()
      }
      if (!child.killed) {
        child.kill()
      }
    } catch (error) {
      // 进程可能恰好自行退出；关闭路径只记录诊断，不弹主进程错误窗口。
      const detail = error instanceof Error ? error.message : String(error)
      console.error(`[jixue-bridge-stop] ${detail}`)
    }
  }

  /** 子进程创建成功后发送协议握手，ready 返回前 UI 仍保持 starting。 */
  private sendHello(): void {
    this.writeEnvelope({
      version: PROTOCOL_VERSION,
      type: 'bridge.hello',
      request_id: `bridge_${randomUUID()}`,
      sequence: 0,
      timestamp: new Date().toISOString(),
      payload: {
        protocol_version: PROTOCOL_VERSION,
        client_version: '0.1.0'
      }
    })
  }

  /**
   * 把 JavaScript 信封序列化为“一行 JSON + 换行符”，写入 Python stdin。
   * 换行符是 NDJSON 的消息边界，Python readline() 依靠它判断一条命令结束。
   */
  private writeEnvelope(envelope: BridgeEnvelope): void {
    if (!this.child?.stdin.writable) {
      throw new Error('Python Bridge 输入通道不可用')
    }
    this.child.stdin.write(`${JSON.stringify(envelope)}\n`, 'utf8')
  }

  /**
   * 接收 stdout 的任意文本片段，并按换行符恢复完整 NDJSON 行。
   * 操作系统的 data chunk 不保证正好对应一条事件，因此不能直接 JSON.parse(chunk)。
   */
  private consumeStdout(chunk: string): void {
    this.stdoutBuffer += chunk
    if (this.stdoutBuffer.length > MAX_BUFFER_LENGTH) {
      this.stdoutBuffer = ''
      this.setState('error', 'Python Bridge 返回了超过 1 MiB 的协议行')
      return
    }

    // 一次 chunk 可能包含多行，所以使用 while 逐行消费，而不是只切一次。
    let lineEnd = this.stdoutBuffer.indexOf('\n')
    while (lineEnd >= 0) {
      const line = this.stdoutBuffer.slice(0, lineEnd).trim()
      this.stdoutBuffer = this.stdoutBuffer.slice(lineEnd + 1)
      if (line) {
        this.consumeLine(line)
      }
      lineEnd = this.stdoutBuffer.indexOf('\n')
    }
  }

  /** 把一条完整 JSON 行解析成领域信封，并通知业务监听器。 */
  private consumeLine(line: string): void {
    try {
      const parsed: unknown = JSON.parse(line)
      if (!isBridgeEnvelope(parsed)) {
        throw new Error('事件结构不符合协议')
      }

      if (parsed.type === 'bridge.ready') {
        // 跨进程 payload 仍是不可信数据，必须运行时检查，不能只相信 TypeScript 类型。
        const model = parsed.payload.model
        if (typeof model !== 'string' || model.trim().length === 0) {
          throw new Error('bridge.ready 缺少有效模型名')
        }
        this.activeModelName = model
        // ready 是握手完成标志；只有这之后 sendChat() 才允许写业务命令。
        this.setState('ready', `${model} / Bridge 在线`)
      }
      // Bridge 自己不消费聊天事件，只把统一信封广播给 Main 的监听器。
      for (const listener of this.eventListeners) {
        listener(parsed)
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      this.setState('error', `无法解析 Python Bridge 事件：${detail}`)
    }
  }

  /** 更新内部状态并把副本广播给所有状态监听器。 */
  private setState(status: BridgeState['status'], detail: string): void {
    this.state = { status, detail }
    for (const listener of this.stateListeners) {
      listener(this.getState())
    }
  }
}
