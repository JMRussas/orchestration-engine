#  Orchestration Engine - Context Forwarding Tests
#
#  Tests for injecting completed task outputs into dependent tasks' context.
#
#  Depends on: backend/services/executor.py, backend/db/connection.py
#  Used by:    pytest

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.models.enums import ProjectStatus, TaskStatus
from backend.services.decomposer import decompose_plan
from backend.services.executor import Executor


@pytest.fixture
async def executor_with_db(tmp_db):
    """Create an Executor wired to tmp_db with mocked external services."""
    mock_budget = AsyncMock()
    mock_budget.can_spend = AsyncMock(return_value=True)
    mock_budget.reserve_spend = AsyncMock(return_value=True)
    mock_budget.can_spend_project = AsyncMock(return_value=True)
    mock_budget.release_reservation = AsyncMock()
    mock_budget.record_spend = AsyncMock()

    mock_progress = AsyncMock()
    mock_progress.push_event = AsyncMock()

    mock_rm = MagicMock()
    mock_rm.is_available = MagicMock(return_value=True)

    mock_registry = MagicMock()

    executor = Executor(
        db=tmp_db,
        budget=mock_budget,
        progress=mock_progress,
        resource_monitor=mock_rm,
        tool_registry=mock_registry,
    )

    return executor


