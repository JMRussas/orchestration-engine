// Orchestration Engine - TypeScript Types
//
// Mirrors backend Pydantic schemas and enums.
//
// Depends on: (none)
// Used by:    all pages, api/*

// ---------------------------------------------------------------------------
// Enums (match backend/models/enums.py)
// ---------------------------------------------------------------------------

export type ProjectStatus = 'draft' | 'planning' | 'ready' | 'executing' | 'paused' | 'completed' | 'failed' | 'cancelled'
export type PlanStatus = 'draft' | 'approved' | 'superseded'
export type TaskStatus = 'pending' | 'blocked' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type ModelTier = 'haiku' | 'sonnet' | 'opus' | 'ollama'
export type TaskType = 'code' | 'research' | 'analysis' | 'asset' | 'integration' | 'documentation'
export type ResourceStatus = 'online' | 'offline' | 'degraded'
export type SSEEventType = 'task_start' | 'task_complete' | 'task_failed' | 'tool_call' | 'budget_warning' | 'project_complete' | 'project_failed'

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

export interface Project {
  id: string
  name: string
  requirements: string
  status: ProjectStatus
  created_at: number
  updated_at: number
  completed_at: number | null
  config: Record<string, unknown>
  task_summary: TaskSummary | null
}

export interface TaskSummary {
  total: number
  completed: number
  running: number
  failed: number
}

export interface Plan {
  id: string
  project_id: string
  version: number
  model_used: string
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  plan: PlanData
  status: PlanStatus
  created_at: number
}

export interface PlanData {
  summary: string
  tasks: PlanTask[]
}

export interface PlanTask {
  title: string
  description: string
  task_type: TaskType
  complexity: string
  depends_on: (number | string)[]
  tools_needed: string[]
}

export interface Task {
  id: string
  project_id: string
  plan_id: string
  title: string
  description: string
  task_type: TaskType
  priority: number
  status: TaskStatus
  model_tier: ModelTier
  model_used: string | null
  tools: string[]
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  output_text: string | null
  output_artifacts: Record<string, unknown>[]
  error: string | null
  depends_on: string[]
  started_at: number | null
  completed_at: number | null
  created_at: number
  updated_at: number
}

export interface UsageSummary {
  total_cost_usd: number
  total_prompt_tokens: number
  total_completion_tokens: number
  api_call_count: number
  by_model: Record<string, ModelUsage>
  by_provider: Record<string, ProviderUsage>
}

export interface ModelUsage {
  cost_usd: number
  prompt_tokens: number
  completion_tokens: number
  calls: number
}

export interface ProviderUsage {
  cost_usd: number
  calls: number
}

export interface BudgetStatus {
  daily_spent_usd: number
  daily_limit_usd: number
  daily_pct: number
  monthly_spent_usd: number
  monthly_limit_usd: number
  monthly_pct: number
}

export interface Resource {
  id: string
  name: string
  status: ResourceStatus
  method: string
  details: Record<string, unknown>
  category: string
}

export interface SSEEvent {
  type: SSEEventType
  message: string
  project_id: string
  task_id: string | null
  timestamp: number
  [key: string]: unknown
}
