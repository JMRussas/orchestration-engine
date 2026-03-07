// Orchestration Engine - Analytics Page
//
// Admin-only analytics: usage overview, cost breakdown, task outcomes, efficiency.
//
// Depends on: api/analytics.ts
// Used by:    App.tsx

import { useEffect, useState, useCallback } from 'react'
import {
  getCostBreakdown, getTaskOutcomes, getEfficiency, getUsageOverview,
  type CostBreakdown, type TaskOutcomes, type Efficiency, type UsageOverview,
} from '../api/analytics'

const PURPOSE_COLORS: Record<string, string> = {
  execution: 'var(--accent)',
  planning: 'var(--warning)',
  verification: '#ab47bc',
  knowledge_extraction: '#66bb6a',
}

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: 'var(--accent)',
  ollama: '#66bb6a',
}

const DAY_OPTIONS = [7, 14, 30, 90]

const pctBar = (value: number, className = 'ok') => (
  <div className="progress-bar" role="progressbar"
    aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}
    style={{ width: '100px', display: 'inline-block', verticalAlign: 'middle' }}>
    <div className={`progress-fill ${className}`} style={{ width: `${Math.min(value * 100, 100)}%` }} />
  </div>
)

const pctClass = (rate: number) => rate >= 0.8 ? 'ok' : rate >= 0.5 ? 'warn' : 'danger'

const fmtTokens = (n: number) => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

const purposeLabel = (p: string) =>
  p.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

