// Orchestration Engine - PlanTree Dependency Context
//
// Shared state for dependency visualization: node DOM refs,
// dependency graph, and hover tracking.
//
// Depends on: (none)
// Used by:    PlanTree/index.tsx, PlanTree/PlanTreeNode.tsx, PlanTree/DependencyOverlay.tsx

import { createContext, useContext, useCallback, useRef, useState } from 'react'

interface DependencyContextValue {
  nodeRefs: React.MutableRefObject<Map<string, HTMLElement>>
  dependencyMap: Map<string, string[]>
  hoveredNodeId: string | null
  setHoveredNodeId: (id: string | null) => void
}

const DependencyCtx = createContext<DependencyContextValue | null>(null)

export function DependencyProvider({
  dependencyMap,
  children,
}: {
  dependencyMap: Map<string, string[]>
  children: React.ReactNode
}) {
  const nodeRefs = useRef(new Map<string, HTMLElement>())
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null)

  return (
    <DependencyCtx.Provider value={{ nodeRefs, dependencyMap, hoveredNodeId, setHoveredNodeId }}>
      {children}
    </DependencyCtx.Provider>
  )
}

export function useDependencyContext(): DependencyContextValue {
  const ctx = useContext(DependencyCtx)
  if (!ctx) throw new Error('useDependencyContext must be used within DependencyProvider')
  return ctx
}

export function useRegisterNode(id: string) {
  const { nodeRefs } = useDependencyContext()

  return useCallback((el: HTMLElement | null) => {
    if (el) {
      nodeRefs.current.set(id, el)
    } else {
      nodeRefs.current.delete(id)
    }
  }, [id, nodeRefs])
}

export function useNodeDependencies(id: string) {
  const { dependencyMap } = useDependencyContext()

  const upstream = dependencyMap.get(id) ?? []
  const downstream: string[] = []
  dependencyMap.forEach((deps, nodeId) => {
    if (deps.includes(id)) downstream.push(nodeId)
  })

  return { upstream, downstream }
}
