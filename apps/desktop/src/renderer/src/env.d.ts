/// <reference types="vite/client" />

/**
 * Renderer 的全局类型补充。
 * Preload 在运行时创建 window.jixue；这个声明让 TypeScript 在编译时也认识它。
 */

import type { JixueDesktopApi } from '../../shared/protocol'

declare global {
  interface Window {
    /** 由 Preload 通过 contextBridge 暴露的安全桌面 API。 */
    jixue: JixueDesktopApi
  }
}

// export {} 让本文件成为模块，declare global 才会扩展全局类型而不是污染普通脚本作用域。
export {}