class TestForwardContext:
    async def test_output_injected_into_dependent(self, seeded_db, executor_with_db):
        """Completing Task A should inject its output into Task B's context."""
        tmp_db, project_id, plan_id = seeded_db
        # Use executor_with_db fixture by passing our seeded tmp_db
        # (seeded_db uses tmp_db fixture internally)
        executor = executor_with_db

        result = await decompose_plan(project_id, plan_id, db=tmp_db)
        task_ids = result["task_ids"]

        # Simulate Task A completing
        task_a = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_ids[0],))
        await executor._forward_context(task_a, "Task A produced this output")

        # Check Task B's context now has the dependency output
        task_b = await tmp_db.fetchone("SELECT context_json FROM tasks WHERE id = ?", (task_ids[1],))
        ctx = json.loads(task_b["context_json"])

        dep_entries = [e for e in ctx if e.get("type") == "dependency_output"]
        assert len(dep_entries) == 1
        assert dep_entries[0]["source_task_id"] == task_ids[0]
        assert dep_entries[0]["source_task_title"] == "Task A"
        assert dep_entries[0]["content"] == "Task A produced this output"

    async def test_non_dependent_not_affected(self, tmp_db, executor_with_db):
        """Tasks that don't depend on the completed task should be unchanged."""
        now = time.time()
        project_id = "proj_ctx_002"
        plan_id = "plan_ctx_002"

        await tmp_db.execute_write(
            "INSERT INTO projects (id, name, requirements, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'draft', ?, ?)",
            (project_id, "Context Test", "Test forwarding", now, now),
        )

        plan_data = {
            "summary": "Independent tasks",
            "tasks": [
                {"title": "A", "description": "Do A", "task_type": "code",
                 "complexity": "simple", "depends_on": [], "tools_needed": []},
                {"title": "B", "description": "Do B", "task_type": "code",
                 "complexity": "simple", "depends_on": [], "tools_needed": []},
            ],
        }
        await tmp_db.execute_write(
            "INSERT INTO plans (id, project_id, version, model_used, plan_json, status, created_at) "
            "VALUES (?, ?, 1, 'test-model', ?, 'draft', ?)",
            (plan_id, project_id, json.dumps(plan_data), now),
        )

        result = await decompose_plan(project_id, plan_id, db=tmp_db)
        task_ids = result["task_ids"]

        # Get Task B's context before forwarding
        before = await tmp_db.fetchone("SELECT context_json FROM tasks WHERE id = ?", (task_ids[1],))
        ctx_before = json.loads(before["context_json"])

        # Forward from Task A — B is independent, should not be affected
        task_a = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_ids[0],))
        executor = executor_with_db
        await executor._forward_context(task_a, "A's output")

        after = await tmp_db.fetchone("SELECT context_json FROM tasks WHERE id = ?", (task_ids[1],))
        ctx_after = json.loads(after["context_json"])

        assert ctx_before == ctx_after

    async def test_output_truncated_at_max_chars(self, seeded_db, executor_with_db):
        """Long outputs should be truncated to CONTEXT_FORWARD_MAX_CHARS."""
        from unittest.mock import patch

        tmp_db, project_id, plan_id = seeded_db
        result = await decompose_plan(project_id, plan_id, db=tmp_db)
        task_ids = result["task_ids"]

        task_a = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_ids[0],))
        executor = executor_with_db

        # Set a small max for testing
        with patch("backend.services.executor.CONTEXT_FORWARD_MAX_CHARS", 50):
            await executor._forward_context(task_a, "X" * 200)

        task_b = await tmp_db.fetchone("SELECT context_json FROM tasks WHERE id = ?", (task_ids[1],))
        ctx = json.loads(task_b["context_json"])
        dep_entries = [e for e in ctx if e.get("type") == "dependency_output"]
        assert len(dep_entries[0]["content"]) == 50

    async def test_empty_output_forwards_empty_content(self, seeded_db, executor_with_db):
        """Empty output should still create a context entry with empty content."""
        tmp_db, project_id, plan_id = seeded_db
        result = await decompose_plan(project_id, plan_id, db=tmp_db)
        task_ids = result["task_ids"]

        task_a = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_ids[0],))
        executor = executor_with_db
        await executor._forward_context(task_a, "")

        task_b = await tmp_db.fetchone("SELECT context_json FROM tasks WHERE id = ?", (task_ids[1],))
        ctx = json.loads(task_b["context_json"])
        dep_entries = [e for e in ctx if e.get("type") == "dependency_output"]
        assert len(dep_entries) == 1
        assert dep_entries[0]["content"] == ""

    async def test_multiple_deps_forward_all(self, tmp_db, executor_with_db):
        """A task with two dependencies gets context from both when they complete."""
        now = time.time()
        project_id = "proj_ctx_003"
        plan_id = "plan_ctx_003"

        await tmp_db.execute_write(
            "INSERT INTO projects (id, name, requirements, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'draft', ?, ?)",
            (project_id, "Multi-dep Test", "Test multi forwarding", now, now),
        )

        plan_data = {
            "summary": "Diamond pattern",
            "tasks": [
                {"title": "A", "description": "Do A", "task_type": "code",
                 "complexity": "simple", "depends_on": [], "tools_needed": []},
                {"title": "B", "description": "Do B", "task_type": "code",
                 "complexity": "simple", "depends_on": [], "tools_needed": []},
                {"title": "C", "description": "Do C (depends on A and B)", "task_type": "code",
                 "complexity": "simple", "depends_on": [0, 1], "tools_needed": []},
            ],
        }
        await tmp_db.execute_write(
            "INSERT INTO plans (id, project_id, version, model_used, plan_json, status, created_at) "
            "VALUES (?, ?, 1, 'test-model', ?, 'draft', ?)",
            (plan_id, project_id, json.dumps(plan_data), now),
        )

        result = await decompose_plan(project_id, plan_id, db=tmp_db)
        task_ids = result["task_ids"]

        executor = executor_with_db

        # Forward from A
        task_a = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_ids[0],))
        await executor._forward_context(task_a, "Output from A")

        # Forward from B
        task_b = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_ids[1],))
        await executor._forward_context(task_b, "Output from B")

        # Check C has both
        task_c = await tmp_db.fetchone("SELECT context_json FROM tasks WHERE id = ?", (task_ids[2],))
        ctx = json.loads(task_c["context_json"])
        dep_entries = [e for e in ctx if e.get("type") == "dependency_output"]
        assert len(dep_entries) == 2

        titles = {e["source_task_title"] for e in dep_entries}
        assert titles == {"A", "B"}

    async def test_none_output_handled(self, seeded_db, executor_with_db):
        """None output should be treated as empty string."""
        tmp_db, project_id, plan_id = seeded_db
        result = await decompose_plan(project_id, plan_id, db=tmp_db)
        task_ids = result["task_ids"]

        task_a = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_ids[0],))
        executor = executor_with_db
        await executor._forward_context(task_a, None)

        task_b = await tmp_db.fetchone("SELECT context_json FROM tasks WHERE id = ?", (task_ids[1],))
        ctx = json.loads(task_b["context_json"])
        dep_entries = [e for e in ctx if e.get("type") == "dependency_output"]
        assert len(dep_entries) == 1
        assert dep_entries[0]["content"] == ""
