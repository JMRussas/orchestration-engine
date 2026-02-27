#  Orchestration Engine - Task Lifecycle
#
#  Core task execution flow: dispatch, verify, checkpoint, context forwarding.
#  Extracted from executor.py for modularity.
#
#  Depends on: config.py, services/claude_agent.py, services/ollama_agent.py,
#              services/budget.py, services/progress.py
#  Used by:    services/executor.py

import json
import logging
import random
import time
import uuid

import anthropic
import httpx

from backend.config import (
    CHECKPOINT_ON_RETRY_EXHAUSTED,
    CONTEXT_FORWARD_MAX_CHARS,
    VERIFICATION_ENABLED,
)
from backend.logging_config import set_task_id
from backend.models.enums import ModelTier, TaskStatus
from backend.services.claude_agent import run_claude_task
from backend.services.ollama_agent import run_ollama_task

logger = logging.getLogger("orchestration.executor")

# Transient errors that warrant automatic retry with backoff
_TRANSIENT_ERRORS = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
    httpx.ConnectError,
    httpx.ReadTimeout,
)


async def create_checkpoint(
    *, project_id, task_id, task_row, error_msg, db, progress,
):
    """Create a checkpoint for a task that exhausted retries.

    Sets the task to NEEDS_REVIEW and creates a structured checkpoint record
    with attempt history for the user to resolve.
    """
    checkpoint_id = uuid.uuid4().hex[:12]

    # Gather attempt history from task_events
    events = await db.fetchall(
        "SELECT message, timestamp FROM task_events "
        "WHERE task_id = ? AND event_type IN ('task_retry', 'task_failed') "
        "ORDER BY timestamp",
        (task_id,),
    )
    attempts = [
        {"message": e["message"], "timestamp": e["timestamp"]}
        for e in events
    ]

    await db.execute_write(
        "INSERT INTO checkpoints "
        "(id, project_id, task_id, checkpoint_type, summary, attempts_json, question, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            checkpoint_id, project_id, task_id, "retry_exhausted",
            f"Task '{task_row['title']}' failed after {task_row['max_retries']} attempts",
            json.dumps(attempts),
            "How should we proceed? Options: retry with modified approach, "
            "skip this task, or fail it.",
            time.time(),
        ),
    )

    await db.execute_write(
        "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
        (TaskStatus.NEEDS_REVIEW, error_msg, time.time(), task_id),
    )

    await progress.push_event(
        project_id, "checkpoint",
        f"Checkpoint: {task_row['title']} needs attention after {task_row['max_retries']} failed attempts",
        task_id=task_id, checkpoint_id=checkpoint_id,
    )


async def verify_task_output(
    *, task_row, output_text, project_id, task_id, db, client, budget, progress,
) -> bool:
    """Run output verification. Returns True if the task status was overridden."""
    from backend.services.verifier import verify_output
    from backend.models.enums import VerificationResult

    try:
        verification = await verify_output(
            task_title=task_row["title"],
            task_description=task_row["description"],
            output_text=output_text,
            client=client,
            budget=budget,
            project_id=project_id,
            task_id=task_id,
        )
    except Exception as e:
        # Verification failure should not block task completion
        logger.warning("Verification failed for task %s: %s", task_id, e)
        await db.execute_write(
            "UPDATE tasks SET verification_status = ?, verification_notes = ?, "
            "updated_at = ? WHERE id = ?",
            (VerificationResult.SKIPPED, f"Verification error: {e}",
             time.time(), task_id),
        )
        return False

    v_result = verification["result"]
    v_notes = verification["notes"]

    await db.execute_write(
        "UPDATE tasks SET verification_status = ?, verification_notes = ?, "
        "updated_at = ? WHERE id = ?",
        (v_result, v_notes, time.time(), task_id),
    )

    if v_result == VerificationResult.GAPS_FOUND:
        retry_count = task_row["retry_count"]
        max_retries = task_row["max_retries"]
        if retry_count < max_retries:
            # Auto-retry with verification feedback appended to context
            ctx = json.loads(task_row["context_json"]) if task_row["context_json"] else []
            ctx.append({
                "type": "verification_feedback",
                "content": f"Previous attempt had gaps: {v_notes}. Address these issues.",
            })
            await db.execute_write(
                "UPDATE tasks SET status = ?, context_json = ?, "
                "retry_count = retry_count + 1, completed_at = NULL, updated_at = ? WHERE id = ?",
                (TaskStatus.PENDING, json.dumps(ctx), time.time(), task_id),
            )
            await progress.push_event(
                project_id, "task_verification_retry",
                f"{task_row['title']}: gaps found, retrying with feedback",
                task_id=task_id, verification_notes=v_notes,
            )
            return True

    if v_result == VerificationResult.HUMAN_NEEDED:
        await db.execute_write(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (TaskStatus.NEEDS_REVIEW, time.time(), task_id),
        )
        await progress.push_event(
            project_id, "task_needs_review",
            f"{task_row['title']}: requires human review",
            task_id=task_id, verification_notes=v_notes,
        )
        return True

    return False


