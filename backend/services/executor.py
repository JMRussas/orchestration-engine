#  Orchestration Engine - Task Executor
#
#  Async worker pool that executes tasks via Claude API or Ollama,
#  with tool support, dependency resolution, budget enforcement,
#  wave-based dispatch, and context forwarding.
#
#  Depends on: backend/config.py, backend/db/connection.py,
#              services/budget.py, services/model_router.py,
#              services/resource_monitor.py, services/progress.py,
#              services/task_lifecycle.py, tools/registry.py
#  Used by:    container.py, app.py (background task)

import asyncio
import json
import logging
import time

import anthropic

from backend.config import (
    ANTHROPIC_API_KEY,
    MAX_CONCURRENT_TASKS,
    SHUTDOWN_GRACE_SECONDS,
    STALE_TASK_THRESHOLD_SECONDS,
    TICK_INTERVAL,
    WAVE_CHECKPOINTS,
)
from backend.models.enums import ModelTier, ProjectStatus, TaskStatus
from backend.services.model_router import calculate_cost, get_model_id
from backend.services.task_lifecycle import execute_task

logger = logging.getLogger("orchestration.executor")

# Token estimate for budget reservation before task execution
_EST_TASK_INPUT_TOKENS = 1500  # system prompt + context + tool definitions


