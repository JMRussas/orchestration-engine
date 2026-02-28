#  Orchestration Engine - Progress Manager Unit Tests
#
#  Tests for event persistence and broadcast.
#
#  Depends on: backend/services/progress.py, tests/conftest.py
#  Used by:    pytest

import asyncio
import json
import time


from backend.services.progress import ProgressManager


async def _create_project(db, project_id="proj1"):
    """Helper: insert a project + plan row so FK constraints pass."""
    now = time.time()
    await db.execute_write(
        "INSERT OR IGNORE INTO projects (id, name, requirements, status, created_at, updated_at) "
        "VALUES (?, 'Test', 'test', 'draft', ?, ?)",
        (project_id, now, now),
    )
    await db.execute_write(
        "INSERT OR IGNORE INTO plans (id, project_id, version, model_used, plan_json, status, created_at) "
        "VALUES (?, ?, 1, 'test', '{}', 'approved', ?)",
        (f"plan_{project_id}", project_id, now),
    )


async def _create_task(db, task_id, project_id="proj1"):
    """Helper: insert a task row so FK constraints pass (project must exist)."""
    now = time.time()
    await db.execute_write(
        "INSERT OR IGNORE INTO tasks (id, project_id, plan_id, title, description, "
        "task_type, priority, status, model_tier, wave, retry_count, max_retries, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, 'Test', 'test', 'code', 0, 'pending', 'haiku', 0, 0, 5, ?, ?)",
        (task_id, project_id, f"plan_{project_id}", now, now),
    )


class TestPushEvent:
    async def test_push_event_persists_to_db(self, tmp_db):
        await _create_project(tmp_db, "proj1")
        await _create_task(tmp_db, "t1", "proj1")
        pm = ProgressManager(db=tmp_db)
        await pm.push_event("proj1", "task_started", message="Starting task A", task_id="t1")

        rows = await tmp_db.fetchall(
            "SELECT * FROM task_events WHERE project_id = 'proj1'"
        )
        assert len(rows) == 1
        assert rows[0]["event_type"] == "task_started"
        assert rows[0]["message"] == "Starting task A"
        assert rows[0]["task_id"] == "t1"

    async def test_push_event_with_extra_data(self, tmp_db):
        await _create_project(tmp_db, "proj1")
        pm = ProgressManager(db=tmp_db)
        await pm.push_event("proj1", "task_progress", progress=42, detail="halfway")

        rows = await tmp_db.fetchall(
            "SELECT data_json FROM task_events WHERE project_id = 'proj1'"
        )
        data = json.loads(rows[0]["data_json"])
        assert data["progress"] == 42
        assert data["detail"] == "halfway"

    async def test_push_event_broadcasts_to_subscribers(self, tmp_db):
        await _create_project(tmp_db, "proj1")
        pm = ProgressManager(db=tmp_db)
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        pm._subscribers.setdefault("proj1", []).append(queue)

        await pm.push_event("proj1", "task_completed", message="Done")

        event = queue.get_nowait()
        assert event["type"] == "task_completed"
        assert event["message"] == "Done"

    async def test_push_event_no_subscribers_ok(self, tmp_db):
        await _create_project(tmp_db, "proj1")
        pm = ProgressManager(db=tmp_db)
        # Should not raise even with no subscribers
        await pm.push_event("proj1", "task_started")

    async def test_push_event_drops_when_queue_full(self, tmp_db):
        await _create_project(tmp_db, "proj1")
        pm = ProgressManager(db=tmp_db)
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        queue.put_nowait({"type": "filler"})  # fill it
        pm._subscribers.setdefault("proj1", []).append(queue)

        # Should not raise even though queue is full
        await pm.push_event("proj1", "task_started")
        assert queue.qsize() == 1  # unchanged — event was dropped


class TestGetEvents:
    async def test_get_events_returns_persisted(self, tmp_db):
        await _create_project(tmp_db, "proj1")
        await _create_task(tmp_db, "t1", "proj1")
        pm = ProgressManager(db=tmp_db)
        await pm.push_event("proj1", "task_started", task_id="t1")
        await pm.push_event("proj1", "task_completed", task_id="t1")

        events = await pm.get_events("proj1")
        assert len(events) == 2
        assert events[0]["event_type"] == "task_started"
        assert events[1]["event_type"] == "task_completed"

    async def test_get_events_filter_by_task_id(self, tmp_db):
        await _create_project(tmp_db, "proj1")
        await _create_task(tmp_db, "t1", "proj1")
        await _create_task(tmp_db, "t2", "proj1")
        pm = ProgressManager(db=tmp_db)
        await pm.push_event("proj1", "task_started", task_id="t1")
        await pm.push_event("proj1", "task_started", task_id="t2")

        events = await pm.get_events("proj1", task_id="t1")
        assert len(events) == 1
        assert events[0]["task_id"] == "t1"

    async def test_get_events_respects_limit(self, tmp_db):
        await _create_project(tmp_db, "proj1")
        pm = ProgressManager(db=tmp_db)
        for i in range(5):
            await pm.push_event("proj1", f"event_{i}")

        events = await pm.get_events("proj1", limit=3)
        assert len(events) == 3

    async def test_get_events_empty_project(self, tmp_db):
        pm = ProgressManager(db=tmp_db)
        events = await pm.get_events("no_such_project")
        assert events == []
