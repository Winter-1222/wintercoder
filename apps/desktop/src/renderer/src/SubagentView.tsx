/** 子任务快照单独展示，后台运行时也保持可见。 */
import { useState } from 'react'
import type { SubagentTaskInfo } from '../../shared/protocol'

const LABELS: Record<string, string> = {
  running: '执行中', completed: '已完成', failed: '失败', cancelled: '已停止',
  interrupted: '进程中断', incomplete: '未完成', timed_out: '已超时', permission: '等待确认'
}

export function SubagentView({ task }: { task: SubagentTaskInfo }): React.JSX.Element {
  const [pending, setPending] = useState('')
  const [error, setError] = useState('')
  const running = task.status === 'running'
  async function act(action: 'stop' | 'respond' | 'status', token = '', allow?: boolean): Promise<void> {
    setPending(token || 'stop')
    setError('')
    try {
      await window.jixue.subagent(action, task.session_id, task.agent_id, token, allow)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      setPending('')
    }
  }
  const needsPermission = task.trace.some((row) => !!row.permission_token)
  return (
    <details className="subagent-card" data-agent-id={task.agent_id} data-status={task.status}
      key={task.run_id + task.status + String(needsPermission)} open={running}
      onToggle={(event) => {
        // 回放只含摘要，用户展开时再取完整报告和近期工具轨迹。
        if (event.currentTarget.open && !running) void act('status')
      }}>
      <summary>
        <strong>{task.kind === 'fork' ? 'Fork' : task.role} 子任务</strong>
        <span>{needsPermission ? '等待确认' : LABELS[task.status] ?? task.status}</span>
        <small>{task.background ? '后台' : '前台'} · {task.model}</small>
      </summary>
      <div className="subagent-body">
        <p>{task.prompt}</p>
        <small>{task.agent_id} · {(task.duration_ms / 1000).toFixed(1)} 秒 ·
          输入 {task.usage.input_tokens} / 输出 {task.usage.output_tokens} Token</small>
        {running && <button disabled={!!pending} onClick={() => void act('stop')}>停止子任务</button>}
        {task.trace.map((row, index) => (
          <details className="subagent-tool" key={task.run_id + index + row.permission_token}
            open={!!row.permission_token}>
            <summary>{row.name} · {LABELS[row.status] ?? row.status}</summary>
            <pre>{JSON.stringify(row.input, null, 2)}</pre>
            <pre>{row.content}</pre>
            {running && row.permission_token && (
              <div className="permission-actions">
                <button disabled={!!pending} onClick={() => void act('respond', row.permission_token, false)}>
                  拒绝子任务操作
                </button>
                <button disabled={!!pending} onClick={() => void act('respond', row.permission_token, true)}>
                  允许子任务操作
                </button>
              </div>
            )}
          </details>
        ))}
        {task.report && <pre className="subagent-report">{task.report}</pre>}
        {error && <p role="alert">{error}</p>}
      </div>
    </details>
  )
}
