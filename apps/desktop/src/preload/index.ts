/** Renderer 只能通过这里暴露的四个方法访问 Electron。 */

import { contextBridge, ipcRenderer } from 'electron'
import type { BridgeEnvelope, BridgeState, JixueDesktopApi } from '../shared/protocol'

function subscribe<T>(channel: string, listener: (value: T) => void): () => void {
  const handler = (_event: Electron.IpcRendererEvent, value: T): void => listener(value)
  ipcRenderer.on(channel, handler)
  return () => ipcRenderer.removeListener(channel, handler)
}

const api: JixueDesktopApi = {
  sendChat: (requestId, text) => ipcRenderer.invoke('jixue:send-chat', requestId, text),
  getBridgeState: () => ipcRenderer.invoke('jixue:get-bridge-state'),
  onBridgeEvent: (listener) => subscribe<BridgeEnvelope>('jixue:bridge-event', listener),
  onBridgeState: (listener) => subscribe<BridgeState>('jixue:bridge-state', listener)
}

contextBridge.exposeInMainWorld('jixue', api)
