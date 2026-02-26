#  Orchestration Engine - Pydantic Schemas
#
#  Request/response models for the REST API.
#
#  Depends on: models/enums.py
#  Used by:    routes/*

from pydantic import BaseModel, EmailStr, Field

from backend.models.enums import (
    ModelTier,
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


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    requirements: str | None = Field(default=None, min_length=1, max_length=50_000)
    config: dict | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    requirements: str
    status: ProjectStatus
    created_at: float
    updated_at: float
    completed_at: float | None = None
    config: dict = Field(default_factory=dict)
    task_summary: dict | None = None  # {total, completed, running, failed}


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
    tools: list[str] = Field(default_factory=list)
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


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=50_000)
    model_tier: ModelTier | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    max_tokens: int | None = Field(default=None, ge=1, le=16384)


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


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
