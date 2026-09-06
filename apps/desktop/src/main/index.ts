/** Electron Main：创建窗口、校验 IPC、管理 Python Bridge。 */

import { app, BrowserWindow, ipcMain } from 'electron'
import { join, resolve } from 'node:path'

import type { AgentMode, PermissionMode, SessionAction } from '../shared/protocol'
import { PythonBridge } from './bridge-process'

let mainWindow: BrowserWindow | null = null
let bridge: PythonBridge | null = null

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1180,
    height: 800,
    minWidth: 860,
    minHeight: 620,
    show: false,
    title: '霁雪 · Jixue',
    backgroundColor: '#fafafa',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })
  window.once('ready-to-show', () => window.show())
  window.once('closed', () => {
    if (mainWindow === window) mainWindow = null
  })
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  if (process.env.ELECTRON_RENDERER_URL) {
    void window.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void window.loadFile(join(__dirname, '../renderer/index.html'))
  }
  return window
}

function validSender(event: Electron.IpcMainInvokeEvent): boolean {
  return !!mainWindow && !mainWindow.isDestroyed() && event.sender === mainWindow.webContents
}

function send(channel: string, value: unknown): void {
  if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.webContents.isDestroyed()) {
    mainWindow.webContents.send(channel, value)
  }
}

function registerIpc(): void {
  ipcMain.handle('jixue:subagent', (event, action: unknown, sessionId: unknown,
    agentId: unknown, token: unknown, allow: unknown) => {
    if (!validSender(event)) throw new Error('拒绝未知窗口')
    if (action !== 'stop' && action !== 'respond' && action !== 'status') throw new Error('子任务操作无效')
    if (typeof sessionId !== 'string' || !/^[0-9a-f]{32}$/.test(sessionId) ||
        typeof agentId !== 'string' || !/^sub_[0-9a-f]{32}$/.test(agentId)) {
      throw new Error('子任务或会话编号无效')
    }
    if (action === 'respond' && (typeof token !== 'string' ||
        !/^[0-9a-f]{32}$/.test(token) || typeof allow !== 'boolean')) {
      throw new Error('子任务确认参数无效')
    }
    if (!bridge) throw new Error('Bridge 未创建')
    bridge.subagent(action, sessionId, agentId, token as string | undefined,
      allow as boolean | undefined)
  })
  ipcMain.handle('jixue:session', (event, action: unknown, sessionId: unknown) => {
    if (!validSender(event)) throw new Error('拒绝未知窗口')
    if (typeof action !== 'string' || !['current', 'new', 'switch', 'list'].includes(action)) {
      throw new Error('会话操作无效')
    }
    if (sessionId !== undefined &&
        (typeof sessionId !== 'string' || !/^[0-9a-f]{32}$/.test(sessionId))) {
      throw new Error('会话编号无效')
    }
    if (!bridge) throw new Error('Bridge 未创建')
    bridge.session(action as SessionAction, sessionId as string | undefined)
  })
  ipcMain.handle('jixue:get-bridge-state', (event) => {
    if (!validSender(event)) throw new Error('拒绝未知窗口')
    return bridge?.getState() ?? {
      status: 'offline',
      detail: 'Bridge 未创建',
      mcpServers: []
    }
  })
  ipcMain.handle('jixue:send-chat', (event, requestId: unknown, text: unknown) => {
    if (!validSender(event)) throw new Error('拒绝未知窗口')
    if (typeof requestId !== 'string' || !requestId.startsWith('req_')) {
      throw new Error('requestId 无效')
    }
    if (typeof text !== 'string' || !text.trim() || text.length > 20_000) {
      throw new Error('消息必须是 1—20000 个字符')
    }
    bridge?.sendChat(requestId, text)
  })
  ipcMain.handle('jixue:cancel-chat', (event, requestId: unknown) => {
    if (!validSender(event)) throw new Error('拒绝未知窗口')
    if (typeof requestId !== 'string' || !requestId.startsWith('req_')) {
      throw new Error('requestId 无效')
    }
    bridge?.cancelChat(requestId)
  })
  ipcMain.handle(
    'jixue:respond-permission',
    (event, requestId: unknown, toolUseId: unknown, allow: unknown) => {
      if (!validSender(event)) throw new Error('拒绝未知窗口')
      if (typeof requestId !== 'string' || !requestId.startsWith('req_')) {
        throw new Error('requestId 无效')
      }
      if (typeof toolUseId !== 'string' || !toolUseId || toolUseId.length > 500) {
        throw new Error('toolUseId 无效')
      }
      if (typeof allow !== 'boolean') throw new Error('allow 必须是布尔值')
      bridge?.respondPermission(requestId, toolUseId, allow)
    }
  )
  ipcMain.handle('jixue:set-agent-mode', (event, mode: unknown) => {
    if (!validSender(event)) throw new Error('拒绝未知窗口')
    if (mode !== 'plan' && mode !== 'do') throw new Error('模式只能是 plan 或 do')
    bridge?.setAgentMode(mode as AgentMode)
  })
  ipcMain.handle('jixue:set-permission-mode', (event, mode: unknown) => {
    if (!validSender(event)) throw new Error('拒绝未知窗口')
    if (mode !== 'confirm_edits' && mode !== 'ask_all' && mode !== 'auto_allow') {
      throw new Error('权限模式无效')
    }
    bridge?.setPermissionMode(mode as PermissionMode)
  })
}

app.whenReady().then(() => {
  mainWindow = createWindow()
  registerIpc()
  const root = process.env.JIXUE_PROJECT_ROOT ?? resolve(app.getAppPath(), '../..')
  bridge = new PythonBridge(root)
  bridge.onEvent((event) => send('jixue:bridge-event', event))
  bridge.onState((state) => send('jixue:bridge-state', state))
  bridge.start()
})

app.on('before-quit', () => bridge?.stop())
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