async def forward_context(*, completed_task, output_text, db):
    """Inject completed task's output summary into dependent tasks' context."""
    deps = await db.fetchall(
        "SELECT task_id FROM task_deps WHERE depends_on = ?",
        (completed_task["id"],),
    )
    if not deps:
        return

    # Truncate output to configured max for context injection
    summary = (output_text or "")[:CONTEXT_FORWARD_MAX_CHARS]
    context_entry = {
        "type": "dependency_output",
        "source_task_id": completed_task["id"],
        "source_task_title": completed_task["title"],
        "content": summary,
    }

    for dep in deps:
        dep_task = await db.fetchone(
            "SELECT context_json FROM tasks WHERE id = ?", (dep["task_id"],),
        )
        if dep_task:
            ctx = json.loads(dep_task["context_json"]) if dep_task["context_json"] else []
            ctx.append(context_entry)
            await db.execute_write(
                "UPDATE tasks SET context_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(ctx), time.time(), dep["task_id"]),
            )


async def execute_task(
    *,
    task_row,
    est_cost: float = 0.0,
    db,
    budget,
    progress,
    tool_registry,
    http_client,
    client,
    semaphore,
    dispatched: set,
    retry_after: dict,
):
    """Execute a single task with semaphore-controlled concurrency.

    Args:
        task_row: Task database row.
        est_cost: Budget reservation estimate (0 for Ollama).
        db: Database instance.
        budget: BudgetManager instance.
        progress: ProgressManager instance.
        tool_registry: ToolRegistry for tool definitions.
        http_client: Shared httpx.AsyncClient (or None).
        client: anthropic.AsyncAnthropic instance.
        semaphore: asyncio.Semaphore for concurrency control.
        dispatched: Mutable set of currently dispatched task IDs.
        retry_after: Mutable dict of task_id → earliest retry timestamp.
    """
    task_id = task_row["id"]
    set_task_id(task_id)
    try:
        async with semaphore:
            project_id = task_row["project_id"]
            tier = ModelTier(task_row["model_tier"])

            # Mark as running
            now = time.time()
            await db.execute_write(
                "UPDATE tasks SET status = ?, started_at = ?, updated_at = ? WHERE id = ?",
                (TaskStatus.RUNNING, now, now, task_id),
            )
            await progress.push_event(
                project_id, "task_start", task_row["title"], task_id=task_id
            )

            try:
                if tier == ModelTier.OLLAMA:
                    result = await run_ollama_task(
                        task_row=task_row, http_client=http_client, budget=budget,
                    )
                else:
                    result = await run_claude_task(
                        task_row=task_row, est_cost=est_cost, client=client,
                        tool_registry=tool_registry, budget=budget, progress=progress,
                    )

                # Mark completed, clean up retry tracking
                retry_after.pop(task_id, None)
                await db.execute_write(
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

                # Optional output verification (skip for Ollama — free tasks)
                if VERIFICATION_ENABLED and tier != ModelTier.OLLAMA and client:
                    verification_overridden = await verify_task_output(
                        task_row=task_row, output_text=result["output"],
                        project_id=project_id, task_id=task_id,
                        db=db, client=client, budget=budget, progress=progress,
                    )
                    if verification_overridden:
                        return  # Task was reset to PENDING or NEEDS_REVIEW

                await progress.push_event(
                    project_id, "task_complete", task_row["title"],
                    task_id=task_id, cost_usd=result["cost_usd"],
                )

                # Forward output to dependent tasks' context
                await forward_context(
                    completed_task=task_row, output_text=result["output"], db=db,
                )

            except _TRANSIENT_ERRORS as e:
                retry_count = task_row["retry_count"]
                max_retries = task_row["max_retries"]
                if retry_count < max_retries:
                    # Schedule retry via retry_after instead of sleeping
                    # inside the semaphore. The tick loop will re-dispatch
                    # once the backoff period expires.
                    delay = min(5 * (2 ** retry_count) + random.uniform(0, 2), 120)
                    retry_after[task_id] = time.time() + delay
                    await db.execute_write(
                        "UPDATE tasks SET status = ?, retry_count = retry_count + 1, "
                        "error = ?, updated_at = ? WHERE id = ?",
                        (TaskStatus.PENDING, f"Transient error (retry {retry_count + 1}): {e}",
                         time.time(), task_id),
                    )
                    await progress.push_event(
                        project_id, "task_retry",
                        f"{task_row['title']}: retrying in {delay:.0f}s ({e})",
                        task_id=task_id,
                    )
                else:
                    retry_after.pop(task_id, None)
                    error_msg = f"Max retries exceeded: {e}"

                    if CHECKPOINT_ON_RETRY_EXHAUSTED:
                        await create_checkpoint(
                            project_id=project_id, task_id=task_id,
                            task_row=task_row, error_msg=error_msg,
                            db=db, progress=progress,
                        )
                    else:
                        await db.execute_write(
                            "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                            (TaskStatus.FAILED, error_msg, time.time(), task_id),
                        )
                        await progress.push_event(
                            project_id, "task_failed", f"{task_row['title']}: {error_msg}",
                            task_id=task_id,
                        )

            except Exception as e:
                retry_after.pop(task_id, None)
                error_msg = str(e)
                await db.execute_write(
                    "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                    (TaskStatus.FAILED, error_msg, time.time(), task_id),
                )
                await progress.push_event(
                    project_id, "task_failed", f"{task_row['title']}: {error_msg}",
                    task_id=task_id,
                )
    finally:
        set_task_id(None)
        dispatched.discard(task_id)
        if est_cost > 0:
            await budget.release_reservation(est_cost)
