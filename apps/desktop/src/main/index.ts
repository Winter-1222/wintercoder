import { app, BrowserWindow, ipcMain } from 'electron'
import { join, resolve } from 'node:path'

import { PythonBridge } from './bridge-process'

let mainWindow: BrowserWindow | null = null
let bridge: PythonBridge | null = null

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1180,
    height: 800,
    minWidth: 860,
    minHeight: 620,
    backgroundColor: '#071018',
    show: false,
    title: '霁雪 · Jixue',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })

  window.once('ready-to-show', () => window.show())
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event, url) => {
    if (url !== window.webContents.getURL()) {
      event.preventDefault()
    }
  })

  if (process.env.ELECTRON_RENDERER_URL) {
    void window.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void window.loadFile(join(__dirname, '../renderer/index.html'))
  }

  return window
}

function senderIsMainWindow(event: Electron.IpcMainInvokeEvent): boolean {
  return mainWindow !== null && event.sender === mainWindow.webContents
}

function registerIpc(): void {
  ipcMain.handle('jixue:get-bridge-state', (event) => {
    if (!senderIsMainWindow(event)) {
      throw new Error('拒绝未知窗口读取 Bridge 状态')
    }
    return bridge?.getState() ?? { status: 'offline', detail: 'Bridge 未创建' }
  })

  ipcMain.handle('jixue:send-chat', (event, requestId: unknown, text: unknown) => {
    if (!senderIsMainWindow(event)) {
      throw new Error('拒绝未知窗口发送消息')
    }
    if (typeof requestId !== 'string' || !/^req_[a-f0-9-]{16,}$/.test(requestId)) {
      throw new Error('requestId 格式无效')
    }
    if (typeof text !== 'string' || text.trim().length === 0 || text.length > 20_000) {
      throw new Error('消息必须是 1—20000 个字符')
    }
    bridge?.sendChat(requestId, text)
  })
}

app.whenReady().then(() => {
  mainWindow = createWindow()
  registerIpc()

  // 开发阶段 appPath 指向 apps/desktop；打包路径在发布章节单独处理。
  const projectRoot = process.env.JIXUE_PROJECT_ROOT ?? resolve(app.getAppPath(), '../..')
  bridge = new PythonBridge(projectRoot)
  bridge.onEvent((event) => mainWindow?.webContents.send('jixue:bridge-event', event))
  bridge.onState((state) => mainWindow?.webContents.send('jixue:bridge-state', state))
  bridge.start()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow()
    }
  })
})

app.on('before-quit', () => bridge?.stop())

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

