#  Orchestration Engine - Project Routes
#
#  CRUD for orchestration projects + plan/execute triggers.
#  All endpoints enforce ownership: users see/modify only their own projects.
#  Admins can access all projects.
#
#  Depends on: container.py, models/schemas.py, services/planner.py, services/decomposer.py, middleware/auth.py
#  Used by:    app.py

import json
import time
import uuid

from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.container import Container
from backend.db.connection import Database
from backend.exceptions import (
    BudgetExhaustedError,
    CycleDetectedError,
    InvalidStateError,
    NotFoundError,
    OrchestrationError,
    PlanParseError,
)
from backend.rate_limit import limiter
from backend.middleware.auth import get_current_user
from backend.models.enums import PlanStatus, ProjectStatus, TaskStatus
from backend.models.schemas import PlanOut, ProjectCreate, ProjectOut, ProjectUpdate
from backend.services.budget import BudgetManager

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _row_to_project(
    row, db: Database,
    include_task_summary: bool = False,
    preloaded_summary: dict | None = None,
) -> dict:
    """Convert a DB row to a ProjectOut-compatible dict.

    For batch use, pass preloaded_summary to avoid per-project DB queries.
    """
    data = {
        "id": row["id"],
        "name": row["name"],
        "requirements": row["requirements"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "config": json.loads(row["config_json"]) if row["config_json"] else {},
    }

    if include_task_summary:
        if preloaded_summary is not None:
            data["task_summary"] = preloaded_summary
        else:
            tasks = await db.fetchall(
                "SELECT status, COUNT(*) as cnt FROM tasks WHERE project_id = ? GROUP BY status",
                (row["id"],),
            )
            summary = {"total": 0, "completed": 0, "running": 0, "failed": 0}
            for t in tasks:
                summary["total"] += t["cnt"]
                if t["status"] in summary:
                    summary[t["status"]] = t["cnt"]
            data["task_summary"] = summary

    return data


async def _get_owned_project(db: Database, project_id: str, user: dict):
    """Fetch a project and verify ownership. Raises 404/403."""
    row = await db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not row:
        raise HTTPException(404, f"Project {project_id} not found")
    # Admins can access all; NULL owner_id treated as admin-owned (legacy data)
    if user.get("role") != "admin" and row["owner_id"] is not None and row["owner_id"] != user["id"]:
        raise HTTPException(403, "You do not own this project")
    return row


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
@inject
async def create_project(
    body: ProjectCreate,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
) -> ProjectOut:
    project_id = uuid.uuid4().hex[:12]
    now = time.time()

    await db.execute_write(
        "INSERT INTO projects (id, name, requirements, status, config_json, owner_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, body.name, body.requirements, ProjectStatus.DRAFT, json.dumps(body.config), current_user["id"], now, now),
    )

    row = await db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    return ProjectOut(**await _row_to_project(row, db))


@router.get("")
@inject
async def list_projects(
    status: ProjectStatus | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
) -> list[ProjectOut]:
    if current_user.get("role") == "admin":
        query = "SELECT * FROM projects WHERE 1=1"
        params: list = []
    else:
        query = "SELECT * FROM projects WHERE owner_id = ?"
        params = [current_user["id"]]

    if status:
        query += " AND status = ?"
        params.append(status.value)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = await db.fetchall(query, params)
    if not rows:
        return []

    # Batch-load task summaries in a single query (avoids N+1)
    project_ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(project_ids))
    summary_rows = await db.fetchall(
        f"SELECT project_id, status, COUNT(*) as cnt FROM tasks "
        f"WHERE project_id IN ({placeholders}) GROUP BY project_id, status",
        project_ids,
    )
    summaries: dict[str, dict] = {}
    for sr in summary_rows:
        pid = sr["project_id"]
        if pid not in summaries:
            summaries[pid] = {"total": 0, "completed": 0, "running": 0, "failed": 0}
        summaries[pid]["total"] += sr["cnt"]
        if sr["status"] in summaries[pid]:
            summaries[pid][sr["status"]] = sr["cnt"]

    return [
        ProjectOut(**await _row_to_project(
            r, db, include_task_summary=True,
            preloaded_summary=summaries.get(r["id"], {"total": 0, "completed": 0, "running": 0, "failed": 0}),
        ))
        for r in rows
    ]


@router.get("/{project_id}")
@inject
async def get_project(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
) -> ProjectOut:
    row = await _get_owned_project(db, project_id, current_user)
    return ProjectOut(**await _row_to_project(row, db, include_task_summary=True))


