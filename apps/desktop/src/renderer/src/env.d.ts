/// <reference types="vite/client" />

import type { JixueDesktopApi } from '../../shared/protocol'

declare global {
  interface Window {
    jixue: JixueDesktopApi
  }
}

export {}

