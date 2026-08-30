import { contextBridge, ipcRenderer } from 'electron'

import type {
  BridgeEnvelope,
  BridgeState,
  JixueDesktopApi
} from '../shared/protocol'

const api: JixueDesktopApi = {
  sendChat: (requestId, text) => ipcRenderer.invoke('jixue:send-chat', requestId, text),
  getBridgeState: () => ipcRenderer.invoke('jixue:get-bridge-state'),
  onBridgeEvent: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, payload: BridgeEnvelope): void => {
      listener(payload)
    }
    ipcRenderer.on('jixue:bridge-event', handler)
    return () => ipcRenderer.removeListener('jixue:bridge-event', handler)
  },
  onBridgeState: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, payload: BridgeState): void => {
      listener(payload)
    }
    ipcRenderer.on('jixue:bridge-state', handler)
    return () => ipcRenderer.removeListener('jixue:bridge-state', handler)
  }
}

contextBridge.exposeInMainWorld('jixue', api)

