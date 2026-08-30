/** Renderer 的浏览器入口：把根组件 App 挂载到 index.html 的 #root 节点。 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import './styles.css'

const root = document.getElementById('root')
if (!root) {
  // 没有挂载节点说明 index.html 与入口约定不一致，继续运行只会得到空白页面。
  throw new Error('找不到应用挂载节点')
}

createRoot(root).render(
  // StrictMode 只在开发期帮助发现不安全副作用，不会在生产界面额外渲染元素。
  <StrictMode>
    <App />
  </StrictMode>
)
