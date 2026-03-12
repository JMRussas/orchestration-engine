#  Orchestration Engine - Stale External Claim Recovery Tests
#
#  Tests for _recover_stale_external_claims() in executor.py.
#
#  Depends on: backend/services/executor.py
#  Used by:    CI

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.enums import TaskStatus


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.execute_write = AsyncMock()
    return db


@pytest.fixture
def mock_progress():
    progress = AsyncMock()
    progress.push_event = AsyncMock()
    return progress


@pytest.fixture
def executor(mock_db, mock_progress):
    from backend.services.executor import Executor

    return Executor(
        db=mock_db,
        budget=AsyncMock(),
        progress=mock_progress,
        resource_monitor=AsyncMock(),
        tool_registry=MagicMock(),
    )


@pytest.mark.asyncio
async def test_stale_claim_released(executor, mock_db, mock_progress):
    """Task with expired external claim is reset to PENDING."""
    stale_task = {
        "id": "task-1",
        "title": "Stale task",
        "project_id": "proj-1",
        "retry_count": 0,
    }
    mock_db.fetchall.return_value = [stale_task]

    with patch("backend.services.executor.EXTERNAL_CLAIM_TIMEOUT_SECONDS", 3600):
        await executor._recover_stale_external_claims()

    mock_db.execute_write.assert_called_once()
    args = mock_db.execute_write.call_args
    sql = args[0][0]
    params = args[0][1]
    assert "status = ?" in sql
    assert "claimed_by = NULL" in sql
    assert "claimed_at = NULL" in sql
    assert "retry_count = retry_count + 1" in sql
    assert params[0] == TaskStatus.PENDING

    mock_progress.push_event.assert_called_once()
    event_args = mock_progress.push_event.call_args[0]
    assert event_args[0] == "proj-1"
    assert event_args[1] == "task_retry"


@pytest.mark.asyncio
async def test_fresh_claim_untouched(executor, mock_db):
    """Task with claim within timeout is not affected (query won't match)."""
    mock_db.fetchall.return_value = []

    await executor._recover_stale_external_claims()

    mock_db.execute_write.assert_not_called()


@pytest.mark.asyncio
async def test_internal_running_task_unaffected(executor, mock_db):
    """Internally-dispatched RUNNING task (no claimed_by) is not affected.

    The SQL query filters on claimed_by IS NOT NULL, so internal tasks
    are never returned.
    """
    mock_db.fetchall.return_value = []

    await executor._recover_stale_external_claims()

    # Verify the query includes claimed_by IS NOT NULL
    call_args = mock_db.fetchall.call_args[0]
    sql = call_args[0]
    assert "claimed_by IS NOT NULL" in sql
    mock_db.execute_write.assert_not_called()


@pytest.mark.asyncio
async def test_multiple_stale_claims_all_recovered(executor, mock_db, mock_progress):
    """Multiple stale claims are all recovered in one tick."""
    stale_tasks = [
        {"id": f"task-{i}", "title": f"Stale {i}", "project_id": "proj-1", "retry_count": i}
        for i in range(3)
    ]
    mock_db.fetchall.return_value = stale_tasks

    with patch("backend.services.executor.EXTERNAL_CLAIM_TIMEOUT_SECONDS", 3600):
        await executor._recover_stale_external_claims()

    assert mock_db.execute_write.call_count == 3
    assert mock_progress.push_event.call_count == 3
