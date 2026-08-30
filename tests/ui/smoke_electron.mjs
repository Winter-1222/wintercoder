/** 启动真实 Electron，验证 Main、Preload、Python Bridge 与 Renderer。 */

import { mkdir } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { _electron as electron } from 'playwright-core'

const currentDirectory = dirname(fileURLToPath(import.meta.url))
const root = resolve(currentDirectory, '..', '..')
const desktop = resolve(root, 'apps', 'desktop')
const executablePath = resolve(root, 'node_modules', 'electron', 'dist', 'electron.exe')
const screenshotPath = resolve(root, 'artifacts', 'ui', 'electron-smoke.png')

await mkdir(dirname(screenshotPath), { recursive: true })

const consoleErrors = []
const application = await electron.launch({
  executablePath,
  args: [desktop],
  cwd: desktop,
  env: {
    ...process.env,
    JIXUE_PROJECT_ROOT: root,
    PYTHONIOENCODING: 'utf-8'
  }
})

try {
  const window = await application.firstWindow({ timeout: 30_000 })
  window.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text())
    }
  })
  window.on('pageerror', (error) => consoleErrors.push(String(error)))

  await window.waitForLoadState('domcontentloaded')
  await window.getByRole('heading', { name: '霁雪 Jixue' }).waitFor({ timeout: 30_000 })
  await window.getByText('FakeLLM / Bridge 在线').waitFor({ timeout: 30_000 })

  await window.getByLabel('输入消息').fill('真实桌面链路测试')
  await window.getByRole('button', { name: '发送' }).click()

  await window.getByRole('heading', { name: '霁雪已经醒来' }).waitFor({ timeout: 30_000 })
  await window.getByText('Python Bridge 正常').waitFor({ timeout: 30_000 })
  await window.getByText('fake-jixue').waitFor({ timeout: 30_000 })
  await window.screenshot({ path: screenshotPath, fullPage: true })
} finally {
  await application.close()
}

if (consoleErrors.length > 0) {
  throw new Error(`Electron Renderer 控制台出现错误：${consoleErrors.join('；')}`)
}
