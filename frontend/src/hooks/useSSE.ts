// Orchestration Engine - SSE Hook
//
// Server-Sent Events hook for real-time project/task progress.
// Fetches a short-lived SSE token before connecting (never exposes
// the full access token in a URL).
//
// Depends on: types/index.ts, api/auth.ts, api/client.ts
// Used by:    pages/ProjectDetail.tsx

import { useEffect, useState, useRef } from 'react'
import type { SSEEvent } from '../types'
import { apiPost } from '../api/client'

const MAX_EVENTS = 200

interface SSEState {
  events: SSEEvent[]
  connected: boolean
}

export function useSSE(projectId: string | null): SSEState {
  const [state, setState] = useState<SSEState>({ events: [], connected: false })
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!projectId) return

    let cancelled = false

    async function connect() {
      try {
        // Fetch a short-lived SSE token scoped to this project
        const { token } = await apiPost<{ token: string }>(
          `/events/${projectId}/token`
        )
        if (cancelled) return

        const url = `/api/events/${projectId}?token=${encodeURIComponent(token)}`
        const source = new EventSource(url)
        sourceRef.current = source

        source.onopen = () => {
          setState(prev => ({ ...prev, connected: true }))
        }

        const handleEvent = (e: Event) => {
          try {
            const data = JSON.parse((e as MessageEvent).data) as SSEEvent
            setState(prev => ({
              ...prev,
              events: [...prev.events.slice(-MAX_EVENTS + 1), data],
            }))
          } catch { /* ignore parse errors */ }
        }

        for (const type of [
          'task_start', 'task_complete', 'task_failed', 'tool_call',
          'budget_warning', 'project_complete', 'project_failed',
        ]) {
          source.addEventListener(type, handleEvent)
        }

        source.onerror = () => {
          setState(prev => ({ ...prev, connected: false }))
          // Don't close — let EventSource auto-reconnect
        }
      } catch {
        setState(prev => ({ ...prev, connected: false }))
      }
    }

    connect()

    return () => {
      cancelled = true
      if (sourceRef.current) {
        sourceRef.current.close()
        sourceRef.current = null
      }
    }
  }, [projectId])

  return state
}
