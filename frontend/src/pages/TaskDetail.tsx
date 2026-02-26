// Orchestration Engine - Task Detail Page

import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { apiFetch } from '../api/client'
import type { Task } from '../types'

export default function TaskDetail() {
  const { id, taskId } = useParams<{ id: string; taskId: string }>()
  const [task, setTask] = useState<Task | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (taskId) {
      apiFetch<Task>(`/tasks/${taskId}`)
        .then(setTask)
        .catch(e => setError(String(e)))
    }
  }, [taskId])

  if (!id || !taskId) return <div className="text-dim">Invalid URL — missing project or task ID.</div>
  if (error) return <div className="card" style={{ borderColor: 'var(--error)' }}>Error loading task: {error}</div>
  if (!task) return <div className="text-dim">Loading...</div>

  return (
    <>
      <Link to={`/project/${id}`} className="text-dim text-sm">&larr; Back to project</Link>
      <div className="flex-between mb-2">
        <h2>{task.title}</h2>
        <span className={`badge ${task.status}`}>{task.status}</span>
      </div>

      <div className="grid grid-4 mb-2">
        <div className="card">
          <h3>Model</h3>
          <span className={`badge ${task.model_tier}`}>{task.model_tier}</span>
          {task.model_used && <span className="text-dim text-sm" style={{ marginLeft: '0.5rem' }}>{task.model_used}</span>}
        </div>
        <div className="card">
          <h3>Cost</h3>
          <span className="cost">${task.cost_usd.toFixed(4)}</span>
        </div>
        <div className="card">
          <h3>Tokens</h3>
          <span className="text-sm">{task.prompt_tokens} in / {task.completion_tokens} out</span>
        </div>
        <div className="card">
          <h3>Type</h3>
          <span>{task.task_type}</span>
        </div>
      </div>

      <div className="card mb-2">
        <h3>Description</h3>
        <p style={{ whiteSpace: 'pre-wrap' }}>{task.description}</p>
      </div>

      {task.tools.length > 0 && (
        <div className="card mb-2">
          <h3>Tools</h3>
          <div className="flex gap-1">
            {task.tools.map(t => <span key={t} className="badge">{t}</span>)}
          </div>
        </div>
      )}

      {task.error && (
        <div className="card mb-2" style={{ borderColor: 'var(--error)' }}>
          <h3>Error</h3>
          <pre>{task.error}</pre>
        </div>
      )}

      {task.output_text && (
        <div className="card">
          <h3>Output</h3>
          <pre style={{ maxHeight: '60vh', overflowY: 'auto' }}>{task.output_text}</pre>
        </div>
      )}
    </>
  )
}
