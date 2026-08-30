/**
 * Electron Main 进程入口。
 *
 * Main 是桌面应用中权限最高的 JavaScript 进程，负责创建窗口、注册 IPC、
 * 启动 Python Bridge 和处理退出。它不渲染聊天内容，也不理解 LLM 业务。
 */

import { app, BrowserWindow, ipcMain } from 'electron'
import { join, resolve } from 'node:path'

import { PythonBridge } from './bridge-process'

/** 当前唯一主窗口；null 表示窗口尚未创建或已经关闭。 */
let mainWindow: BrowserWindow | null = null
/** 当前 Python Bridge 管理器；应用 ready 后创建。 */
let bridge: PythonBridge | null = null

/** 创建并配置霁雪主窗口，但不负责启动 Python。 */
function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1180,
    height: 800,
    minWidth: 860,
    minHeight: 620,
    backgroundColor: '#f7f7f5',
    show: false,
    title: '霁雪 · Jixue',
    webPreferences: {
      // Preload 是高权限 Main 与低权限 Renderer 之间唯一允许的窄桥梁。
      preload: join(__dirname, '../preload/index.js'),
      // 把 Preload 和网页的 JavaScript 全局环境隔离，防止页面直接取得高权限对象。
      contextIsolation: true,
      // Renderer 只负责 UI，不允许直接 require Node 模块或启动系统命令。
      nodeIntegration: false,
      sandbox: true
    }
  })

  // 等页面完成首次绘制后再显示窗口，避免用户看到短暂白屏。
  window.once('ready-to-show', () => window.show())
  window.once('closed', () => {
    // 及时清空引用，Bridge 的迟到事件就不会向已经销毁的 webContents 发送。
    if (mainWindow === window) {
      mainWindow = null
    }
  })
  // 本章不需要弹出新窗口，任何 window.open 请求都直接拒绝。
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event, url) => {
    // 只允许当前页面自身加载；外部内容不能把主窗口导航到未知站点。
    if (url !== window.webContents.getURL()) {
      event.preventDefault()
    }
  })

  if (process.env.ELECTRON_RENDERER_URL) {
    // 开发模式加载 electron-vite 提供的热更新地址。
    void window.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    // 构建后没有开发服务器，改为加载本地生成的 renderer/index.html。
    void window.loadFile(join(__dirname, '../renderer/index.html'))
  }

  return window
}

/**
 * 确认 IPC 调用确实来自当前主窗口。
 * Renderer 传入的参数属于进程边界输入，Main 不能因为它来自“自己的页面”就省略检查。
 */
function senderIsMainWindow(event: Electron.IpcMainInvokeEvent): boolean {
  return (
    mainWindow !== null &&
    !mainWindow.isDestroyed() &&
    !mainWindow.webContents.isDestroyed() &&
    event.sender === mainWindow.webContents
  )
}

/**
 * 安全地向 Renderer 发送一个状态或业务事件。
 * 退出期间窗口和 Python 可能同时关闭，因此发送前必须同时检查窗口和 webContents。
 */
function sendToRenderer(channel: string, value: unknown): void {
  const window = mainWindow
  if (!window || window.isDestroyed() || window.webContents.isDestroyed()) {
    return
  }
  window.webContents.send(channel, value)
}

/** 注册 Renderer 可以通过 Preload 调用的两个 IPC 请求。 */
function registerIpc(): void {
  ipcMain.handle('jixue:get-bridge-state', (event) => {
    // 页面初次挂载时会读取一次快照，避免错过订阅建立之前的状态变化。
    if (!senderIsMainWindow(event)) {
      throw new Error('拒绝未知窗口读取 Bridge 状态')
    }
    return bridge?.getState() ?? { status: 'offline', detail: 'Bridge 未创建' }
  })

  ipcMain.handle('jixue:send-chat', (event, requestId: unknown, text: unknown) => {
    // Main 再次校验发送者和参数；Renderer 自己的 canSend 检查只属于用户体验层。
    if (!senderIsMainWindow(event)) {
      throw new Error('拒绝未知窗口发送消息')
    }
    if (typeof requestId !== 'string' || !/^req_[a-f0-9-]{16,}$/.test(requestId)) {
      throw new Error('requestId 格式无效')
    }
    if (typeof text !== 'string' || text.trim().length === 0 || text.length > 20_000) {
      throw new Error('消息必须是 1—20000 个字符')
    }
    // 通过校验后才把领域参数交给 PythonBridge，Main 不拼装模型请求。
    bridge?.sendChat(requestId, text)
  })
}

// app.whenReady() 是 Electron 的主启动点：只有框架准备好后才能安全创建 BrowserWindow。
app.whenReady().then(() => {
  mainWindow = createWindow()
  registerIpc()

  // 开发阶段 appPath 指向 apps/desktop；打包路径在发布章节单独处理。
  const projectRoot = process.env.JIXUE_PROJECT_ROOT ?? resolve(app.getAppPath(), '../..')
  bridge = new PythonBridge(projectRoot)
  // PythonBridge 只暴露领域事件和状态，Main 把它们原样转发给 Renderer。
  bridge.onEvent((event) => sendToRenderer('jixue:bridge-event', event))
  bridge.onState((state) => sendToRenderer('jixue:bridge-state', state))
  bridge.start()

  app.on('activate', () => {
    // macOS 常见行为：应用仍在运行但窗口被关掉时，点击 Dock 图标重新创建窗口。
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow()
    }
  })
})

// 应用真正退出前主动关闭 stdin 和子进程，避免残留 Python Bridge。
app.on('before-quit', () => bridge?.stop())

app.on('window-all-closed', () => {
  // Windows/Linux 关闭最后一个窗口就退出；macOS 通常保留应用，等待 activate。
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
