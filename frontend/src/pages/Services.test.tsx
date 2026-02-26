import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../api/services', () => ({
  listServices: vi.fn(),
}))

import { listServices } from '../api/services'
import Services from './Services'

const mockListServices = vi.mocked(listServices)

beforeEach(() => {
  vi.resetAllMocks()
})

describe('Services', () => {
  it('shows services after load', async () => {
    mockListServices.mockResolvedValueOnce([
      { id: 's1', name: 'Ollama (local)', status: 'online', category: 'LLM', method: 'HTTP', details: {} },
      { id: 's2', name: 'Claude API', status: 'offline', category: 'LLM', method: 'HTTP', details: {} },
    ])

    render(<Services />)
    expect(await screen.findByText('Ollama (local)')).toBeInTheDocument()
    expect(screen.getByText('Claude API')).toBeInTheDocument()
  })

  it('shows error on failure', async () => {
    mockListServices.mockRejectedValueOnce(new Error('Network error'))

    render(<Services />)
    expect(await screen.findByText(/Failed to load services: .*Network error/)).toBeInTheDocument()
  })

  it('refresh button calls API again', async () => {
    mockListServices.mockResolvedValue([])
    render(<Services />)

    await waitFor(() => expect(mockListServices).toHaveBeenCalledTimes(1))

    const user = userEvent.setup()
    await user.click(screen.getByText('Refresh'))

    await waitFor(() => expect(mockListServices).toHaveBeenCalledTimes(2))
  })

  it('refresh button shows Checking while loading', async () => {
    let resolve: (v: unknown[]) => void
    mockListServices.mockImplementation(() => new Promise(r => { resolve = r as (v: unknown[]) => void }))

    render(<Services />)
    // While loading, button shows "Checking..."
    expect(screen.getByText('Checking...')).toBeInTheDocument()

    resolve!([])
    await waitFor(() => expect(screen.getByText('Refresh')).toBeInTheDocument())
  })

  it('shows service status badge', async () => {
    mockListServices.mockResolvedValueOnce([
      { id: 's1', name: 'Ollama', status: 'online', category: 'LLM', details: {} },
    ])

    render(<Services />)
    const badge = await screen.findByText('online')
    expect(badge).toHaveClass('online')
  })

  it('shows details pre block when non-empty', async () => {
    mockListServices.mockResolvedValueOnce([
      { id: 's1', name: 'Ollama', status: 'online', category: 'LLM', details: { version: '1.0' } },
    ])

    render(<Services />)
    await waitFor(() => {
      expect(screen.getByText(/"version": "1.0"/)).toBeInTheDocument()
    })
  })
})
