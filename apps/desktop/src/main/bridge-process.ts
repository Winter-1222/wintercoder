import { randomUUID } from 'node:crypto'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { delimiter, resolve } from 'node:path'

import {
  PROTOCOL_VERSION,
  isBridgeEnvelope,
  type BridgeEnvelope,
  type BridgeState
} from '../shared/protocol'

const MAX_BUFFER_LENGTH = 1024 * 1024

type EventListener = (event: BridgeEnvelope) => void
type StateListener = (state: BridgeState) => void

export class PythonBridge {
  private child: ChildProcessWithoutNullStreams | null = null
  private stdoutBuffer = ''
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
    if (this.child) {
      return
    }

    this.stopping = false
    this.setState('starting', '正在唤醒 Python Bridge')

    const sourceRoot = resolve(this.projectRoot, 'src')
    const pythonPath = [sourceRoot, process.env.PYTHONPATH].filter(Boolean).join(delimiter)

    this.child = spawn(
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

    this.child.stdout.setEncoding('utf8')
    this.child.stderr.setEncoding('utf8')
    this.child.stdout.on('data', (chunk: string) => this.consumeStdout(chunk))
    this.child.stderr.on('data', (chunk: string) => {
      // stderr 是诊断通道；永远不要把它当作协议事件解析。
      console.error(`[jixue-python] ${chunk.trimEnd()}`)
    })
    this.child.on('spawn', () => this.sendHello())
    this.child.on('error', (error) => {
      this.setState('error', `无法启动 Python Bridge：${error.message}`)
    })
    this.child.on('exit', (code, signal) => {
      this.child = null
      if (this.stopping) {
        this.setState('offline', 'Python Bridge 已关闭')
        return
      }
      this.setState(
        'error',
        `Python Bridge 意外退出（code=${code ?? 'null'}, signal=${signal ?? 'null'}）`
      )
    })
  }

  sendChat(requestId: string, text: string): void {
    if (this.state.status !== 'ready') {
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
        model_id: 'fake'
      }
    })
  }

  stop(): void {
    this.stopping = true
    if (!this.child) {
      this.setState('offline', 'Python Bridge 已关闭')
      return
    }

    this.child.stdin.end()
    this.child.kill()
  }

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

  private writeEnvelope(envelope: BridgeEnvelope): void {
    if (!this.child?.stdin.writable) {
      throw new Error('Python Bridge 输入通道不可用')
    }
    this.child.stdin.write(`${JSON.stringify(envelope)}\n`, 'utf8')
  }

  private consumeStdout(chunk: string): void {
    this.stdoutBuffer += chunk
    if (this.stdoutBuffer.length > MAX_BUFFER_LENGTH) {
      this.stdoutBuffer = ''
      this.setState('error', 'Python Bridge 返回了超过 1 MiB 的协议行')
      return
    }

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

  private consumeLine(line: string): void {
    try {
      const parsed: unknown = JSON.parse(line)
      if (!isBridgeEnvelope(parsed)) {
        throw new Error('事件结构不符合协议')
      }

      if (parsed.type === 'bridge.ready') {
        this.setState('ready', 'FakeLLM / Bridge 在线')
      }
      for (const listener of this.eventListeners) {
        listener(parsed)
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      this.setState('error', `无法解析 Python Bridge 事件：${detail}`)
    }
  }

  private setState(status: BridgeState['status'], detail: string): void {
    this.state = { status, detail }
    for (const listener of this.stateListeners) {
      listener(this.getState())
    }
  }
}