export default function Analytics() {
  const [days, setDays] = useState(30)
  const [overview, setOverview] = useState<UsageOverview | null>(null)
  const [cost, setCost] = useState<CostBreakdown | null>(null)
  const [outcomes, setOutcomes] = useState<TaskOutcomes | null>(null)
  const [efficiency, setEfficiency] = useState<Efficiency | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const loadData = useCallback((d: number) => {
    setLoading(true)
    setError('')
    const errors: string[] = []
    Promise.allSettled([
      getUsageOverview(d).then(setOverview),
      getCostBreakdown(d).then(setCost),
      getTaskOutcomes().then(setOutcomes),
      getEfficiency().then(setEfficiency),
    ]).then(results => {
      for (const r of results) {
        if (r.status === 'rejected') errors.push(String(r.reason))
      }
      if (errors.length) setError(errors.join('; '))
      setLoading(false)
    })
  }, [])

  useEffect(() => { loadData(days) }, [days, loadData])

  if (loading) return <p className="text-dim">Loading analytics...</p>

  return (
    <>
      <div className="flex-between mb-2">
        <h2 style={{ margin: 0 }}>Analytics</h2>
        <div className="days-selector">
          {DAY_OPTIONS.map(d => (
            <button key={d} className={`btn btn-sm${d === days ? ' btn-active' : ''}`}
              onClick={() => setDays(d)}>
              {d}d
            </button>
          ))}
        </div>
      </div>

      {error && <div className="card mb-2" style={{ borderColor: 'var(--error)' }}>Failed to load: {error}</div>}

      {/* Usage Overview */}
      {overview && (
        <>
          {/* Summary Cards */}
          <div className="grid grid-4 mb-2">
            <div className="card stat-card">
              <span className="stat-label">Total Spend</span>
              <span className="stat-value cost">${overview.summary.total_cost_usd.toFixed(4)}</span>
            </div>
            <div className="card stat-card">
              <span className="stat-label">API Calls</span>
              <span className="stat-value">{overview.summary.total_api_calls.toLocaleString()}</span>
            </div>
            <div className="card stat-card">
              <span className="stat-label">Tokens Processed</span>
              <span className="stat-value">{fmtTokens(overview.summary.total_tokens)}</span>
            </div>
            <div className="card stat-card">
              <span className="stat-label">Active Projects</span>
              <span className="stat-value">{overview.summary.active_projects}</span>
            </div>
          </div>

          {/* Cost by Purpose */}
          <div className="grid grid-2 mb-2">
            <div className="card">
              <h4>Cost by Purpose</h4>
              {overview.by_purpose.length === 0 ? (
                <span className="text-dim">No data yet</span>
              ) : (
                <>
                  <div className="bar-chart mb-1">
                    {overview.by_purpose.map(p => (
                      <div key={p.purpose} className="bar-segment" title={`${purposeLabel(p.purpose)}: ${p.pct_of_total}%`}
                        style={{ width: `${Math.max(p.pct_of_total, 1)}%`, background: PURPOSE_COLORS[p.purpose] || 'var(--border)' }} />
                    ))}
                  </div>
                  <table>
                    <thead><tr><th>Purpose</th><th>Calls</th><th>Cost</th><th>%</th></tr></thead>
                    <tbody>
                      {overview.by_purpose.map(p => (
                        <tr key={p.purpose}>
                          <td>
                            <span className="color-dot" style={{ background: PURPOSE_COLORS[p.purpose] || 'var(--border)' }} />
                            {purposeLabel(p.purpose)}
                          </td>
                          <td>{p.api_calls}</td>
                          <td className="cost">${p.cost_usd.toFixed(4)}</td>
                          <td className="text-dim">{p.pct_of_total.toFixed(1)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </div>

            {/* Cost by Provider */}
            <div className="card">
              <h4>Cost by Provider</h4>
              {overview.by_provider.length === 0 ? (
                <span className="text-dim">No data yet</span>
              ) : (
                <>
                  <div className="bar-chart mb-1">
                    {overview.by_provider.map(p => (
                      <div key={p.provider} className="bar-segment" title={`${p.provider}: ${p.pct_of_total}%`}
                        style={{ width: `${Math.max(p.pct_of_total, 1)}%`, background: PROVIDER_COLORS[p.provider] || 'var(--border)' }} />
                    ))}
                  </div>
                  <table>
                    <thead><tr><th>Provider</th><th>Calls</th><th>In Tokens</th><th>Out Tokens</th><th>Cost</th><th>%</th></tr></thead>
                    <tbody>
                      {overview.by_provider.map(p => (
                        <tr key={p.provider}>
                          <td>
                            <span className="color-dot" style={{ background: PROVIDER_COLORS[p.provider] || 'var(--border)' }} />
                            {p.provider}
                          </td>
                          <td>{p.api_calls}</td>
                          <td className="text-dim">{fmtTokens(p.prompt_tokens)}</td>
                          <td className="text-dim">{fmtTokens(p.completion_tokens)}</td>
                          <td className="cost">${p.cost_usd.toFixed(4)}</td>
                          <td className="text-dim">{p.pct_of_total.toFixed(1)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </div>
          </div>

          {/* Cost by Model */}
          {overview.by_model.length > 0 && (
            <div className="card mb-2">
              <h4>Cost by Model (top 10)</h4>
              <table>
                <thead><tr><th>Model</th><th>Provider</th><th>Calls</th><th>Cost</th><th></th><th>%</th></tr></thead>
                <tbody>
                  {overview.by_model.map(m => (
                    <tr key={`${m.model}-${m.provider}`}>
                      <td className="text-sm">{m.model}</td>
                      <td><span className={`badge ${m.provider === 'ollama' ? 'ollama' : 'sonnet'}`}>{m.provider}</span></td>
                      <td>{m.api_calls}</td>
                      <td className="cost">${m.cost_usd.toFixed(4)}</td>
                      <td style={{ width: '120px' }}>
                        <div className="progress-bar" style={{ width: '100%' }}>
                          <div className="progress-fill ok" style={{ width: `${Math.min(m.pct_of_total, 100)}%` }} />
                        </div>
                      </td>
                      <td className="text-dim">{m.pct_of_total.toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {/* Cost Breakdown */}
      {cost && (
        <>
          <h3 className="mb-1">Cost Breakdown (by task)</h3>
          <div className="grid grid-2 mb-2">
            <div className="card">
              <h4>By Model Tier</h4>
              {cost.by_model_tier.length === 0 ? (
                <span className="text-dim">No data yet</span>
              ) : (
                <table>
                  <thead><tr><th>Tier</th><th>Tasks</th><th>Total Cost</th><th>Avg/Task</th></tr></thead>
                  <tbody>
                    {cost.by_model_tier.map(t => (
                      <tr key={t.model_tier}>
                        <td><span className={`badge ${t.model_tier}`}>{t.model_tier}</span></td>
                        <td>{t.task_count}</td>
                        <td className="cost">${t.cost_usd.toFixed(4)}</td>
                        <td className="text-dim">${t.avg_cost_per_task.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="card">
              <h4>By Project (top 10)</h4>
              {cost.by_project.length === 0 ? (
                <span className="text-dim">No data yet</span>
              ) : (
                <table>
                  <thead><tr><th>Project</th><th>Tasks</th><th>Cost</th></tr></thead>
                  <tbody>
                    {cost.by_project.slice(0, 10).map(p => (
                      <tr key={p.project_id}>
                        <td>{p.project_name}</td>
                        <td>{p.task_count}</td>
                        <td className="cost">${p.cost_usd.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {cost.daily_trend.length > 0 && (
            <div className="card mb-2">
              <h4>Daily Trend</h4>
              <table>
                <thead><tr><th>Date</th><th>API Calls</th><th>Cost</th></tr></thead>
                <tbody>
                  {cost.daily_trend.map(d => (
                    <tr key={d.date}>
                      <td>{d.date}</td>
                      <td>{d.api_calls}</td>
                      <td className="cost">${d.cost_usd.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {/* Task Outcomes */}
      {outcomes && (
        <>
          <h3 className="mb-1">Task Outcomes</h3>
          <div className="grid grid-2 mb-2">
            <div className="card">
              <h4>Success Rate by Tier</h4>
              {outcomes.by_tier.length === 0 ? (
                <span className="text-dim">No data yet</span>
              ) : (
                <table>
                  <thead><tr><th>Tier</th><th>Total</th><th>OK</th><th>Failed</th><th>Review</th><th>Rate</th></tr></thead>
                  <tbody>
                    {outcomes.by_tier.map(t => (
                      <tr key={t.model_tier}>
                        <td><span className={`badge ${t.model_tier}`}>{t.model_tier}</span></td>
                        <td>{t.total}</td>
                        <td>{t.completed}</td>
                        <td>{t.failed}</td>
                        <td>{t.needs_review}</td>
                        <td>{(t.success_rate * 100).toFixed(0)}% {pctBar(t.success_rate, pctClass(t.success_rate))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="card">
              <h4>Verification Signal</h4>
              {outcomes.verification_by_tier.length === 0 ? (
                <span className="text-dim">No verified tasks yet</span>
              ) : (
                <table>
                  <thead><tr><th>Tier</th><th>Verified</th><th>Pass</th><th>Gaps</th><th>Human</th><th>Rate</th></tr></thead>
                  <tbody>
                    {outcomes.verification_by_tier.map(v => (
                      <tr key={v.model_tier}>
                        <td><span className={`badge ${v.model_tier}`}>{v.model_tier}</span></td>
                        <td>{v.total_verified}</td>
                        <td>{v.passed}</td>
                        <td>{v.gaps_found}</td>
                        <td>{v.human_needed}</td>
                        <td>{(v.pass_rate * 100).toFixed(0)}% {pctBar(v.pass_rate, pctClass(v.pass_rate))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </>
      )}

      {/* Efficiency */}
      {efficiency && (
        <>
          <h3 className="mb-1">Efficiency</h3>
          <div className="grid grid-2 mb-2">
            <div className="card">
              <h4>Retries by Tier</h4>
              {efficiency.retries_by_tier.length === 0 ? (
                <span className="text-dim">No data yet</span>
              ) : (
                <table>
                  <thead><tr><th>Tier</th><th>Tasks</th><th>W/ Retries</th><th>Total Retries</th><th>Rate</th></tr></thead>
                  <tbody>
                    {efficiency.retries_by_tier.map(r => (
                      <tr key={r.model_tier}>
                        <td><span className={`badge ${r.model_tier}`}>{r.model_tier}</span></td>
                        <td>{r.total_tasks}</td>
                        <td>{r.tasks_with_retries}</td>
                        <td>{r.total_retries}</td>
                        <td>{(r.retry_rate * 100).toFixed(0)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div className="mt-1 text-dim">
                Checkpoints: {efficiency.checkpoint_count} total, {efficiency.unresolved_checkpoint_count} unresolved
              </div>
            </div>
            <div className="card">
              <h4>Wave Throughput</h4>
              {efficiency.wave_throughput.length === 0 ? (
                <span className="text-dim">No completed tasks yet</span>
              ) : (
                <table>
                  <thead><tr><th>Project</th><th>Wave</th><th>Tasks</th><th>Avg Duration</th></tr></thead>
                  <tbody>
                    {efficiency.wave_throughput.map(w => (
                      <tr key={`${w.project_id}-${w.wave}`}>
                        <td>{w.project_name}</td>
                        <td>Wave {w.wave}</td>
                        <td>{w.task_count}</td>
                        <td>{w.avg_duration_seconds != null ? `${w.avg_duration_seconds.toFixed(1)}s` : '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {efficiency.cost_efficiency.length > 0 && (
            <div className="card mb-2">
              <h4>Cost Efficiency</h4>
              <table>
                <thead><tr><th>Tier</th><th>Total Cost</th><th>Completed</th><th>Verified Pass</th><th>Cost/Pass</th></tr></thead>
                <tbody>
                  {efficiency.cost_efficiency.map(c => (
                    <tr key={c.model_tier}>
                      <td><span className={`badge ${c.model_tier}`}>{c.model_tier}</span></td>
                      <td className="cost">${c.cost_usd.toFixed(4)}</td>
                      <td>{c.tasks_completed}</td>
                      <td>{c.verification_pass_count}</td>
                      <td className="cost">{c.cost_per_pass != null ? `$${c.cost_per_pass.toFixed(4)}` : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  )
}
