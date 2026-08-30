export const PROTOCOL_VERSION = 1

export type BridgeStatus = 'starting' | 'ready' | 'offline' | 'error'

export interface BridgeEnvelope {
  version: number
  type: string
  request_id: string
  sequence: number
  timestamp: string
  payload: Record<string, unknown>
}

export interface BridgeState {
  status: BridgeStatus
  detail: string
}

export interface JixueDesktopApi {
  sendChat: (requestId: string, text: string) => Promise<void>
  getBridgeState: () => Promise<BridgeState>
  onBridgeEvent: (listener: (event: BridgeEnvelope) => void) => () => void
  onBridgeState: (listener: (state: BridgeState) => void) => () => void
}

export function isBridgeEnvelope(value: unknown): value is BridgeEnvelope {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const candidate = value as Partial<BridgeEnvelope>
  return (
    candidate.version === PROTOCOL_VERSION &&
    typeof candidate.type === 'string' &&
    typeof candidate.request_id === 'string' &&
    typeof candidate.sequence === 'number' &&
    typeof candidate.timestamp === 'string' &&
    typeof candidate.payload === 'object' &&
    candidate.payload !== null
  )
}

