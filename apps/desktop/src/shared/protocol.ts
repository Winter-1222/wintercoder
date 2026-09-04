/** Electron 与 Python 共用的最小协议类型。 */

export const PROTOCOL_VERSION = 1

export type BridgeStatus = 'starting' | 'ready' | 'offline' | 'error'
export type McpServerStatus = 'connecting' | 'connected' | 'failed'
export type AgentMode = 'plan' | 'do'
export type PermissionMode = 'confirm_edits' | 'ask_all' | 'auto_allow'

export interface McpServerState {
  name: string
  status: McpServerStatus
  detail: string
  toolCount: number
}

export interface BridgeState {
  status: BridgeStatus
  detail: string
  mcpServers: McpServerState[]
}

export interface BridgeEnvelope {
  version: number
  type: string
  request_id: string
  sequence: number
  timestamp: string
  payload: Record<string, unknown>
}

export interface JixueDesktopApi {
  sendChat: (requestId: string, text: string) => Promise<void>
  cancelChat: (requestId: string) => Promise<void>
  respondPermission: (requestId: string, toolUseId: string, allow: boolean) => Promise<void>
  setAgentMode: (mode: AgentMode) => Promise<void>
  setPermissionMode: (mode: PermissionMode) => Promise<void>
  getBridgeState: () => Promise<BridgeState>
  onBridgeEvent: (listener: (event: BridgeEnvelope) => void) => () => void
  onBridgeState: (listener: (state: BridgeState) => void) => () => void
}

export function isBridgeEnvelope(value: unknown): value is BridgeEnvelope {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<BridgeEnvelope>
  return (
    item.version === PROTOCOL_VERSION &&
    typeof item.type === 'string' &&
    typeof item.request_id === 'string' &&
    typeof item.sequence === 'number' &&
    typeof item.timestamp === 'string' &&
    !!item.payload &&
    typeof item.payload === 'object'
  )
}
