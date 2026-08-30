import { describe, expect, it } from 'vitest'

import { chatReducer, initialChatState } from './state'

describe('chatReducer', () => {
  it('按流式顺序追加文本并在完成后收口', () => {
    const requestId = 'req_12345678-1234-1234'
    let state = chatReducer(initialChatState, {
      type: 'request_started',
      requestId,
      text: '你好',
      startedAt: 100
    })

    state = chatReducer(state, {
      type: 'text_received',
      requestId,
      messageId: 'msg_1',
      text: '雪'
    })
    state = chatReducer(state, {
      type: 'text_received',
      requestId,
      messageId: 'msg_1',
      text: '停了'
    })
    state = chatReducer(state, {
      type: 'request_completed',
      requestId,
      durationMs: 320,
      model: 'fake-jixue'
    })

    expect(state.messages[1]).toMatchObject({
      content: '雪停了',
      status: 'complete'
    })
    expect(state.activeRequestId).toBeNull()
    expect(state.durationMs).toBe(320)
  })

  it('失败时保留已经收到的部分文本', () => {
    const requestId = 'req_12345678-1234-1234'
    let state = chatReducer(initialChatState, {
      type: 'request_started',
      requestId,
      text: '测试',
      startedAt: 100
    })
    state = chatReducer(state, {
      type: 'text_received',
      requestId,
      messageId: 'msg_2',
      text: '部分回复'
    })
    state = chatReducer(state, {
      type: 'request_failed',
      requestId,
      message: '连接中断'
    })

    expect(state.messages[1]).toMatchObject({
      content: '部分回复',
      status: 'failed'
    })
  })
})

