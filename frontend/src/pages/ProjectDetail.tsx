// Orchestration Engine - Project Detail Page

import { useEffect, useState, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  getProject, listPlans, listTasks, fetchCoverage, fetchCheckpoints,
  generatePlan, approvePlan, startExecution, pauseExecution, cancelProject,
  resolveCheckpoint,
} from '../api/projects'
import { useSSE } from '../hooks/useSSE'
import { useFetch } from '../hooks/useFetch'
import type { Project, Plan, Task, Checkpoint, CoverageReport } from '../types'

interface ProjectData {
  project: Project
  plans: Plan[]
  tasks: Task[]
  coverage: CoverageReport | null
  checkpoints: Checkpoint[]
}

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>()
  const [loading, setLoading] = useState('')
  const [actionError, setActionError] = useState('')

  const { data, error: fetchError, refetch } = useFetch<ProjectData>(
    () => Promise.all([
      getProject(id!),
      listPlans(id!),
      listTasks(id!),
      fetchCoverage(id!).catch(() => null),
      fetchCheckpoints(id!).catch(() => []),
    ]).then(([project, plans, tasks, coverage, checkpoints]) => ({
      project, plans, tasks, coverage, checkpoints,
    })),
    [id],
  )

  const project = data?.project ?? null
  const plans = data?.plans ?? []
  const tasks = data?.tasks ?? []
  const coverage = data?.coverage ?? null
  const checkpoints = data?.checkpoints ?? []
  const error = actionError || fetchError

  const sse = useSSE(project?.status === 'executing' ? id! : null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Checkpoint resolve state
  const [resolveId, setResolveId] = useState<string | null>(null)
  const [resolveGuidance, setResolveGuidance] = useState('')

  // Auto-refresh on SSE events (debounced to avoid request storms)
  useEffect(() => {
    if (sse.events.length === 0) return
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => { refetch() }, 2000)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [sse.events.length, refetch])

  const action = async (label: string, fn: () => Promise<unknown>) => {
    setLoading(label)
    setActionError('')
    try {
      await fn()
      refetch()
    } catch (e) {
      setActionError(String(e))
    }
    setLoading('')
  }

  const handleResolve = async (checkpointId: string, resolveAction: string) => {
    setLoading(`resolve-${checkpointId}`)
    setActionError('')
    try {
      await resolveCheckpoint(checkpointId, resolveAction, resolveGuidance)
      setResolveId(null)
      setResolveGuidance('')
      refetch()
    } catch (e) {
      setActionError(String(e))
    }
    setLoading('')
  }

  if (!id) return <div className="text-dim">Invalid URL — missing project ID.</div>
  if (error && !project) return <div className="card" style={{ borderColor: 'var(--error)' }}>Error: {error}</div>
  if (!project) return <div className="text-dim">Loading...</div>

  const latestPlan = plans[0]
  const draftPlan = plans.find(p => p.status === 'draft')
  const unresolvedCheckpoints = checkpoints.filter(c => !c.resolved_at)

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

      {/* Requirements + Coverage */}
      <div className="card mb-2">
        <h3>Requirements</h3>
        <p style={{ whiteSpace: 'pre-wrap' }}>{project.requirements}</p>
        {coverage && coverage.total_requirements > 0 && (
          <div style={{ marginTop: '0.75rem' }}>
            <div className="flex-between mb-1">
              <span className="text-sm text-dim">Requirement Coverage</span>
              <span className="text-sm">{coverage.covered_count}/{coverage.total_requirements}</span>
            </div>
            <div className="progress-bar">
              <div className="progress-fill ok"
                style={{ width: `${(coverage.covered_count / coverage.total_requirements) * 100}%` }} />
            </div>
            {coverage.uncovered_count > 0 && (
              <div style={{ marginTop: '0.5rem' }}>
                {coverage.requirements.filter(r => !r.covered).map(r => (
                  <div key={r.id} className="text-sm" style={{ color: 'var(--warning)', padding: '0.125rem 0' }}>
                    [{r.id}] {r.text}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
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

      {/* Checkpoints */}
      {unresolvedCheckpoints.length > 0 && (
        <div className="card mb-2" style={{ borderColor: 'var(--warning)' }}>
          <h3>Checkpoints ({unresolvedCheckpoints.length} unresolved)</h3>
          {unresolvedCheckpoints.map(cp => (
            <div key={cp.id} className="checkpoint-item" style={{
              padding: '0.75rem', marginBottom: '0.5rem',
              background: 'var(--bg)', borderRadius: 'var(--radius)',
            }}>
              <div className="flex-between mb-1">
                <span className="text-sm" style={{ fontWeight: 600 }}>{cp.summary}</span>
                <span className={`badge ${cp.checkpoint_type === 'retry_exhausted' ? 'failed' : 'pending'}`}>
                  {cp.checkpoint_type.replace('_', ' ')}
                </span>
              </div>
              <p className="text-sm mb-1" style={{ color: 'var(--warning)' }}>{cp.question}</p>
              {resolveId === cp.id ? (
                <div>
                  <div className="form-group">
                    <textarea value={resolveGuidance} onChange={e => setResolveGuidance(e.target.value)}
                      placeholder="Optional guidance..." style={{ minHeight: '50px' }} />
                  </div>
                  <div className="flex gap-1">
                    <button className="btn btn-primary btn-sm" onClick={() => handleResolve(cp.id, 'retry')}
                      disabled={!!loading}>Retry</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => handleResolve(cp.id, 'skip')}
                      disabled={!!loading}>Skip</button>
                    <button className="btn btn-danger btn-sm" onClick={() => handleResolve(cp.id, 'fail')}
                      disabled={!!loading}>Fail</button>
                    <button className="btn btn-sm" style={{ background: 'transparent', color: 'var(--text-dim)' }}
                      onClick={() => { setResolveId(null); setResolveGuidance('') }}>Cancel</button>
                  </div>
                </div>
              ) : (
                <button className="btn btn-secondary btn-sm" onClick={() => setResolveId(cp.id)}>
                  Resolve
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Tasks — grouped by wave */}
      {tasks.length > 0 && (() => {
        const waves = new Map<number, Task[]>()
        for (const t of tasks) {
          const w = t.wave ?? 0
          if (!waves.has(w)) waves.set(w, [])
          waves.get(w)!.push(t)
        }
        const sortedWaves = [...waves.entries()].sort((a, b) => a[0] - b[0])
        const hasMultipleWaves = sortedWaves.length > 1

        return (
          <div className="card">
            <h3>Tasks ({tasks.filter(t => t.status === 'completed').length}/{tasks.length})</h3>
            {sortedWaves.map(([wave, waveTasks]) => {
              const completed = waveTasks.filter(t => t.status === 'completed').length
              const allDone = completed === waveTasks.length

              return (
                <div key={wave} className="wave-group">
                  {hasMultipleWaves && (
                    <div className="wave-header">
                      <h4>Wave {wave} ({waveTasks.length} task{waveTasks.length !== 1 ? 's' : ''})</h4>
                      <span className="wave-summary">
                        {allDone ? 'complete' : `${completed}/${waveTasks.length} done`}
                      </span>
                    </div>
                  )}
                  <table>
                    <thead>
                      <tr>
                        <th>Task</th><th>Type</th><th>Model</th><th>Status</th><th>Cost</th><th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {waveTasks.map(t => (
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
              )
            })}
          </div>
        )
      })()}

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
