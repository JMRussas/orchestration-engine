#  Orchestration Engine - Plan Decomposer
#
#  Converts an approved plan JSON into task rows with dependency edges.
#
#  Depends on: backend/config.py, services/model_router.py
#  Used by:    routes/projects.py, container.py

import json
import time
import uuid

from backend.config import DEFAULT_MAX_TOKENS
from backend.exceptions import CycleDetectedError, InvalidStateError, NotFoundError
from backend.models.enums import PlanStatus, ProjectStatus, TaskStatus
from backend.services.model_router import estimate_task_cost, recommend_tier, recommend_tools

# Token estimate for budget estimation during decomposition
_EST_DECOMPOSE_INPUT_TOKENS = 1500  # system prompt + context + tools


def _flatten_plan_tasks(plan_data: dict) -> tuple[list[dict], list[str | None]]:
    """Extract a flat task list from either a flat or phased plan.

    Returns:
        (tasks_data, phase_names) where phase_names[i] is the phase name
        for tasks_data[i]. For flat plans, all phase_names are None.
    """
    phases = plan_data.get("phases")
    if phases and isinstance(phases, list) and len(phases) > 0:
        tasks_data: list[dict] = []
        phase_names: list[str | None] = []
        for phase in phases:
            phase_name = phase.get("name", "Unnamed Phase")
            for task in phase.get("tasks", []):
                tasks_data.append(task)
                phase_names.append(phase_name)
        return tasks_data, phase_names

    # Flat plan (L1 or legacy)
    tasks_data = plan_data.get("tasks", [])
    return tasks_data, [None] * len(tasks_data)


class DecomposerService:
    """Injectable service that converts plans into executable tasks."""

    def __init__(self, *, db):
        self._db = db

    async def decompose(self, project_id: str, plan_id: str) -> dict:
        """Convert an approved plan into executable tasks with dependencies.

        Returns a summary of created tasks and estimated total cost.
        """
        db = self._db

        # Load the plan
        plan_row = await db.fetchone("SELECT * FROM plans WHERE id = ?", (plan_id,))
        if not plan_row:
            raise NotFoundError(f"Plan {plan_id} not found")
        if plan_row["project_id"] != project_id:
            raise NotFoundError(f"Plan {plan_id} does not belong to project {project_id}")

        plan_data = json.loads(plan_row["plan_json"])
        tasks_data, phase_names = _flatten_plan_tasks(plan_data)

        if not tasks_data:
            raise InvalidStateError("Plan has no tasks")

        # Validate dependency graph and compute wave numbers
        _check_for_cycles(tasks_data)
        waves = _compute_waves(tasks_data)

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

            phase = phase_names[i]

            # Build enriched context for this task
            context = [
                {"type": "project_summary", "content": plan_data.get("summary", "")},
                {"type": "project_requirements", "content": project_row["requirements"]},
                {"type": "task_description", "content": description},
            ]

            if phase:
                context.append({"type": "phase", "content": phase})

            # Add sibling task summaries (what else is being worked on)
            siblings = []
            for j, sibling in enumerate(tasks_data):
                if j != i:
                    siblings.append(f"- {sibling.get('title', f'Task {j+1}')}: "
                                    f"{sibling.get('description', '')[:100]}")
            if siblings:
                context.append({"type": "sibling_tasks", "content": "\n".join(siblings)})

            # Forward verification criteria if present
            criteria = task_def.get("verification_criteria", "")
            if criteria:
                context.append({"type": "verification_criteria", "content": criteria})

            # Forward affected files if specified
            files = task_def.get("affected_files", [])
            if files:
                context.append({"type": "affected_files", "content": ", ".join(files)})

            # Requirement traceability
            requirement_ids = task_def.get("requirement_ids", [])

            # Priority: lower index = higher priority (0 = highest)
            priority = i * 10

            # Estimate cost
            est_cost = estimate_task_cost(tier, _EST_DECOMPOSE_INPUT_TOKENS, DEFAULT_MAX_TOKENS)
            total_estimated_cost += est_cost

            write_statements.append((
                "INSERT INTO tasks (id, project_id, plan_id, title, description, task_type, "
                "priority, status, model_tier, context_json, tools_json, "
                "max_tokens, wave, phase, requirement_ids_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, project_id, plan_id, title, description, task_type,
                 priority, TaskStatus.PENDING, tier.value, json.dumps(context),
                 json.dumps(tools), DEFAULT_MAX_TOKENS, waves[i], phase,
                 json.dumps(requirement_ids), now, now),
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
            "total_waves": max(waves) + 1 if waves else 0,
        }


async def decompose_plan(project_id: str, plan_id: str, *, db) -> dict:
    """Convenience wrapper for backward compatibility with tests and direct callers."""
    return await DecomposerService(db=db).decompose(project_id, plan_id)


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


def _compute_waves(tasks_data: list[dict]) -> list[int]:
    """Assign each task a wave number based on dependency depth.

    Wave 0 = tasks with no dependencies.
    Wave N = max(wave of all deps) + 1.

    Requires an acyclic graph (_check_for_cycles must run first).
    Uses Kahn's algorithm (topological BFS) to process in correct order.
    """
    n = len(tasks_data)
    if n == 0:
        return []

    waves = [0] * n

    # Build adjacency list and in-degree counts
    adj: list[list[int]] = [[] for _ in range(n)]
    in_deg = [0] * n
    for i, task_def in enumerate(tasks_data):
        for dep_raw in task_def.get("depends_on", []):
            dep_idx = int(dep_raw) if isinstance(dep_raw, str) and dep_raw.isdigit() else dep_raw
            if isinstance(dep_idx, int) and 0 <= dep_idx < n and dep_idx != i:
                adj[dep_idx].append(i)  # dep_idx → i (i depends on dep_idx)
                in_deg[i] += 1

    # BFS from all roots (in-degree 0)
    from collections import deque
    queue = deque(i for i in range(n) if in_deg[i] == 0)

    while queue:
        node = queue.popleft()
        for child in adj[node]:
            waves[child] = max(waves[child], waves[node] + 1)
            in_deg[child] -= 1
            if in_deg[child] == 0:
                queue.append(child)

    return waves


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
