/** 单条消息与工具卡片，不处理任务状态。 */
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { UiMessage } from './state'

export function MessageView({
  message,
  onPermission
}: {
  message: UiMessage
  onPermission: (message: UiMessage, allow: boolean) => Promise<void>
}): React.JSX.Element {
  if (message.role === 'tool') {
    const permissionBusy =
      message.permissionStatus === 'allowing' || message.permissionStatus === 'denying'
    const label =
      message.permissionStatus === 'pending'
        ? '需要确认'
        : permissionBusy
          ? '正在提交'
          : message.status === 'streaming'
            ? '工具执行中'
            : message.status === 'failed'
              ? '工具失败'
              : message.status === 'cancelled'
                ? '工具已停止'
                : '工具完成'
    const showPermission =
      message.status === 'streaming' &&
      (message.permissionStatus === 'pending' || permissionBusy)

    return (
      <details
        // 状态变化时重置展开状态；完成后可手动展开，等待确认时重新展开。
        key={message.status + (showPermission ? '_permission' : '')}
        className="tool-message"
        data-status={message.status}
        data-permission={message.permissionStatus}
        open={message.status === 'streaming'}
      >
        <summary className="tool-summary">
          <span className="tool-chevron" aria-hidden="true">▸</span>
          <strong>{message.name}</strong>
          <span className="tool-status">{label}</span>
          {message.durationMs !== undefined && <small>{message.durationMs} 毫秒</small>}
        </summary>
        <div className="tool-details">
          {message.input && (
            <details open={showPermission}>
              <summary>查看输入参数</summary>
              <pre>{message.input}</pre>
            </details>
          )}
          {showPermission ? (
            <div className="permission-panel">
              <div>
                <strong>{message.isDestructive ? '可能修改项目' : '需要你的许可'}</strong>
                <p>{message.permissionReason || '此工具需要确认后才能执行。'}</p>
              </div>
              <div className="permission-actions">
                <button
                  className="permission-deny"
                  aria-label={'拒绝 ' + message.name}
                  disabled={permissionBusy}
                  onClick={() => void onPermission(message, false)}
                >
                  {message.permissionStatus === 'denying' ? '拒绝中…' : '拒绝'}
                </button>
                <button
                  className="permission-allow"
                  aria-label={'允许 ' + message.name}
                  disabled={permissionBusy}
                  onClick={() => void onPermission(message, true)}
                >
                  {message.permissionStatus === 'allowing' ? '允许中…' : '允许'}
                </button>
              </div>
            </div>
          ) : (
            <pre className="tool-output">{message.content}</pre>
          )}
        </div>
      </details>
    )
  }
  if (message.role === 'user') {
    return <article className="user-message">{message.content}</article>
  }
  const complete = message.status === 'complete'
  return (
    <article className="assistant-message">
      <span className="avatar">❄</span>
      <div>
        <header>
          <strong>霁雪</strong>
          <small>
            {message.status === 'cancelled'
              ? '已停止'
              : message.status === 'failed'
                ? '回复中断'
                : complete
                  ? '已完成'
                  : '正在回复'}
          </small>
        </header>
        <div className="message-body">
          {complete ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          ) : (
            <pre>{message.content || ' '}</pre>
          )}
          {/* 光标只代表“正在接收流”。已停止的消息不能继续闪，否则会让人误以为任务还没结束。 */}
          {message.status === 'streaming' && <span className="cursor" />}
        </div>
      </div>
    </article>
  )
}
