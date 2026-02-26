// Orchestration Engine - useFetch Hook
//
// Reusable data-fetching hook that encapsulates loading/error state
// and provides a refetch callback for manual refresh.
//
// Depends on: (none)
// Used by:    pages/Dashboard.tsx, pages/ProjectDetail.tsx, pages/TaskDetail.tsx

import { useEffect, useState, useCallback, useRef } from 'react'

interface UseFetchResult<T> {
  data: T | null
  loading: boolean
  error: string | null
  refetch: () => void
}

export function useFetch<T>(
  fetchFn: () => Promise<T>,
  deps: unknown[] = [],
): UseFetchResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const fetchRef = useRef(fetchFn)
  fetchRef.current = fetchFn

  const doFetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchRef.current()
      setData(result)
    } catch (e) {
      setError(String(e))
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    doFetch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { data, loading, error, refetch: doFetch }
}
