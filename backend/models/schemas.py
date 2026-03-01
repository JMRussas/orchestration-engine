#  Orchestration Engine - Pydantic Schemas
#
#  Request/response models for the REST API.
#
#  Depends on: models/enums.py
#  Used by:    routes/*

from pydantic import BaseModel, EmailStr, Field

from backend.models.enums import (
    ModelTier,
    PlanningRigor,
    PlanStatus,
    ProjectStatus,
    ResourceStatus,
    TaskStatus,
    TaskType,
)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    requirements: str = Field(..., min_length=1, max_length=50_000)
    config: dict = Field(default_factory=dict)
    planning_rigor: PlanningRigor = PlanningRigor.L2
    repo_path: str | None = None
    git_base_branch: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    requirements: str | None = Field(default=None, min_length=1, max_length=50_000)
    config: dict | None = None
    planning_rigor: PlanningRigor | None = None
    repo_path: str | None = None
    git_base_branch: str | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    requirements: str
    status: ProjectStatus
    created_at: float
    updated_at: float
    completed_at: float | None = None
    config: dict = Field(default_factory=dict)
    planning_rigor: str = "L2"
    task_summary: dict | None = None  # {total, completed, running, failed}
    repo_path: str | None = None
    git_base_branch: str | None = None
    git_project_branch: str | None = None
    git_state: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

class PlanOut(BaseModel):
    id: str
    project_id: str
    version: int
    model_used: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    plan: dict  # The structured plan JSON
    status: PlanStatus
    created_at: float


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

class TaskOut(BaseModel):
    id: str
    project_id: str
    plan_id: str
    title: str
    description: str
    task_type: TaskType
    priority: int
    status: TaskStatus
    model_tier: ModelTier
    model_used: str | None = None
    wave: int = 0
    phase: str | None = None
    tools: list[str] = Field(default_factory=list)
    verification_status: str | None = None
    verification_notes: str | None = None
    requirement_ids: list[str] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    output_text: str | None = None
    output_artifacts: list[dict] = Field(default_factory=list)
    error: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    started_at: float | None = None
    completed_at: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    git_branch: str | None = None
    git_commit_sha: str | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=50_000)
    model_tier: ModelTier | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    max_tokens: int | None = Field(default=None, ge=1, le=16384)


class ReviewAction(BaseModel):
    """User response to a NEEDS_REVIEW task."""
    action: str = Field(..., pattern="^(approve|retry)$")
    feedback: str = Field(default="", max_length=10_000)


class BulkTaskAction(BaseModel):
    """Perform an action on multiple tasks at once."""
    action: str = Field(..., pattern="^(retry|cancel)$")
    task_ids: list[str] = Field(..., min_length=1, max_length=100)


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

class CheckpointOut(BaseModel):
    id: str
    project_id: str
    task_id: str | None = None
    checkpoint_type: str
    summary: str
    attempts: list[dict] = Field(default_factory=list)
    question: str
    response: str | None = None
    resolved_at: float | None = None
    created_at: float


class CheckpointResolve(BaseModel):
    """User response to a checkpoint."""
    action: str = Field(..., pattern="^(retry|skip|fail)$")
    guidance: str = Field(default="", max_length=10_000)


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

class UsageSummary(BaseModel):
    total_cost_usd: float
    total_prompt_tokens: int
    total_completion_tokens: int
    api_call_count: int
    by_model: dict = Field(default_factory=dict)
    by_provider: dict = Field(default_factory=dict)


class BudgetStatus(BaseModel):
    daily_spent_usd: float
    daily_limit_usd: float
    daily_pct: float
    monthly_spent_usd: float
    monthly_limit_usd: float
    monthly_pct: float


# ---------------------------------------------------------------------------
# Services / Resources
# ---------------------------------------------------------------------------

class ResourceOut(BaseModel):
    id: str
    name: str
    status: ResourceStatus
    method: str = ""
    details: dict = Field(default_factory=dict)
    category: str = "ai"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    display_name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    has_password: bool = True
    linked_providers: list[str] = Field(default_factory=list)


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# OIDC
# ---------------------------------------------------------------------------

class OIDCCallbackRequest(BaseModel):
    """OIDC authorization code callback."""
    code: str = Field(..., min_length=1)
    state: str = Field(..., min_length=1)
    state_token: str = Field(..., min_length=1)
    redirect_uri: str = Field(..., min_length=1)


class OIDCLinkRequest(BaseModel):
    """Link an OIDC provider to an existing account."""
    code: str = Field(..., min_length=1)
    state: str = Field(..., min_length=1)
    state_token: str = Field(..., min_length=1)
    redirect_uri: str = Field(..., min_length=1)


class OIDCProviderInfo(BaseModel):
    """Public info about a configured OIDC provider."""
    name: str
    display_name: str


class OIDCIdentityOut(BaseModel):
    """A linked OIDC identity for a user."""
    provider: str
    provider_email: str | None = None
    created_at: float


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

class AdminUserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    is_active: bool
    created_at: float
    last_login_at: float | None = None
    project_count: int = 0


class AdminUserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    is_active: bool | None = None


class AdminStats(BaseModel):
    total_users: int
    active_users: int
    total_projects: int
    projects_by_status: dict[str, int]
    total_tasks: int
    tasks_by_status: dict[str, int]
    total_spend_usd: float
    spend_by_model: dict[str, float]
    task_completion_rate: float


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

class CostByProject(BaseModel):
    project_id: str
    project_name: str
    cost_usd: float
    task_count: int


class CostByModelTier(BaseModel):
    model_tier: str
    cost_usd: float
    task_count: int
    avg_cost_per_task: float


class DailyCostTrend(BaseModel):
    date: str
    cost_usd: float
    api_calls: int


class AnalyticsCostBreakdown(BaseModel):
    by_project: list[CostByProject]
    by_model_tier: list[CostByModelTier]
    daily_trend: list[DailyCostTrend]
    total_cost_usd: float


class TaskOutcomeByTier(BaseModel):
    model_tier: str
    total: int
    completed: int
    failed: int
    needs_review: int
    success_rate: float


class VerificationByTier(BaseModel):
    model_tier: str
    total_verified: int
    passed: int
    gaps_found: int
    human_needed: int
    pass_rate: float


class AnalyticsTaskOutcomes(BaseModel):
    by_tier: list[TaskOutcomeByTier]
    verification_by_tier: list[VerificationByTier]


class RetryByTier(BaseModel):
    model_tier: str
    total_tasks: int
    tasks_with_retries: int
    total_retries: int
    retry_rate: float


class WaveThroughput(BaseModel):
    project_id: str
    project_name: str
    wave: int
    task_count: int
    avg_duration_seconds: float | None = None


class CostEfficiencyItem(BaseModel):
    model_tier: str
    cost_usd: float
    tasks_completed: int
    verification_pass_count: int
    cost_per_pass: float | None = None


class AnalyticsEfficiency(BaseModel):
    retries_by_tier: list[RetryByTier]
    checkpoint_count: int
    unresolved_checkpoint_count: int
    wave_throughput: list[WaveThroughput]
    cost_efficiency: list[CostEfficiencyItem]


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------

class RAGDatabaseInfo(BaseModel):
    name: str
    path: str
    exists: bool
    file_size_bytes: int = 0
    chunk_count: int = 0
    source_count: int = 0
    index_status: str = "unknown"
    sources: list[dict] = Field(default_factory=list)


class RAGChunkPreview(BaseModel):
    id: str
    source: str
    type_name: str | None = None
    file_path: str | None = None
    text_preview: str
