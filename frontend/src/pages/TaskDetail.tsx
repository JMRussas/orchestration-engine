// Orchestration Engine - Task Detail Page

import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { reviewTask } from '../api/projects'
import { useFetch } from '../hooks/useFetch'
import type { Task } from '../types'

export default function TaskDetail() {
  const { id, taskId } = useParams<{ id: string; taskId: string }>()
  const { data: task, error, refetch } = useFetch<Task>(
    () => apiFetch<Task>(`/tasks/${taskId}`),
    [taskId],
  )
  const [feedback, setFeedback] = useState('')
  const [actionLoading, setActionLoading] = useState('')
  const [actionError, setActionError] = useState('')

  if (!id || !taskId) return <div className="text-dim">Invalid URL — missing project or task ID.</div>
  if (error) return <div className="card" style={{ borderColor: 'var(--error)' }}>Error loading task: {error}</div>
  if (!task) return <div className="text-dim">Loading...</div>

  const handleReview = async (action: 'approve' | 'retry') => {
    setActionLoading(action)
    setActionError('')
    try {
      await reviewTask(taskId!, action, action === 'retry' ? feedback : '')
      setFeedback('')
      refetch()
    } catch (e) {
      setActionError(String(e))
    }
    setActionLoading('')
  }

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

      {/* Review action panel */}
      {task.status === 'needs_review' && (
        <div className="card mb-2" style={{ borderColor: 'var(--warning)' }}>
          <h3>Review Required</h3>
          <p className="text-sm text-dim mb-1">
            This task needs your review. Approve the output or retry with feedback.
          </p>
          <div className="form-group">
            <label>Feedback (for retry)</label>
            <textarea value={feedback} onChange={e => setFeedback(e.target.value)}
              placeholder="Describe what needs to change..." style={{ minHeight: '60px' }} />
          </div>
          {actionError && <div className="text-sm mb-1" style={{ color: 'var(--error)' }}>{actionError}</div>}
          <div className="flex gap-1">
            <button className="btn btn-primary" onClick={() => handleReview('approve')}
              disabled={!!actionLoading}>
              {actionLoading === 'approve' ? 'Approving...' : 'Approve'}
            </button>
            <button className="btn btn-secondary" onClick={() => handleReview('retry')}
              disabled={!!actionLoading}>
              {actionLoading === 'retry' ? 'Retrying...' : 'Retry with Feedback'}
            </button>
          </div>
        </div>
      )}

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

      {/* Verification status */}
      {task.verification_status && (
        <div className={`verification-card ${task.verification_status}`}>
          <div className="flex-between mb-1">
            <h3 style={{ margin: 0 }}>Verification</h3>
            <span className={`badge ${task.verification_status}`}>
              {task.verification_status.replace('_', ' ')}
            </span>
          </div>
          {task.verification_notes && (
            <p className="text-sm" style={{ whiteSpace: 'pre-wrap' }}>{task.verification_notes}</p>
          )}
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
