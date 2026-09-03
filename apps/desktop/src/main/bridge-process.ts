/** 管理 Conda Python 子进程和 NDJSON 通信。 */

import { randomUUID } from 'node:crypto'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { delimiter, resolve } from 'node:path'

import {
  PROTOCOL_VERSION,
  isBridgeEnvelope,
  type AgentMode,
  type BridgeEnvelope,
  type BridgeState
} from '../shared/protocol'

const MAX_BUFFER = 1024 * 1024
type EventListener = (event: BridgeEnvelope) => void
type StateListener = (state: BridgeState) => void

export class PythonBridge {
  private child: ChildProcessWithoutNullStreams | null = null
  private buffer = ''
  private state: BridgeState = { status: 'offline', detail: '后端尚未启动' }
  private readonly eventListeners = new Set<EventListener>()
  private readonly stateListeners = new Set<StateListener>()
  private stopping = false

  constructor(private readonly projectRoot: string) {}

  getState(): BridgeState {
    return { ...this.state }
  }

  onEvent(listener: EventListener): () => void {
    this.eventListeners.add(listener)
    return () => this.eventListeners.delete(listener)
  }

  onState(listener: StateListener): () => void {
    this.stateListeners.add(listener)
    return () => this.stateListeners.delete(listener)
  }

  start(): void {
    if (this.child) return
    this.stopping = false
    this.setState('starting', '正在启动 Python Bridge')

    const sourceRoot = resolve(this.projectRoot, 'src')
    const pythonPath = [sourceRoot, process.env.PYTHONPATH].filter(Boolean).join(delimiter)
    const child = spawn(
      'conda',
      ['run', '--no-capture-output', '-n', 'mycoder', 'python', '-u', '-m', 'jixue.bridge'],
      {
        cwd: this.projectRoot,
        windowsHide: true,
        env: {
          ...process.env,
          PYTHONIOENCODING: 'utf-8',
          PYTHONPATH: pythonPath
        }
      }
    )
    this.child = child
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk: string) => this.readStdout(chunk))
    child.stderr.on('data', (chunk: string) => console.error(`[jixue-python] ${chunk.trimEnd()}`))
    child.stdin.on('error', (error) => {
      if (!this.stopping) this.setState('error', `Bridge 输入异常：${error.message}`)
    })
    child.on('spawn', () => this.write('bridge.hello', `bridge_${randomUUID()}`, {}))
    child.on('error', (error) => this.setState('error', `无法启动 Bridge：${error.message}`))
    child.on('exit', (code) => {
      this.child = null
      this.setState(
        this.stopping ? 'offline' : 'error',
        this.stopping ? 'Python Bridge 已关闭' : `Python Bridge 意外退出（${code ?? '未知'}）`
      )
    })
  }

  sendChat(requestId: string, text: string): void {
    if (this.state.status !== 'ready') throw new Error('Python Bridge 尚未就绪')
    this.write('chat.send', requestId, { text })
  }

  cancelChat(requestId: string): void {
    if (this.state.status !== 'ready') throw new Error('Python Bridge 尚未就绪')
    // 取消命令有自己的编号，payload 指向真正需要停止的聊天请求。
    this.write('chat.cancel', `cancel_${randomUUID()}`, { target_request_id: requestId })
  }

  respondPermission(requestId: string, toolUseId: string, allow: boolean): void {
    if (this.state.status !== 'ready') throw new Error('Python Bridge 尚未就绪')
    // 权限命令有自己的编号，同时指向聊天请求和其中那一次工具调用。
    this.write('permission.respond', 'permission_' + randomUUID(), {
      target_request_id: requestId,
      tool_use_id: toolUseId,
      allow
    })
  }

  setAgentMode(mode: AgentMode): void {
    if (this.state.status !== 'ready') throw new Error('Python Bridge 尚未就绪')
    this.write('agent.mode', 'mode_' + randomUUID(), { mode })
  }

  stop(): void {
    this.stopping = true
    const child = this.child
    if (!child) return
    try {
      if (child.stdin.writable) child.stdin.end()
      if (!child.killed) child.kill()
    } catch (error) {
      console.error(`[jixue-stop] ${error instanceof Error ? error.message : String(error)}`)
    }
  }

  private write(type: string, requestId: string, payload: Record<string, unknown>): void {
    if (!this.child?.stdin.writable) throw new Error('Python Bridge 输入通道不可用')
    const envelope: BridgeEnvelope = {
      version: PROTOCOL_VERSION,
      type,
      request_id: requestId,
      sequence: 0,
      timestamp: new Date().toISOString(),
      payload
    }
    this.child.stdin.write(`${JSON.stringify(envelope)}\n`)
  }

  private readStdout(chunk: string): void {
    this.buffer += chunk
    if (this.buffer.length > MAX_BUFFER) {
      this.buffer = ''
      this.setState('error', 'Bridge 返回内容过大')
      return
    }
    let end = this.buffer.indexOf('\n')
    while (end >= 0) {
      const line = this.buffer.slice(0, end).trim()
      this.buffer = this.buffer.slice(end + 1)
      if (line) this.readLine(line)
      end = this.buffer.indexOf('\n')
    }
  }

  private readLine(line: string): void {
    try {
      const event: unknown = JSON.parse(line)
      if (!isBridgeEnvelope(event)) throw new Error('事件格式错误')
      if (event.type === 'bridge.ready') {
        const model = event.payload.model
        if (typeof model !== 'string') throw new Error('ready 缺少模型名')
        this.setState('ready', `${model} / Bridge 在线`)
      }
      this.eventListeners.forEach((listener) => listener(event))
    } catch (error) {
      this.setState('error', `无法解析 Bridge 事件：${String(error)}`)
    }
  }

  private setState(status: BridgeState['status'], detail: string): void {
    this.state = { status, detail }
    this.stateListeners.forEach((listener) => listener(this.getState()))
  }
}
