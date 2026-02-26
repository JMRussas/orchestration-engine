#  Orchestration Engine - Task Routes
#
#  Task management: list, detail, update, retry, cancel.
#  All endpoints enforce ownership via the parent project.
#
#  Depends on: container.py, models/schemas.py, middleware/auth.py
#  Used by:    app.py

import json
import time

from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.config import MAX_TASK_RETRIES
from backend.container import Container
from backend.db.connection import Database
from backend.middleware.auth import get_current_user
from backend.models.enums import TaskStatus
from backend.models.schemas import ReviewAction, TaskOut, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _verify_task_ownership(db: Database, task_id: str, user: dict):
    """Fetch a task and verify the user owns its parent project. Returns the task row."""
    row = await db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(404, f"Task {task_id} not found")
    project = await db.fetchone("SELECT owner_id FROM projects WHERE id = ?", (row["project_id"],))
    if project and user.get("role") != "admin" and project["owner_id"] is not None and project["owner_id"] != user["id"]:
        raise HTTPException(403, "You do not own this task's project")
    return row


async def _row_to_dict(row, db: Database, deps_list: list[str] | None = None) -> dict:
    """Convert a DB row to a TaskOut-compatible dict.

    If deps_list is None, fetches dependencies from the DB (single-task endpoints).
    For batch use, pass pre-loaded deps_list to avoid N+1 queries.
    """
    if deps_list is None:
        deps = await db.fetchall(
            "SELECT depends_on FROM task_deps WHERE task_id = ?", (row["id"],)
        )
        deps_list = [d["depends_on"] for d in deps]

    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "plan_id": row["plan_id"],
        "title": row["title"],
        "description": row["description"],
        "task_type": row["task_type"],
        "priority": row["priority"],
        "status": row["status"],
        "model_tier": row["model_tier"],
        "model_used": row["model_used"],
        "tools": json.loads(row["tools_json"]) if row["tools_json"] else [],
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "cost_usd": row["cost_usd"],
        "output_text": row["output_text"],
        "output_artifacts": json.loads(row["output_artifacts_json"]) if row["output_artifacts_json"] else [],
        "wave": row["wave"],
        "verification_status": row["verification_status"],
        "verification_notes": row["verification_notes"],
        "requirement_ids": json.loads(row["requirement_ids_json"]) if row["requirement_ids_json"] else [],
        "error": row["error"],
        "depends_on": deps_list,
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def _rows_to_tasks(rows, db: Database) -> list[dict]:
    """Batch-convert rows to TaskOut dicts with a single dep query (avoids N+1)."""
    if not rows:
        return []

    task_ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(task_ids))
    dep_rows = await db.fetchall(
        f"SELECT task_id, depends_on FROM task_deps WHERE task_id IN ({placeholders})",
        task_ids,
    )

    # Group deps by task_id
    deps_map: dict[str, list[str]] = {tid: [] for tid in task_ids}
    for d in dep_rows:
        deps_map[d["task_id"]].append(d["depends_on"])

    return [await _row_to_dict(r, db, deps_map.get(r["id"], [])) for r in rows]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/project/{project_id}")
@inject
async def list_tasks(
    project_id: str,
    status: TaskStatus | None = None,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
) -> list[TaskOut]:
    """List all tasks for a project."""
    # Verify project ownership
    from backend.routes.projects import _get_owned_project
    await _get_owned_project(db, project_id, current_user)

    query = "SELECT * FROM tasks WHERE project_id = ?"
    params: list = [project_id]
    if status:
        query += " AND status = ?"
        params.append(status.value)
    query += " ORDER BY priority ASC, created_at ASC"

    rows = await db.fetchall(query, params)
    return [TaskOut(**d) for d in await _rows_to_tasks(rows, db)]


@router.get("/{task_id}")
@inject
async def get_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
) -> TaskOut:
    """Get task detail including output and cost."""
    row = await _verify_task_ownership(db, task_id, current_user)
    return TaskOut(**await _row_to_dict(row, db))


