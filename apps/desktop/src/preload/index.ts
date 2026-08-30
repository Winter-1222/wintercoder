/**
 * Preload 安全桥。
 *
 * Renderer 不能直接访问 ipcRenderer；本文件只把四个经过设计的领域方法暴露为 window.jixue。
 * 以后即使 Main 的 IPC 实现改变，页面仍然只依赖 JixueDesktopApi。
 */

import { contextBridge, ipcRenderer } from 'electron'

import type {
  BridgeEnvelope,
  BridgeState,
  JixueDesktopApi
} from '../shared/protocol'

/** Renderer 唯一允许使用的桌面能力白名单。 */
const api: JixueDesktopApi = {
  // invoke 是一次“请求—响应”调用；Main 校验后会把消息继续送入 Python Bridge。
  sendChat: (requestId, text) => ipcRenderer.invoke('jixue:send-chat', requestId, text),
  // 页面挂载时读取一次状态快照，避免错过订阅建立前已经到达的 ready 事件。
  getBridgeState: () => ipcRenderer.invoke('jixue:get-bridge-state'),
  onBridgeEvent: (listener) => {
    // handler 去掉 Electron 自己的 event 参数，只把霁雪领域信封交给 Renderer。
    const handler = (_event: Electron.IpcRendererEvent, payload: BridgeEnvelope): void => {
      listener(payload)
    }
    ipcRenderer.on('jixue:bridge-event', handler)
    // 返回取消函数，让 React useEffect 在卸载时移除同一个 handler。
    return () => ipcRenderer.removeListener('jixue:bridge-event', handler)
  },
  onBridgeState: (listener) => {
    // 状态与业务事件分开订阅，页面无需从文本事件推断后端是否在线。
    const handler = (_event: Electron.IpcRendererEvent, payload: BridgeState): void => {
      listener(payload)
    }
    ipcRenderer.on('jixue:bridge-state', handler)
    return () => ipcRenderer.removeListener('jixue:bridge-state', handler)
  }
}

// contextIsolation 开启时，通过 contextBridge 才能把受控对象安全挂到页面 window 上。
contextBridge.exposeInMainWorld('jixue', api)
