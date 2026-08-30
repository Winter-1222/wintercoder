"""使用本机 Chrome 验证 Renderer 的真实布局与交互。"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import ConsoleMessage, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SCREENSHOT = ROOT / "artifacts" / "ui" / "renderer-smoke.png"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

MOCK_DESKTOP_API = """
(() => {
  const eventListeners = [];
  const stateListeners = [];
  const ready = { status: 'ready', detail: 'FakeLLM / Bridge 在线' };

  window.jixue = {
    getBridgeState: async () => ready,
    onBridgeState: (listener) => {
      stateListeners.push(listener);
      queueMicrotask(() => listener(ready));
      return () => {
        const index = stateListeners.indexOf(listener);
        if (index >= 0) stateListeners.splice(index, 1);
      };
    },
    onBridgeEvent: (listener) => {
      eventListeners.push(listener);
      return () => {
        const index = eventListeners.indexOf(listener);
        if (index >= 0) eventListeners.splice(index, 1);
      };
    },
    sendChat: async (requestId, text) => {
      const emit = (type, sequence, payload) => {
        const event = {
          version: 1,
          type,
          request_id: requestId,
          sequence,
          timestamp: new Date().toISOString(),
          payload
        };
        eventListeners.forEach((listener) => listener(event));
      };

      setTimeout(() => emit('stream_text', 0, {
        text: '## 霁雪已经醒来\\n\\n',
        message_id: 'msg_smoke'
      }), 40);
      setTimeout(() => emit('stream_text', 1, {
        text: '这是来自浏览器冒烟测试的 **流式回复**。',
        message_id: 'msg_smoke'
      }), 90);
      setTimeout(() => emit('usage', 2, {
        turn: { input_tokens: 3, output_tokens: 7 },
        cumulative: { input_tokens: 3, output_tokens: 7 }
      }), 120);
      setTimeout(() => emit('turn_complete', 3, {
        turn_index: 1,
        stop_reason: 'end_turn',
        duration_ms: 148,
        model: 'fake-jixue',
        message_id: 'msg_smoke'
      }), 150);
    }
  };
})();
"""


def collect_console_error(errors: list[str], message: ConsoleMessage) -> None:
    if message.type == "error":
        errors.append(message.text)


def verify_page(page: Page) -> None:
    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")

    page.get_by_role("heading", name="霁雪 Jixue").wait_for()
    page.get_by_text("FakeLLM / Bridge 在线").wait_for()
    page.get_by_label("输入消息").fill("你好，霁雪")
    page.get_by_role("button", name="发送").click()

    page.get_by_role("heading", name="霁雪已经醒来").wait_for()
    page.get_by_text("这是来自浏览器冒烟测试的 流式回复。").wait_for()
    page.get_by_text("7 tok").wait_for()


def main() -> None:
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(CHROME),
        )
        page = browser.new_page(viewport={"width": 1180, "height": 800})
        page.add_init_script(MOCK_DESKTOP_API)
        page.on("console", lambda message: collect_console_error(console_errors, message))
        page.on("pageerror", lambda error: console_errors.append(str(error)))

        verify_page(page)
        page.screenshot(path=str(SCREENSHOT), full_page=True)
        browser.close()

    if console_errors:
        raise AssertionError(f"Renderer 控制台出现错误：{console_errors}")


if __name__ == "__main__":
    main()

