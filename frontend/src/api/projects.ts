// Orchestration Engine - Projects API

import { apiFetch, apiPost, apiPatch, apiDelete } from './client'
import type { Project, Plan, Task, Checkpoint, CoverageReport } from '../types'

export const listProjects = (status?: string) =>
  apiFetch<Project[]>(`/projects${status ? `?status=${status}` : ''}`)

export const getProject = (id: string) =>
  apiFetch<Project>(`/projects/${id}`)

export const createProject = (name: string, requirements: string) =>
  apiPost<Project>('/projects', { name, requirements })

export const deleteProject = (id: string) =>
  apiDelete(`/projects/${id}`)

export const generatePlan = (projectId: string) =>
  apiPost<{ plan_id: string; plan: unknown; cost_usd: number }>(`/projects/${projectId}/plan`)

export const listPlans = (projectId: string) =>
  apiFetch<Plan[]>(`/projects/${projectId}/plans`)

export const approvePlan = (projectId: string, planId: string) =>
  apiPost<{ tasks_created: number; estimated_cost_usd: number }>(`/projects/${projectId}/plans/${planId}/approve`)

export const startExecution = (projectId: string) =>
  apiPost<{ status: string }>(`/projects/${projectId}/execute`)

export const pauseExecution = (projectId: string) =>
  apiPost<{ status: string }>(`/projects/${projectId}/pause`)

export const cancelProject = (projectId: string) =>
  apiPost<{ status: string }>(`/projects/${projectId}/cancel`)

export const listTasks = (projectId: string) =>
  apiFetch<Task[]>(`/tasks/project/${projectId}`)

export const fetchCoverage = (projectId: string) =>
  apiFetch<CoverageReport>(`/projects/${projectId}/coverage`)

export const fetchCheckpoints = (projectId: string, resolved = false) =>
  apiFetch<Checkpoint[]>(`/checkpoints/project/${projectId}${resolved ? '?resolved=true' : ''}`)

export const resolveCheckpoint = (checkpointId: string, action: string, guidance = '') =>
  apiPost<Checkpoint>(`/checkpoints/${checkpointId}/resolve`, { action, guidance })

export const reviewTask = (taskId: string, action: string, feedback = '') =>
  apiPost<Task>(`/tasks/${taskId}/review`, { action, feedback })
