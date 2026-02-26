#  Orchestration Engine - Executor Hardening Tests
#
#  Tests for graceful shutdown and stale task recovery.
#
#  Depends on: backend/services/executor.py, backend/db/connection.py
#  Used by:    pytest

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.models.enums import TaskStatus
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


# ---------------------------------------------------------------------------
# Stale Task Recovery
# ---------------------------------------------------------------------------

class TestStaleTaskRecovery:
    async def test_stale_running_task_recovered(self, tmp_db, executor_with_db):
        """Tasks stuck in 'running' longer than threshold are reset to pending."""
        now = time.time()
        stale_time = now - 600  # 10 min ago, well past 5 min threshold

        await tmp_db.execute_write(
            "INSERT INTO projects (id, name, requirements, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'executing', ?, ?)",
            ("proj_stale_001", "Stale Test", "test", now, now),
        )
        await tmp_db.execute_write(
            "INSERT INTO plans (id, project_id, version, model_used, plan_json, status, created_at) "
            "VALUES (?, ?, 1, 'test-model', ?, 'approved', ?)",
            ("plan_stale_001", "proj_stale_001", json.dumps({"summary": "test", "tasks": []}), now),
        )
        await tmp_db.execute_write(
            "INSERT INTO tasks (id, project_id, plan_id, title, description, task_type, "
            "priority, status, model_tier, wave, retry_count, max_retries, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("task_stale_001", "proj_stale_001", "plan_stale_001", "Stale Task",
             "Do something", "code", 0, TaskStatus.RUNNING, "haiku", 0,
             1, 5, stale_time, stale_time),
        )

        await executor_with_db._recover_stale_tasks()

        row = await tmp_db.fetchone("SELECT status, retry_count, error FROM tasks WHERE id = ?",
                                     ("task_stale_001",))
        assert row["status"] == TaskStatus.PENDING
        assert row["retry_count"] == 2  # Was 1, incremented to 2
        assert "Recovered from stale state" in row["error"]

    async def test_stale_queued_task_recovered(self, tmp_db, executor_with_db):
        """Tasks stuck in 'queued' longer than threshold are also recovered."""
        now = time.time()
        stale_time = now - 600

        await tmp_db.execute_write(
            "INSERT INTO projects (id, name, requirements, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'executing', ?, ?)",
            ("proj_stale_002", "Stale Queued", "test", now, now),
        )
        await tmp_db.execute_write(
            "INSERT INTO plans (id, project_id, version, model_used, plan_json, status, created_at) "
            "VALUES (?, ?, 1, 'test-model', ?, 'approved', ?)",
            ("plan_stale_002", "proj_stale_002", json.dumps({"summary": "test", "tasks": []}), now),
        )
        await tmp_db.execute_write(
            "INSERT INTO tasks (id, project_id, plan_id, title, description, task_type, "
            "priority, status, model_tier, wave, retry_count, max_retries, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("task_stale_002", "proj_stale_002", "plan_stale_002", "Queued Task",
             "Do something", "code", 0, TaskStatus.QUEUED, "haiku", 0,
             0, 5, stale_time, stale_time),
        )

        await executor_with_db._recover_stale_tasks()

        row = await tmp_db.fetchone("SELECT status, retry_count FROM tasks WHERE id = ?",
                                     ("task_stale_002",))
        assert row["status"] == TaskStatus.PENDING
        assert row["retry_count"] == 1

    async def test_recent_running_task_not_recovered(self, tmp_db, executor_with_db):
        """Tasks recently updated (within threshold) should NOT be recovered."""
        now = time.time()

        await tmp_db.execute_write(
            "INSERT INTO projects (id, name, requirements, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'executing', ?, ?)",
            ("proj_stale_003", "Recent Test", "test", now, now),
        )
        await tmp_db.execute_write(
            "INSERT INTO plans (id, project_id, version, model_used, plan_json, status, created_at) "
            "VALUES (?, ?, 1, 'test-model', ?, 'approved', ?)",
            ("plan_stale_003", "proj_stale_003", json.dumps({"summary": "test", "tasks": []}), now),
        )
        await tmp_db.execute_write(
            "INSERT INTO tasks (id, project_id, plan_id, title, description, task_type, "
            "priority, status, model_tier, wave, retry_count, max_retries, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("task_stale_003", "proj_stale_003", "plan_stale_003", "Recent Task",
             "Do something", "code", 0, TaskStatus.RUNNING, "haiku", 0,
             0, 5, now, now),  # updated_at = now, within threshold
        )

        await executor_with_db._recover_stale_tasks()

        row = await tmp_db.fetchone("SELECT status, retry_count FROM tasks WHERE id = ?",
                                     ("task_stale_003",))
        assert row["status"] == TaskStatus.RUNNING  # Unchanged
        assert row["retry_count"] == 0

    async def test_no_stale_tasks_is_noop(self, tmp_db, executor_with_db):
        """Recovery with no stale tasks should complete without error."""
        # No tasks exist at all — should just return
        await executor_with_db._recover_stale_tasks()
        # If we got here without exception, test passes


# ---------------------------------------------------------------------------
# Graceful Shutdown
# ---------------------------------------------------------------------------

class TestGracefulShutdown:
    async def test_stop_waits_for_in_flight_tasks(self, executor_with_db):
        """Stop should wait for in-flight tasks to complete within grace period."""
        completed = False

        async def slow_task():
            nonlocal completed
            await asyncio.sleep(0.1)
            completed = True

        handle = asyncio.create_task(slow_task())
        executor_with_db._in_flight.add(handle)
        handle.add_done_callback(executor_with_db._in_flight.discard)

        await executor_with_db.stop(grace_seconds=5)

        assert completed is True
        assert len(executor_with_db._in_flight) == 0

    async def test_stop_cancels_after_grace_period(self, executor_with_db):
        """Tasks still running after grace period should be cancelled."""
        cancelled = False

        async def very_slow_task():
            nonlocal cancelled
            try:
                await asyncio.sleep(60)  # Way longer than grace
            except asyncio.CancelledError:
                cancelled = True
                raise

        handle = asyncio.create_task(very_slow_task())
        executor_with_db._in_flight.add(handle)

        await executor_with_db.stop(grace_seconds=0.1)

        assert cancelled is True
        assert len(executor_with_db._in_flight) == 0

    async def test_stop_with_no_in_flight(self, executor_with_db):
        """Stop with no in-flight tasks should complete immediately."""
        await executor_with_db.stop(grace_seconds=1)
        assert len(executor_with_db._in_flight) == 0
