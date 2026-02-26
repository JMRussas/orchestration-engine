#  Orchestration Engine - Plan Decomposer
#
#  Converts an approved plan JSON into task rows with dependency edges.
#
#  Depends on: backend/config.py, services/model_router.py
#  Used by:    routes/projects.py

import json
import time
import uuid

from backend.config import DEFAULT_MAX_TOKENS
from backend.exceptions import CycleDetectedError, InvalidStateError, NotFoundError
from backend.models.enums import PlanStatus, ProjectStatus, TaskStatus
from backend.services.model_router import estimate_task_cost, recommend_tier, recommend_tools


async def decompose_plan(project_id: str, plan_id: str, *, db) -> dict:
    """Convert an approved plan into executable tasks with dependencies.

    Args:
        project_id: The project ID.
        plan_id: The plan ID to decompose.
        db: Database instance (injected).

    Returns a summary of created tasks and estimated total cost.
    """
    # Load the plan
    plan_row = await db.fetchone("SELECT * FROM plans WHERE id = ?", (plan_id,))
    if not plan_row:
        raise NotFoundError(f"Plan {plan_id} not found")
    if plan_row["project_id"] != project_id:
        raise NotFoundError(f"Plan {plan_id} does not belong to project {project_id}")

    plan_data = json.loads(plan_row["plan_json"])
    tasks_data = plan_data.get("tasks", [])

    if not tasks_data:
        raise InvalidStateError("Plan has no tasks")

    # Validate dependency graph: detect cycles before creating task rows
    _check_for_cycles(tasks_data)

    # Get project requirements for context injection
    project_row = await db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not project_row:
        raise NotFoundError(f"Project {project_id} not found")

    now = time.time()
    task_ids: list[str] = []
    total_estimated_cost = 0.0
    write_statements: list[tuple[str, tuple | list]] = []

    # Create task rows
    for i, task_def in enumerate(tasks_data):
        task_id = uuid.uuid4().hex[:12]
        task_ids.append(task_id)

        task_type = task_def.get("task_type", "code")
        complexity = task_def.get("complexity", "medium")
        title = task_def.get("title", f"Task {i + 1}")
        description = task_def.get("description", "")

        # Determine model tier and tools
        tier = recommend_tier(task_type, complexity)
        tools = task_def.get("tools_needed", recommend_tools(task_type))

        # Build minimal context for this task
        context = [
            {"type": "project_summary", "content": plan_data.get("summary", "")},
            {"type": "task_description", "content": description},
        ]

        # Priority: lower index = higher priority (0 = highest)
        priority = i * 10

        # Estimate cost
        est_input = 1500  # ~system prompt + context + tools
        est_cost = estimate_task_cost(tier, est_input, DEFAULT_MAX_TOKENS)
        total_estimated_cost += est_cost

        write_statements.append((
            "INSERT INTO tasks (id, project_id, plan_id, title, description, task_type, "
            "priority, status, model_tier, context_json, tools_json, "
            "max_tokens, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, project_id, plan_id, title, description, task_type,
             priority, TaskStatus.PENDING, tier.value, json.dumps(context),
             json.dumps(tools), DEFAULT_MAX_TOKENS, now, now),
        ))

    # Create dependency edges
    for i, task_def in enumerate(tasks_data):
        depends_on_indices = task_def.get("depends_on", [])
        for dep_raw in depends_on_indices:
            # Claude may return indices as strings or ints
            dep_idx = int(dep_raw) if isinstance(dep_raw, str) and dep_raw.isdigit() else dep_raw
            if isinstance(dep_idx, int) and 0 <= dep_idx < len(task_ids) and dep_idx != i:
                write_statements.append((
                    "INSERT INTO task_deps (task_id, depends_on) VALUES (?, ?)",
                    (task_ids[i], task_ids[dep_idx]),
                ))

    # Mark plan as approved
    write_statements.append((
        "UPDATE plans SET status = ? WHERE id = ?",
        (PlanStatus.APPROVED, plan_id),
    ))

    # Update project status to ready
    write_statements.append((
        "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
        (ProjectStatus.READY, time.time(), project_id),
    ))

    # Execute all writes in one transaction
    await db.execute_many_write(write_statements)

    # Mark tasks with unmet dependencies as blocked
    await _update_blocked_status(project_id, db=db)

    return {
        "tasks_created": len(task_ids),
        "task_ids": task_ids,
        "estimated_cost_usd": round(total_estimated_cost, 4),
        "summary": plan_data.get("summary", ""),
    }


def _check_for_cycles(tasks_data: list[dict]) -> None:
    """Detect dependency cycles in the task graph before creating rows.

    Uses iterative DFS with three-color marking (white/gray/black).
    Raises CycleDetectedError if a cycle is found.
    """
    n = len(tasks_data)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = [WHITE] * n

    for start in range(n):
        if color[start] != WHITE:
            continue
        stack = [(start, False)]
        while stack:
            node, processed = stack.pop()
            if processed:
                color[node] = BLACK
                continue
            if color[node] == GRAY:
                color[node] = BLACK
                continue
            color[node] = GRAY
            stack.append((node, True))

            deps = tasks_data[node].get("depends_on", [])
            for dep_raw in deps:
                dep_idx = int(dep_raw) if isinstance(dep_raw, str) and dep_raw.isdigit() else dep_raw
                if not isinstance(dep_idx, int) or dep_idx < 0 or dep_idx >= n or dep_idx == node:
                    continue
                if color[dep_idx] == GRAY:
                    raise CycleDetectedError(
                        f"Dependency cycle detected: task {node} "
                        f"('{tasks_data[node].get('title', '')}') and task {dep_idx} "
                        f"('{tasks_data[dep_idx].get('title', '')}') form a cycle"
                    )
                if color[dep_idx] == WHITE:
                    stack.append((dep_idx, False))


async def _update_blocked_status(project_id: str, *, db):
    """Mark pending tasks as blocked if they have incomplete dependencies (single query)."""
    now = time.time()
    await db.execute_write(
        "UPDATE tasks SET status = ?, updated_at = ? "
        "WHERE project_id = ? AND status = ? "
        "AND id IN ("
        "  SELECT d.task_id FROM task_deps d "
        "  JOIN tasks dep ON dep.id = d.depends_on "
        "  WHERE dep.status != ?"
        ")",
        (TaskStatus.BLOCKED, now, project_id, TaskStatus.PENDING, TaskStatus.COMPLETED),
    )
