// Orchestration Engine - Project Detail Page

import { useEffect, useState, useRef, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  getProject, listPlans, listTasks,
  generatePlan, approvePlan, startExecution, pauseExecution, cancelProject,
} from '../api/projects'
import { useSSE } from '../hooks/useSSE'
import type { Project, Plan, Task } from '../types'

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>()
  const [project, setProject] = useState<Project | null>(null)
  const [plans, setPlans] = useState<Plan[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState('')
  const [error, setError] = useState('')
  const sse = useSSE(project?.status === 'executing' ? id! : null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const refresh = useCallback(async () => {
    if (!id) return
    try {
      const [p, pl, t] = await Promise.all([
        getProject(id), listPlans(id), listTasks(id),
      ])
      setProject(p)
      setPlans(pl)
      setTasks(t)
    } catch (e) {
      setError(String(e))
    }
  }, [id])

  useEffect(() => { refresh() }, [refresh])

  // Auto-refresh on SSE events (debounced to avoid request storms)
  useEffect(() => {
    if (sse.events.length === 0) return
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => { refresh() }, 2000)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [sse.events.length, refresh])

  const action = async (label: string, fn: () => Promise<unknown>) => {
    setLoading(label)
    setError('')
    try {
      await fn()
      await refresh()
    } catch (e) {
      setError(String(e))
    }
    setLoading('')
  }

  if (!id) return <div className="text-dim">Invalid URL — missing project ID.</div>
  if (error && !project) return <div className="card" style={{ borderColor: 'var(--error)' }}>Error: {error}</div>
  if (!project) return <div className="text-dim">Loading...</div>

  const latestPlan = plans[0]
  const draftPlan = plans.find(p => p.status === 'draft')

  return (
    <>
      <div className="flex-between mb-2">
        <div>
          <Link to="/" className="text-dim text-sm">&larr; Projects</Link>
          <h2>{project.name}</h2>
          <span className={`badge ${project.status}`}>{project.status}</span>
        </div>
        <div className="flex gap-1">
          {project.status === 'draft' && !draftPlan && (
            <button className="btn btn-primary" onClick={() => action('plan', () => generatePlan(id!))}
              disabled={!!loading}>{loading === 'plan' ? 'Planning...' : 'Generate Plan'}</button>
          )}
          {draftPlan && (
            <button className="btn btn-primary" onClick={() => action('approve', () => approvePlan(id!, draftPlan.id))}
              disabled={!!loading}>{loading === 'approve' ? 'Approving...' : 'Approve Plan'}</button>
          )}
          {project.status === 'ready' && (
            <button className="btn btn-primary" onClick={() => action('execute', () => startExecution(id!))}
              disabled={!!loading}>Start Execution</button>
          )}
          {project.status === 'executing' && (
            <button className="btn btn-secondary" onClick={() => action('pause', () => pauseExecution(id!))}
              disabled={!!loading}>Pause</button>
          )}
          {project.status === 'paused' && (
            <button className="btn btn-primary" onClick={() => action('resume', () => startExecution(id!))}
              disabled={!!loading}>Resume</button>
          )}
          {['executing', 'paused', 'ready'].includes(project.status) && (
            <button className="btn btn-danger btn-sm" onClick={() => action('cancel', () => cancelProject(id!))}
              disabled={!!loading}>Cancel</button>
          )}
        </div>
      </div>

      {error && <div className="card" style={{ borderColor: 'var(--error)' }}>{error}</div>}

      {/* Requirements */}
      <div className="card mb-2">
        <h3>Requirements</h3>
        <p style={{ whiteSpace: 'pre-wrap' }}>{project.requirements}</p>
      </div>

      {/* Plan */}
      {latestPlan && (
        <div className="card mb-2">
          <div className="flex-between">
            <h3>Plan v{latestPlan.version} <span className={`badge ${latestPlan.status}`}>{latestPlan.status}</span></h3>
            <span className="text-sm text-dim">
              {latestPlan.model_used} | {latestPlan.prompt_tokens + latestPlan.completion_tokens} tokens |
              <span className="cost"> ${latestPlan.cost_usd.toFixed(4)}</span>
            </span>
          </div>
          <p className="text-dim mb-1">{latestPlan.plan.summary}</p>
        </div>
      )}

      {/* Tasks */}
      {tasks.length > 0 && (
        <div className="card">
          <h3>Tasks ({tasks.filter(t => t.status === 'completed').length}/{tasks.length})</h3>
          <table>
            <thead>
              <tr>
                <th>Task</th><th>Type</th><th>Model</th><th>Status</th><th>Cost</th><th></th>
              </tr>
            </thead>
            <tbody>
              {tasks.map(t => (
                <tr key={t.id}>
                  <td>
                    <Link to={`/project/${id}/task/${t.id}`}>{t.title}</Link>
                    {t.depends_on.length > 0 && (
                      <span className="text-dim text-sm"> ({t.depends_on.length} deps)</span>
                    )}
                  </td>
                  <td className="text-sm">{t.task_type}</td>
                  <td><span className={`badge ${t.model_tier}`}>{t.model_tier}</span></td>
                  <td><span className={`badge ${t.status}`}>{t.status}</span></td>
                  <td className="text-sm cost">{t.cost_usd > 0 ? `$${t.cost_usd.toFixed(4)}` : '—'}</td>
                  <td className="text-sm text-dim">{t.model_used || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* SSE Events */}
      {sse.events.length > 0 && (
        <div className="card" style={{ marginTop: '1rem' }}>
          <h3>Live Events {sse.connected && <span className="badge running">connected</span>}</h3>
          <div className="event-log">
            {sse.events.map((e, i) => (
              <div key={i} className="event-item">
                <span className="event-type">{e.type}</span>
                <span>{e.message}</span>
                <span className="text-dim text-sm" style={{ marginLeft: '0.5rem' }}>
                  {new Date(e.timestamp * 1000).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}
