#  Orchestration Engine - C# Build Verification Hook Tests
#
#  Tests for _run_csharp_build_verification() in task_lifecycle.py.
#
#  Depends on: backend/services/task_lifecycle.py
#  Used by:    CI

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.models.enums import TaskStatus


def _make_task_row(task_type="csharp_assembly", csproj_path="src/MyApp/MyApp.csproj", **overrides):
    """Build a minimal task row dict for testing."""
    context = [
        {"type": "task_description", "content": "Assemble methods"},
        {"type": "target_class", "content": "MyApp.Services.UserService"},
    ]
    if csproj_path:
        context.append({"type": "csproj_path", "content": csproj_path})

    row = {
        "id": "task-1",
        "title": "Assemble UserService",
        "project_id": "proj-1",
        "task_type": task_type,
        "context_json": json.dumps(context),
        "retry_count": 0,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_assembly_task_build_success():
    """Successful build sets verification_status=passed."""
    from backend.services.task_lifecycle import _run_csharp_build_verification

    db = AsyncMock()
    progress = AsyncMock()
    task_row = _make_task_row()

    with patch("backend.services.task_lifecycle.CSHARP_BUILD_VERIFY_ENABLED", True), \
         patch("backend.services.task_lifecycle.verify_csharp_build", return_value=(True, "Build succeeded")):
        result = await _run_csharp_build_verification(task_row, "task-1", db, progress, "proj-1")

    assert result is False  # Not reset
    db.execute_write.assert_called_once()
    sql = db.execute_write.call_args[0][0]
    assert "verification_status" in sql
    params = db.execute_write.call_args[0][1]
    assert params[0] == "passed"


@pytest.mark.asyncio
async def test_assembly_task_build_failure_resets_to_pending():
    """Failed build resets task to PENDING with error feedback in context."""
    from backend.services.task_lifecycle import _run_csharp_build_verification

    db = AsyncMock()
    progress = AsyncMock()
    task_row = _make_task_row()

    with patch("backend.services.task_lifecycle.CSHARP_BUILD_VERIFY_ENABLED", True), \
         patch("backend.services.task_lifecycle.verify_csharp_build",
               return_value=(False, "Build errors:\nerror CS1002: ; expected")):
        result = await _run_csharp_build_verification(task_row, "task-1", db, progress, "proj-1")

    assert result is True  # Task was reset
    db.execute_write.assert_called_once()
    sql = db.execute_write.call_args[0][0]
    params = db.execute_write.call_args[0][1]
    assert params[0] == TaskStatus.PENDING
    assert "verification_status" in sql
    assert params[1] == "failed"
    # Build errors injected into context
    new_context = json.loads(params[3])
    feedback_entries = [e for e in new_context if e["type"] == "build_error_feedback"]
    assert len(feedback_entries) == 1
    assert "CS1002" in feedback_entries[0]["content"]

    progress.push_event.assert_called_once()
    event_args = progress.push_event.call_args
    assert event_args[0][1] == "task_verification_retry"


@pytest.mark.asyncio
async def test_non_assembly_task_skipped():
    """Non-assembly tasks are not verified."""
    from backend.services.task_lifecycle import _run_csharp_build_verification

    db = AsyncMock()
    progress = AsyncMock()
    task_row = _make_task_row(task_type="csharp_method")

    with patch("backend.services.task_lifecycle.CSHARP_BUILD_VERIFY_ENABLED", True):
        result = await _run_csharp_build_verification(task_row, "task-1", db, progress, "proj-1")

    assert result is False
    db.execute_write.assert_not_called()


@pytest.mark.asyncio
async def test_missing_csproj_path_skipped():
    """Assembly task without csproj_path in context is skipped with warning."""
    from backend.services.task_lifecycle import _run_csharp_build_verification

    db = AsyncMock()
    progress = AsyncMock()
    task_row = _make_task_row(csproj_path=None)

    with patch("backend.services.task_lifecycle.CSHARP_BUILD_VERIFY_ENABLED", True):
        result = await _run_csharp_build_verification(task_row, "task-1", db, progress, "proj-1")

    assert result is False
    db.execute_write.assert_not_called()


@pytest.mark.asyncio
async def test_config_disabled_skips_verification():
    """When csharp_build_verify is false, verification is skipped entirely."""
    from backend.services.task_lifecycle import _run_csharp_build_verification

    db = AsyncMock()
    progress = AsyncMock()
    task_row = _make_task_row()

    with patch("backend.services.task_lifecycle.CSHARP_BUILD_VERIFY_ENABLED", False):
        result = await _run_csharp_build_verification(task_row, "task-1", db, progress, "proj-1")

    assert result is False
    db.execute_write.assert_not_called()


@pytest.mark.asyncio
async def test_decomposer_injects_csproj_path():
    """Assembly tasks created by decomposer include csproj_path from project config."""
    from backend.services.decomposer import _create_csharp_assembly_tasks

    tasks_data = [
        {"task_type": "csharp_method", "target_class": "MyApp.UserService",
         "title": "Implement GetUser", "description": "...", "affected_files": ["UserService.cs"]},
    ]
    task_ids = ["tid-0"]
    waves = [0]
    phase_names = ["Phase 1"]
    write_statements = []

    project_row = {
        "config_json": json.dumps({"csproj_path": "src/MyApp/MyApp.csproj"}),
    }

    _create_csharp_assembly_tasks(
        tasks_data, task_ids, waves, phase_names,
        "proj-1", "plan-1", 1000.0, write_statements,
        project_row=project_row,
    )

    # Should have created 1 INSERT for the assembly task + 1 INSERT for the dep edge
    assert len(write_statements) == 2
    insert_sql, insert_params = write_statements[0]
    assert "csharp_assembly" in str(insert_params)

    # Verify csproj_path is in the context_json
    context_json_str = insert_params[9]  # context_json is the 10th param
    context = json.loads(context_json_str)
    csproj_entries = [e for e in context if e["type"] == "csproj_path"]
    assert len(csproj_entries) == 1
    assert csproj_entries[0]["content"] == "src/MyApp/MyApp.csproj"