@router.patch("/{task_id}")
@inject
async def update_task(
    task_id: str,
    body: TaskUpdate,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
) -> TaskOut:
    """Edit task before execution (description, model tier, priority)."""
    row = await _verify_task_ownership(db, task_id, current_user)

    if row["status"] in (TaskStatus.RUNNING, TaskStatus.COMPLETED):
        raise HTTPException(400, "Cannot edit a running or completed task")

    updates = []
    params = []
    if body.title is not None:
        updates.append("title = ?")
        params.append(body.title)
    if body.description is not None:
        updates.append("description = ?")
        params.append(body.description)
    if body.model_tier is not None:
        updates.append("model_tier = ?")
        params.append(body.model_tier.value)
    if body.priority is not None:
        updates.append("priority = ?")
        params.append(body.priority)
    if body.max_tokens is not None:
        updates.append("max_tokens = ?")
        params.append(body.max_tokens)

    if not updates:
        raise HTTPException(400, "No fields to update")

    updates.append("updated_at = ?")
    params.append(time.time())
    params.append(task_id)

    await db.execute_write(
        f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
        params,
    )

    row = await db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return TaskOut(**await _row_to_dict(row, db))


@router.post("/{task_id}/retry")
@inject
async def retry_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
) -> TaskOut:
    """Retry a failed task."""
    row = await _verify_task_ownership(db, task_id, current_user)
    if row["status"] != TaskStatus.FAILED:
        raise HTTPException(400, "Can only retry failed tasks")
    if row["retry_count"] >= MAX_TASK_RETRIES:
        raise HTTPException(400, f"Maximum retry limit reached ({MAX_TASK_RETRIES})")

    await db.execute_write(
        "UPDATE tasks SET status = ?, error = NULL, output_text = NULL, "
        "retry_count = retry_count + 1, updated_at = ? WHERE id = ?",
        (TaskStatus.PENDING, time.time(), task_id),
    )

    row = await db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return TaskOut(**await _row_to_dict(row, db))


@router.post("/{task_id}/cancel")
@inject
async def cancel_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
) -> TaskOut:
    """Cancel a pending or queued task."""
    row = await _verify_task_ownership(db, task_id, current_user)
    if row["status"] not in (TaskStatus.PENDING, TaskStatus.BLOCKED, TaskStatus.QUEUED):
        raise HTTPException(400, f"Cannot cancel task in '{row['status']}' state")

    await db.execute_write(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
        (TaskStatus.CANCELLED, time.time(), task_id),
    )

    row = await db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return TaskOut(**await _row_to_dict(row, db))


@router.post("/{task_id}/review")
@inject
async def review_task(
    task_id: str,
    body: ReviewAction,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(Provide[Container.db]),
) -> TaskOut:
    """Respond to a task in NEEDS_REVIEW status.

    Actions:
        approve — accept the output as-is, mark task COMPLETED.
        retry — reset to PENDING with user feedback appended to context.
    """
    row = await _verify_task_ownership(db, task_id, current_user)
    if row["status"] != TaskStatus.NEEDS_REVIEW:
        raise HTTPException(400, "Task is not in needs_review status")

    if body.action == "approve":
        await db.execute_write(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (TaskStatus.COMPLETED, time.time(), task_id),
        )
    elif body.action == "retry":
        ctx = json.loads(row["context_json"]) if row["context_json"] else []
        if body.feedback:
            ctx.append({
                "type": "review_feedback",
                "content": body.feedback,
            })
        await db.execute_write(
            "UPDATE tasks SET status = ?, context_json = ?, "
            "verification_status = NULL, verification_notes = NULL, "
            "output_text = NULL, completed_at = NULL, "
            "retry_count = retry_count + 1, updated_at = ? WHERE id = ?",
            (TaskStatus.PENDING, json.dumps(ctx), time.time(), task_id),
        )

    updated = await db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return TaskOut(**await _row_to_dict(updated, db))
