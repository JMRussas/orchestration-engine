import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { useFetch } from './useFetch'

beforeEach(() => {
  vi.resetAllMocks()
})

describe('useFetch', () => {
  it('returns data on success', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ id: 1, name: 'Test' })

    const { result } = renderHook(() => useFetch(fetchFn))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.data).toEqual({ id: 1, name: 'Test' })
    expect(result.current.error).toBeNull()
    expect(fetchFn).toHaveBeenCalledTimes(1)
  })

  it('returns error on failure', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('Network error'))

    const { result } = renderHook(() => useFetch(fetchFn))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.data).toBeNull()
    expect(result.current.error).toContain('Network error')
  })

  it('refetch re-calls the function', async () => {
    const fetchFn = vi.fn()
      .mockResolvedValueOnce('first')
      .mockResolvedValueOnce('second')

    const { result } = renderHook(() => useFetch(fetchFn))

    await waitFor(() => {
      expect(result.current.data).toBe('first')
    })

    await act(async () => {
      result.current.refetch()
    })

    await waitFor(() => {
      expect(result.current.data).toBe('second')
    })

    expect(fetchFn).toHaveBeenCalledTimes(2)
  })

  it('starts in loading state', () => {
    const fetchFn = vi.fn().mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useFetch(fetchFn))

    expect(result.current.loading).toBe(true)
    expect(result.current.data).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('re-fetches when deps change', async () => {
    const fetchFn = vi.fn().mockResolvedValue('data')
    let dep = 'a'

    const { result, rerender } = renderHook(() => useFetch(fetchFn, [dep]))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    dep = 'b'
    rerender()

    await waitFor(() => {
      expect(fetchFn).toHaveBeenCalledTimes(2)
    })
  })
})
