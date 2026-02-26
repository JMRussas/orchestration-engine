// Orchestration Engine - Services Page

import { useEffect, useState } from 'react'
import { listServices } from '../api/services'
import type { Resource } from '../types'

export default function Services() {
  const [services, setServices] = useState<Resource[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const refresh = () => {
    setLoading(true)
    setError('')
    listServices()
      .then(setServices)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => { refresh() }, [])

  return (
    <>
      <div className="flex-between mb-2">
        <h2>Services</h2>
        <button className="btn btn-secondary btn-sm" onClick={refresh} disabled={loading}>
          {loading ? 'Checking...' : 'Refresh'}
        </button>
      </div>

      {error && <div className="card mb-2" style={{ borderColor: 'var(--error)' }}>Failed to load services: {error}</div>}

      <div className="grid grid-3">
        {services.map(s => (
          <div key={s.id} className="card">
            <div className="flex-between mb-1">
              <strong>{s.name}</strong>
              <span className={`badge ${s.status}`}>{s.status}</span>
            </div>
            <div className="text-sm text-dim">
              <span>Category: {s.category}</span>
              {s.method && <span> | Check: {s.method}</span>}
            </div>
            {s.details && Object.keys(s.details).length > 0 && (
              <pre className="text-sm" style={{ marginTop: '0.5rem' }}>
                {JSON.stringify(s.details, null, 2)}
              </pre>
            )}
          </div>
        ))}
      </div>
    </>
  )
}