class Executor:
    """Async task executor with concurrency control and tool support."""

    def __init__(self, db, budget, progress, resource_monitor, tool_registry, http_client=None):
        self._db = db
        self._budget = budget
        self._progress = progress
        self._resource_monitor = resource_monitor
        self._tool_registry = tool_registry
        self._http = http_client  # Shared httpx client for Ollama calls
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        self._task: asyncio.Task | None = None
        self._running = False
        self._dispatched: set[str] = set()  # Task IDs currently dispatched (prevents duplicate dispatch)
        self._in_flight: set[asyncio.Task] = set()  # Tracked task handles for clean shutdown
        self._client: anthropic.AsyncAnthropic | None = None  # Shared Anthropic client
        self._retry_after: dict[str, float] = {}  # task_id → earliest retry timestamp

    async def start(self):
        """Start the executor loop. Recovers stale tasks from prior crashes."""
        if self._running:
            return
        self._running = True
        self._client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        await self._recover_stale_tasks()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Executor started")

    async def stop(self, grace_seconds: float | None = None):
        """Stop the executor loop, waiting for in-flight tasks to finish.

        Args:
            grace_seconds: How long to wait for in-flight tasks before cancelling.
                           Defaults to SHUTDOWN_GRACE_SECONDS from config.
        """
        if grace_seconds is None:
            grace_seconds = SHUTDOWN_GRACE_SECONDS

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Wait for in-flight tasks up to the grace period
        if self._in_flight:
            logger.info("Waiting up to %.0fs for %d in-flight task(s)", grace_seconds, len(self._in_flight))
            done, pending = await asyncio.wait(
                list(self._in_flight), timeout=grace_seconds,
            )
            if pending:
                logger.warning("Grace period expired, cancelling %d task(s)", len(pending))
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            self._in_flight.clear()

        # Close the shared Anthropic client and clear state
        if self._client:
            await self._client.close()
            self._client = None
        self._retry_after.clear()
        logger.info("Executor stopped")

    async def _run_loop(self):
        """Main executor loop. Runs every tick_interval seconds."""
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error("Tick error: %s", e)
            await asyncio.sleep(TICK_INTERVAL)

    async def _recover_stale_tasks(self):
        """Reset tasks stuck in 'running' or 'queued' from a prior crash.

        Only recovers tasks whose updated_at is older than the configured
        stale threshold (default 5 min). Increments retry_count so the task
        doesn't silently restart without tracking the failed attempt.
        """
        cutoff = time.time() - STALE_TASK_THRESHOLD_SECONDS
        stale = await self._db.fetchall(
            "SELECT id, title, project_id, retry_count FROM tasks "
            "WHERE status IN (?, ?) AND updated_at < ?",
            (TaskStatus.RUNNING, TaskStatus.QUEUED, cutoff),
        )
        if not stale:
            return

        now = time.time()
        for row in stale:
            await self._db.execute_write(
                "UPDATE tasks SET status = ?, retry_count = retry_count + 1, "
                "error = ?, updated_at = ? WHERE id = ?",
                (TaskStatus.PENDING,
                 f"Recovered from stale state (retry {row['retry_count'] + 1})",
                 now, row["id"]),
            )
        logger.info("Recovered %d stale task(s) to pending", len(stale))

    async def _tick(self):
        """One executor tick: find ready tasks and dispatch them."""
        # Find projects that are executing
        projects = await self._db.fetchall(
            "SELECT id FROM projects WHERE status = ?",
            (ProjectStatus.EXECUTING,),
        )

        # Terminal statuses: tasks no longer active (done processing)
        _TERMINAL = (TaskStatus.COMPLETED, TaskStatus.FAILED,
                     TaskStatus.CANCELLED, TaskStatus.NEEDS_REVIEW)

        for project in projects:
            pid = project["id"]

            # Check budget
            if not await self._budget.can_spend(0.001):  # Minimal check
                await self._progress.push_event(pid, "budget_warning", "Budget limit reached. Execution paused.")
                await self._db.execute_write(
                    "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                    (ProjectStatus.PAUSED, time.time(), pid),
                )
                continue

            # Unblock tasks whose dependencies are now met
            await self._update_blocked_tasks(pid)

            # Determine the current wave (lowest wave with incomplete tasks)
            wave_row = await self._db.fetchone(
                "SELECT MIN(wave) as w FROM tasks "
                "WHERE project_id = ? AND status NOT IN (?, ?, ?, ?)",
                (pid, *_TERMINAL),
            )
            current_wave = wave_row["w"] if wave_row and wave_row["w"] is not None else 0

            # Find ready tasks: pending with all deps completed, filtered to current wave
            ready = await self._db.fetchall(
                "SELECT t.* FROM tasks t "
                "LEFT JOIN task_deps d ON d.task_id = t.id "
                "LEFT JOIN tasks dep ON dep.id = d.depends_on AND dep.status != ? "
                "WHERE t.project_id = ? AND t.status = ? AND t.wave = ? "
                "GROUP BY t.id HAVING COUNT(dep.id) = 0 "
                "ORDER BY t.priority ASC",
                (TaskStatus.COMPLETED, pid, TaskStatus.PENDING, current_wave),
            )

            for task_row in ready:
                task_id = task_row["id"]

                # Skip tasks still in retry backoff
                if task_id in self._retry_after and time.time() < self._retry_after[task_id]:
                    continue

                # Check resource availability for this task
                if not self._resources_available(task_row):
                    continue

                # Check per-project budget using reserve_spend (prevents TOCTOU race)
                tier = ModelTier(task_row["model_tier"])
                est_cost = 0.0
                if tier != ModelTier.OLLAMA:
                    est_cost = calculate_cost(get_model_id(tier), _EST_TASK_INPUT_TOKENS, task_row["max_tokens"])
                    if not await self._budget.reserve_spend(est_cost):
                        continue
                    if not await self._budget.can_spend_project(pid, est_cost):
                        await self._budget.release_reservation(est_cost)
                        continue

                # Atomic claim: only dispatch if we're the one who transitions pending→queued
                if task_row["id"] in self._dispatched:
                    if est_cost > 0:
                        await self._budget.release_reservation(est_cost)
                    continue
                cursor = await self._db.execute_write(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                    (TaskStatus.QUEUED, time.time(), task_row["id"], TaskStatus.PENDING),
                )
                if cursor.rowcount == 0:
                    if est_cost > 0:
                        await self._budget.release_reservation(est_cost)
                    continue  # Another tick already claimed it
                self._dispatched.add(task_row["id"])
                handle = asyncio.create_task(
                    execute_task(
                        task_row=task_row,
                        est_cost=est_cost,
                        db=self._db,
                        budget=self._budget,
                        progress=self._progress,
                        tool_registry=self._tool_registry,
                        http_client=self._http,
                        client=self._client,
                        semaphore=self._semaphore,
                        dispatched=self._dispatched,
                        retry_after=self._retry_after,
                    )
                )
                self._in_flight.add(handle)
                handle.add_done_callback(self._in_flight.discard)

            # Check for wave completion → optional checkpoint pause
            if WAVE_CHECKPOINTS:
                wave_remaining = await self._db.fetchone(
                    "SELECT COUNT(*) as cnt FROM tasks "
                    "WHERE project_id = ? AND wave = ? AND status NOT IN (?, ?, ?, ?)",
                    (pid, current_wave, *_TERMINAL),
                )
                if wave_remaining and wave_remaining["cnt"] == 0:
                    next_wave = await self._db.fetchone(
                        "SELECT MIN(wave) as w FROM tasks "
                        "WHERE project_id = ? AND status NOT IN (?, ?, ?, ?)",
                        (pid, *_TERMINAL),
                    )
                    if next_wave and next_wave["w"] is not None:
                        await self._db.execute_write(
                            "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                            (ProjectStatus.PAUSED, time.time(), pid),
                        )
                        await self._progress.push_event(
                            pid, "wave_checkpoint",
                            f"Wave {current_wave} complete. Resume to start wave {next_wave['w']}.",
                            wave=current_wave, next_wave=next_wave["w"],
                        )
                        continue

            # Check if all tasks are done
            remaining = await self._db.fetchone(
                "SELECT COUNT(*) as cnt FROM tasks WHERE project_id = ? AND status NOT IN (?, ?, ?, ?)",
                (pid, *_TERMINAL),
            )
            if remaining and remaining["cnt"] == 0:
                # All tasks reached a terminal state
                failed_cnt = await self._db.fetchone(
                    "SELECT COUNT(*) as cnt FROM tasks WHERE project_id = ? AND status = ?",
                    (pid, TaskStatus.FAILED),
                )
                has_failures = failed_cnt and failed_cnt["cnt"] > 0
                new_status = ProjectStatus.COMPLETED if not has_failures else ProjectStatus.FAILED
                await self._db.execute_write(
                    "UPDATE projects SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                    (new_status, time.time(), time.time(), pid),
                )
                event_type = "project_complete" if not has_failures else "project_failed"
                msg = "All tasks finished." if not has_failures else f"Project finished with {failed_cnt['cnt']} failed task(s)."
                await self._progress.push_event(pid, event_type, msg)
                continue

            # Detect dead projects: no tasks are pending/queued/running, but some are blocked
            active = await self._db.fetchone(
                "SELECT COUNT(*) as cnt FROM tasks WHERE project_id = ? AND status IN (?, ?, ?)",
                (pid, TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING),
            )
            if active and active["cnt"] == 0:
                blocked = await self._db.fetchone(
                    "SELECT COUNT(*) as cnt FROM tasks WHERE project_id = ? AND status = ?",
                    (pid, TaskStatus.BLOCKED),
                )
                if blocked and blocked["cnt"] > 0:
                    await self._db.execute_write(
                        "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                        (ProjectStatus.FAILED, time.time(), pid),
                    )
                    await self._progress.push_event(
                        pid, "project_failed",
                        f"No forward progress possible: {blocked['cnt']} task(s) blocked by failed dependencies.",
                    )

    async def _update_blocked_tasks(self, project_id: str):
        """Unblock tasks whose dependencies are now all completed (single query)."""
        now = time.time()
        await self._db.execute_write(
            "UPDATE tasks SET status = ?, updated_at = ? "
            "WHERE project_id = ? AND status = ? "
            "AND id NOT IN ("
            "  SELECT d.task_id FROM task_deps d "
            "  JOIN tasks dep ON dep.id = d.depends_on "
            "  WHERE dep.status != ?"
            ")",
            (TaskStatus.PENDING, now, project_id, TaskStatus.BLOCKED, TaskStatus.COMPLETED),
        )

    def _resources_available(self, task_row) -> bool:
        """Check if the resources this task needs are available."""
        tier = ModelTier(task_row["model_tier"])
        tools = json.loads(task_row["tools_json"]) if task_row["tools_json"] else []

        # Ollama tasks need Ollama online
        if tier == ModelTier.OLLAMA:
            if not self._resource_monitor.is_available("ollama_local"):
                return False

        # Claude tasks need API key
        if tier in (ModelTier.HAIKU, ModelTier.SONNET, ModelTier.OPUS):
            if not self._resource_monitor.is_available("anthropic_api"):
                return False

        # ComfyUI tool needs ComfyUI online
        if "generate_image" in tools:
            if not (self._resource_monitor.is_available("comfyui_local") or
                    self._resource_monitor.is_available("comfyui_server")):
                return False

        # RAG tools need Ollama for embeddings
        if any(t in tools for t in ("search_knowledge", "lookup_type")):
            # lookup_type doesn't need Ollama (FTS only), but search_knowledge does
            if "search_knowledge" in tools and not self._resource_monitor.is_available("ollama_local"):
                return False

        return True
