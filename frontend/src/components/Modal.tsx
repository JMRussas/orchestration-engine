// Orchestration Engine - Modal Component
//
// Portal-based modal with backdrop click and Escape key to close.
//
// Depends on: (none)
// Used by:    pages/TaskDetail.tsx

import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
}

export default function Modal({ open, onClose, title, children }: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleEsc)
    return () => window.removeEventListener('keydown', handleEsc)
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div
      className="modal-overlay"
      ref={overlayRef}
      onClick={e => { if (e.target === overlayRef.current) onClose() }}
    >
      <div className="modal-content">
        <div className="flex-between mb-2">
          <h3 style={{ margin: 0 }}>{title}</h3>
          <button
            className="btn btn-sm"
            onClick={onClose}
            style={{ background: 'transparent', color: 'var(--text-dim)' }}
          >
            &times;
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}
