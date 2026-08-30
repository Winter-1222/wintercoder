/**
 * electron-vite 构建配置。
 * Electron 的 Main、Preload、Renderer 运行环境不同，所以分别构建，不打成同一个文件。
 */

import { resolve } from 'node:path'

import react from '@vitejs/plugin-react'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'

export default defineConfig({
  main: {
    // Electron/Node 依赖保持外部引用，避免把整套运行时重复打进 Main 输出。
    plugins: [externalizeDepsPlugin()]
  },
  preload: {
    // Preload 同样运行在 Electron 环境，依赖处理方式与 Main 一致。
    plugins: [externalizeDepsPlugin()]
  },
  renderer: {
    resolve: {
      alias: {
        // 别名用于以后减少很长的相对路径；当前代码仍以清晰相对路径为主。
        '@renderer': resolve('src/renderer/src'),
        '@shared': resolve('src/shared')
      }
    },
    // React 插件负责 JSX 转换和开发模式热更新。
    plugins: [react()]
  }
})