@router.patch("/{project_id}")
@inject
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
) -> ProjectOut:
    await _get_owned_project(db, project_id, current_user)

    updates = []
    params = []
    if body.name is not None:
        updates.append("name = ?")
        params.append(body.name)
    if body.requirements is not None:
        updates.append("requirements = ?")
        params.append(body.requirements)
    if body.config is not None:
        updates.append("config_json = ?")
        params.append(json.dumps(body.config))

    if not updates:
        raise HTTPException(400, "No fields to update")

    updates.append("updated_at = ?")
    params.append(time.time())
    params.append(project_id)

    await db.execute_write(
        f"UPDATE projects SET {', '.join(updates)} WHERE id = ?",
        params,
    )

    row = await db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    return ProjectOut(**await _row_to_project(row, db, include_task_summary=True))


@router.delete("/{project_id}", status_code=204)
@inject
async def delete_project(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
):
    await _get_owned_project(db, project_id, current_user)
    # Cascade deletes handle plans, tasks, deps, events
    await db.execute_write("DELETE FROM projects WHERE id = ?", (project_id,))


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

@router.post("/{project_id}/plan")
@limiter.limit("5/minute")
@inject
async def trigger_plan(
    request: Request,
    project_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
    budget: BudgetManager = Depends(Provide[Container.budget]),
):
    """Generate a plan from the project's requirements using Claude."""
    await _get_owned_project(db, project_id, current_user)

    from backend.services.planner import generate_plan

    try:
        result = await generate_plan(project_id, db=db, budget=budget)
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except BudgetExhaustedError as e:
        raise HTTPException(402, str(e))
    except PlanParseError as e:
        raise HTTPException(422, str(e))
    except OrchestrationError as e:
        raise HTTPException(400, str(e))

    return result


@router.get("/{project_id}/plans")
@inject
async def list_plans(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
) -> list[PlanOut]:
    """List plan versions for a project."""
    await _get_owned_project(db, project_id, current_user)

    rows = await db.fetchall(
        "SELECT * FROM plans WHERE project_id = ? ORDER BY version DESC",
        (project_id,),
    )
    return [
        PlanOut(
            id=r["id"],
            project_id=r["project_id"],
            version=r["version"],
            model_used=r["model_used"],
            prompt_tokens=r["prompt_tokens"],
            completion_tokens=r["completion_tokens"],
            cost_usd=r["cost_usd"],
            plan=json.loads(r["plan_json"]),
            status=r["status"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/{project_id}/plans/{plan_id}/approve")
@inject
async def approve_plan(
    project_id: str,
    plan_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
):
    """Approve a plan and decompose it into executable tasks."""
    await _get_owned_project(db, project_id, current_user)

    row = await db.fetchone("SELECT * FROM plans WHERE id = ? AND project_id = ?", (plan_id, project_id))
    if not row:
        raise HTTPException(404, f"Plan {plan_id} not found")
    if row["status"] != PlanStatus.DRAFT:
        raise HTTPException(400, f"Plan is already {row['status']}")

    from backend.services.decomposer import decompose_plan

    try:
        result = await decompose_plan(project_id, plan_id, db=db)
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except CycleDetectedError as e:
        raise HTTPException(422, str(e))
    except OrchestrationError as e:
        raise HTTPException(400, str(e))

    return result


@router.post("/{project_id}/execute")
@inject
async def start_execution(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
):
    """Start executing approved tasks for a project."""
    row = await _get_owned_project(db, project_id, current_user)
    if row["status"] not in (ProjectStatus.READY, ProjectStatus.PAUSED):
        raise HTTPException(400, f"Project must be in 'ready' or 'paused' state, got '{row['status']}'")

    await db.execute_write(
        "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
        (ProjectStatus.EXECUTING, time.time(), project_id),
    )

    # Executor will pick up tasks on its next tick
    return {"status": "executing", "project_id": project_id}


@router.post("/{project_id}/pause")
@inject
async def pause_execution(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
):
    """Pause execution — no new tasks will start."""
    row = await _get_owned_project(db, project_id, current_user)
    if row["status"] != ProjectStatus.EXECUTING:
        raise HTTPException(400, "Project is not executing")

    await db.execute_write(
        "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
        (ProjectStatus.PAUSED, time.time(), project_id),
    )
    return {"status": "paused", "project_id": project_id}


@router.post("/{project_id}/cancel")
@inject
async def cancel_project(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
):
    """Cancel project — cancel all pending tasks."""
    await _get_owned_project(db, project_id, current_user)

    now = time.time()
    await db.execute_write(
        "UPDATE tasks SET status = ?, updated_at = ? "
        "WHERE project_id = ? AND status IN (?, ?, ?)",
        (TaskStatus.CANCELLED, now, project_id,
         TaskStatus.PENDING, TaskStatus.BLOCKED, TaskStatus.QUEUED),
    )
    await db.execute_write(
        "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
        (ProjectStatus.CANCELLED, now, project_id),
    )
    return {"status": "cancelled", "project_id": project_id}
