#  Orchestration Engine - Task Executor
#
#  Async worker pool that executes tasks via Claude API or Ollama,
#  with tool support, dependency resolution, and budget enforcement.
#
#  Depends on: backend/config.py, backend/db/connection.py,
#              services/budget.py, services/model_router.py,
#              services/resource_monitor.py, services/progress.py,
#              tools/registry.py
#  Used by:    container.py, app.py (background task)

import asyncio
import json
import logging
import random
import time

import anthropic
import httpx

logger = logging.getLogger("orchestration.executor")

from backend.config import (
    ANTHROPIC_API_KEY,
    API_TIMEOUT,
    MAX_CONCURRENT_TASKS,
    MAX_TOOL_ROUNDS,
    OLLAMA_DEFAULT_MODEL,
    OLLAMA_GENERATE_TIMEOUT,
    OLLAMA_HOSTS,
    TICK_INTERVAL,
)
from backend.models.enums import ModelTier, ProjectStatus, TaskStatus
from backend.services.model_router import calculate_cost, get_model_id

# Token estimate for budget reservation before task execution
_EST_TASK_INPUT_TOKENS = 1500  # system prompt + context + tool definitions

# Transient errors that warrant automatic retry with backoff
_TRANSIENT_ERRORS = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
    httpx.ConnectError,
    httpx.ReadTimeout,
)


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
        """Start the executor loop."""
        if self._running:
            return
        self._running = True
        self._client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Executor started")

    async def stop(self):
        """Stop the executor loop and cancel in-flight tasks."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Cancel all in-flight task executions
        for t in list(self._in_flight):
            t.cancel()
        if self._in_flight:
            await asyncio.gather(*self._in_flight, return_exceptions=True)
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

    async def _tick(self):
        """One executor tick: find ready tasks and dispatch them."""
        # Find projects that are executing
        projects = await self._db.fetchall(
            "SELECT id FROM projects WHERE status = ?",
            (ProjectStatus.EXECUTING,),
        )

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

            # Find ready tasks: pending with all deps completed (single query, no N+1)
            ready = await self._db.fetchall(
                "SELECT t.* FROM tasks t "
                "LEFT JOIN task_deps d ON d.task_id = t.id "
                "LEFT JOIN tasks dep ON dep.id = d.depends_on AND dep.status != ? "
                "WHERE t.project_id = ? AND t.status = ? "
                "GROUP BY t.id HAVING COUNT(dep.id) = 0 "
                "ORDER BY t.priority ASC",
                (TaskStatus.COMPLETED, pid, TaskStatus.PENDING),
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
                handle = asyncio.create_task(self._execute_task(task_row, est_cost))
                self._in_flight.add(handle)
                handle.add_done_callback(self._in_flight.discard)

            # Check if all tasks are done
            remaining = await self._db.fetchone(
                "SELECT COUNT(*) as cnt FROM tasks WHERE project_id = ? AND status NOT IN (?, ?, ?)",
                (pid, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED),
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

    async def _execute_task(self, task_row, est_cost: float = 0.0):
        """Execute a single task with semaphore-controlled concurrency."""
        task_id = task_row["id"]
        try:
            async with self._semaphore:
                project_id = task_row["project_id"]
                tier = ModelTier(task_row["model_tier"])

                # Mark as running
                now = time.time()
                await self._db.execute_write(
                    "UPDATE tasks SET status = ?, started_at = ?, updated_at = ? WHERE id = ?",
                    (TaskStatus.RUNNING, now, now, task_id),
                )
                await self._progress.push_event(
                    project_id, "task_start", task_row["title"], task_id=task_id
                )

                try:
                    if tier == ModelTier.OLLAMA:
                        result = await self._run_ollama_task(task_row)
                    else:
                        result = await self._run_claude_task(task_row, est_cost)

                    # Mark completed, clean up retry tracking
                    self._retry_after.pop(task_id, None)
                    await self._db.execute_write(
                        "UPDATE tasks SET status = ?, output_text = ?, "
                        "prompt_tokens = ?, completion_tokens = ?, cost_usd = ?, "
                        "model_used = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                        (
                            TaskStatus.COMPLETED, result["output"],
                            result["prompt_tokens"], result["completion_tokens"],
                            result["cost_usd"], result["model_used"],
                            time.time(), time.time(), task_id,
                        ),
                    )
                    await self._progress.push_event(
                        project_id, "task_complete", task_row["title"],
                        task_id=task_id, cost_usd=result["cost_usd"],
                    )

                except _TRANSIENT_ERRORS as e:
                    retry_count = task_row["retry_count"]
                    max_retries = task_row["max_retries"]
                    if retry_count < max_retries:
                        # Schedule retry via _retry_after instead of sleeping
                        # inside the semaphore. The tick loop will re-dispatch
                        # once the backoff period expires.
                        delay = min(5 * (2 ** retry_count) + random.uniform(0, 2), 120)
                        self._retry_after[task_id] = time.time() + delay
                        await self._db.execute_write(
                            "UPDATE tasks SET status = ?, retry_count = retry_count + 1, "
                            "error = ?, updated_at = ? WHERE id = ?",
                            (TaskStatus.PENDING, f"Transient error (retry {retry_count + 1}): {e}",
                             time.time(), task_id),
                        )
                        await self._progress.push_event(
                            project_id, "task_retry",
                            f"{task_row['title']}: retrying in {delay:.0f}s ({e})",
                            task_id=task_id,
                        )
                    else:
                        self._retry_after.pop(task_id, None)
                        error_msg = f"Max retries exceeded: {e}"
                        await self._db.execute_write(
                            "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                            (TaskStatus.FAILED, error_msg, time.time(), task_id),
                        )
                        await self._progress.push_event(
                            project_id, "task_failed", f"{task_row['title']}: {error_msg}",
                            task_id=task_id,
                        )

                except Exception as e:
                    self._retry_after.pop(task_id, None)
                    error_msg = str(e)
                    await self._db.execute_write(
                        "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                        (TaskStatus.FAILED, error_msg, time.time(), task_id),
                    )
                    await self._progress.push_event(
                        project_id, "task_failed", f"{task_row['title']}: {error_msg}",
                        task_id=task_id,
                    )
        finally:
            self._dispatched.discard(task_id)
            if est_cost > 0:
                await self._budget.release_reservation(est_cost)

    async def _run_claude_task(self, task_row, est_cost: float = 0.0) -> dict:
        """Execute a task via the Claude API with tool support.

        Args:
            est_cost: The original reserved cost estimate. Used for mid-loop
                budget checks — if actual spend exceeds the estimate, we verify
                the global budget hasn't been exhausted before continuing.
        """
        tier = ModelTier(task_row["model_tier"])
        model_id = get_model_id(tier)
        task_id = task_row["id"]
        project_id = task_row["project_id"]

        # Build context
        context = json.loads(task_row["context_json"]) if task_row["context_json"] else []
        system_parts = [task_row["system_prompt"] or "You are a focused task executor."]
        for ctx in context:
            system_parts.append(f"\n[{ctx.get('type', 'context')}]\n{ctx.get('content', '')}")
        system_prompt = "\n".join(system_parts)

        # Build tool definitions
        tool_names = json.loads(task_row["tools_json"]) if task_row["tools_json"] else []
        tools = self._tool_registry.get_many(tool_names)
        tool_defs = [t.to_claude_tool() for t in tools]
        tool_map = {t.name: t for t in tools}

        # Initial message
        messages = [{"role": "user", "content": task_row["description"]}]

        client = self._client
        if client is None:
            raise RuntimeError("Executor not started — call start() before dispatching tasks")

        total_prompt = 0
        total_completion = 0
        total_cost = 0.0
        text_parts: list[str] = []
        budget_exhausted = False

        for round_num in range(MAX_TOOL_ROUNDS):
            # Make API call
            kwargs = {
                "model": model_id,
                "max_tokens": task_row["max_tokens"],
                "system": system_prompt,
                "messages": messages,
                "timeout": API_TIMEOUT,
            }
            if tool_defs:
                kwargs["tools"] = tool_defs

            response = await client.messages.create(**kwargs)

            # Record usage
            pt = response.usage.input_tokens
            ct = response.usage.output_tokens
            cost = calculate_cost(model_id, pt, ct)
            total_prompt += pt
            total_completion += ct
            total_cost += cost

            await self._budget.record_spend(
                cost_usd=cost,
                prompt_tokens=pt,
                completion_tokens=ct,
                provider="anthropic",
                model=model_id,
                purpose="execution",
                project_id=project_id,
                task_id=task_id,
            )

            # Per-round budget check: if actual cost exceeded the original estimate,
            # verify that global budget hasn't been exhausted before continuing.
            if total_cost > est_cost and not await self._budget.can_spend(0.001):
                logger.warning(
                    "Budget exhausted mid-tool-loop for task %s after %d rounds, "
                    "returning partial result",
                    task_id, round_num + 1,
                )
                budget_exhausted = True

            # Process response
            has_tool_use = False
            tool_results = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    has_tool_use = True
                    tool_name = block.name
                    tool_input = block.input

                    await self._progress.push_event(
                        project_id, "tool_call", f"Calling {tool_name}",
                        task_id=task_id, tool=tool_name,
                    )

                    # Auto-inject project_id for file tools
                    if tool_name in ("read_file", "write_file"):
                        tool_input["project_id"] = project_id

                    # Execute tool
                    tool = tool_map.get(tool_name)
                    if tool:
                        try:
                            result = await tool.execute(tool_input)
                        except Exception as e:
                            result = f"Tool error: {e}"
                    else:
                        result = f"Unknown tool: {tool_name}"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            if not has_tool_use or budget_exhausted:
                break

            # Feed tool results back
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        return {
            "output": "\n".join(text_parts),
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "cost_usd": round(total_cost, 6),
            "model_used": model_id,
        }

    async def _run_ollama_task(self, task_row) -> dict:
        """Execute a task via local Ollama (free)."""
        model = OLLAMA_DEFAULT_MODEL
        host_url = OLLAMA_HOSTS.get("local", "http://localhost:11434")

        # Build context
        context = json.loads(task_row["context_json"]) if task_row["context_json"] else []
        system_parts = [task_row["system_prompt"] or "You are a focused task executor."]
        for ctx in context:
            system_parts.append(f"\n[{ctx.get('type', 'context')}]\n{ctx.get('content', '')}")
        system_prompt = "\n".join(system_parts)

        body = {
            "model": model,
            "prompt": task_row["description"],
            "system": system_prompt,
            "stream": False,
        }

        client = self._http or httpx.AsyncClient(timeout=OLLAMA_GENERATE_TIMEOUT)
        try:
            resp = await client.post(
                f"{host_url}/api/generate", json=body, timeout=OLLAMA_GENERATE_TIMEOUT
            )
        finally:
            if not self._http:
                await client.aclose()
        resp.raise_for_status()
        data = resp.json()

        output = data.get("response", "")
        # Ollama provides token counts in some versions
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)

        # Record usage (cost = 0 for Ollama)
        await self._budget.record_spend(
            cost_usd=0.0,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            provider="ollama",
            model=model,
            purpose="execution",
            project_id=task_row["project_id"],
            task_id=task_row["id"],
        )

        return {
            "output": output,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": 0.0,
            "model_used": model,
        }
