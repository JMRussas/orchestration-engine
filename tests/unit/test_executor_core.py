#  Orchestration Engine - Executor Core Tests
#
#  Tests for _run_claude_task, _run_ollama_task, _execute_task,
#  _resources_available, and _tick dispatch loop.
#
#  Depends on: backend/services/executor.py, backend/db/connection.py
#  Used by:    pytest

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.enums import ModelTier, ProjectStatus, TaskStatus
from backend.services.executor import Executor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def executor_with_db(tmp_db):
    """Executor wired to tmp_db with mocked external services."""
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
    mock_registry.get_many = MagicMock(return_value=[])

    executor = Executor(
        db=tmp_db,
        budget=mock_budget,
        progress=mock_progress,
        resource_monitor=mock_rm,
        tool_registry=mock_registry,
    )
    return executor


def _make_task_row(**overrides):
    """Build a task dict matching what DB queries return."""
    defaults = {
        "id": "task_001",
        "project_id": "proj_001",
        "plan_id": "plan_001",
        "title": "Test Task",
        "description": "Do something useful",
        "task_type": "code",
        "priority": 50,
        "status": "queued",
        "model_tier": "haiku",
        "model_used": None,
        "context_json": "[]",
        "tools_json": "[]",
        "system_prompt": "",
        "output_text": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": 0.0,
        "max_tokens": 4096,
        "retry_count": 0,
        "max_retries": 5,
        "wave": 0,
        "verification_status": None,
        "verification_notes": None,
        "error": None,
        "started_at": None,
        "completed_at": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    defaults.update(overrides)
    return defaults


def _make_claude_response(text="Task completed.", pt=100, ct=50):
    """Build a mock Anthropic API response with a text block."""
    response = MagicMock()
    response.usage = MagicMock(input_tokens=pt, output_tokens=ct)
    text_block = MagicMock(type="text", text=text)
    response.content = [text_block]
    return response


def _make_tool_use_response(tool_name, tool_input, tool_id="tu_001", pt=80, ct=40):
    """Build a mock Anthropic API response with a tool_use block."""
    response = MagicMock()
    response.usage = MagicMock(input_tokens=pt, output_tokens=ct)
    # MagicMock(name=...) sets internal _mock_name, not a .name attribute.
    # Set .name after construction so it becomes a regular attribute.
    tool_block = MagicMock(type="tool_use", input=tool_input, id=tool_id)
    tool_block.name = tool_name
    response.content = [tool_block]
    return response


async def _seed_task(db, task_id="task_001", project_id="proj_001",
                     plan_id="plan_001", status="queued", model_tier="haiku",
                     retry_count=0, max_retries=5, **kwargs):
    """Insert a project, plan, and task into the DB for execute_task tests."""
    now = time.time()
    await db.execute_write(
        "INSERT OR IGNORE INTO projects (id, name, requirements, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'executing', ?, ?)",
        (project_id, "Test Project", "Build X", now, now),
    )
    await db.execute_write(
        "INSERT OR IGNORE INTO plans (id, project_id, version, model_used, plan_json, status, created_at) "
        "VALUES (?, ?, 1, 'test', ?, 'approved', ?)",
        (plan_id, project_id, json.dumps({"summary": "test", "tasks": []}), now),
    )
    task_defaults = {
        "tools_json": "[]", "context_json": "[]", "system_prompt": "",
        "max_tokens": 4096, "wave": 0, "description": "Do something",
        "title": "Test Task", "task_type": "code", "priority": 50,
    }
    task_defaults.update(kwargs)
    await db.execute_write(
        "INSERT INTO tasks (id, project_id, plan_id, title, description, task_type, "
        "priority, status, model_tier, wave, retry_count, max_retries, "
        "tools_json, context_json, system_prompt, max_tokens, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, project_id, plan_id, task_defaults["title"],
         task_defaults["description"], task_defaults["task_type"],
         task_defaults["priority"], status, model_tier, task_defaults["wave"],
         retry_count, max_retries,
         task_defaults["tools_json"], task_defaults["context_json"],
         task_defaults["system_prompt"], task_defaults["max_tokens"],
         now, now),
    )


# ---------------------------------------------------------------------------
# TestRunClaudeTask
# ---------------------------------------------------------------------------

class TestRunClaudeTask:
    """Tests for Executor._run_claude_task."""

    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_simple_text_response(self, _mock_model, _mock_cost, executor_with_db):
        """A single text response returns correct output dict."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_claude_response("Hello world"))
        executor_with_db._client = mock_client

        task_row = _make_task_row()
        result = await executor_with_db._run_claude_task(task_row, est_cost=0.01)

        assert result["output"] == "Hello world"
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 50
        assert result["cost_usd"] >= 0
        assert result["model_used"] == "claude-haiku-4-5-20251001"
        mock_client.messages.create.assert_awaited_once()

    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_tool_use_then_text(self, _mock_model, _mock_cost, executor_with_db):
        """Tool use in first round, text in second round."""
        mock_tool = MagicMock()
        # MagicMock.name is special — use PropertyMock to make .name return a value
        type(mock_tool).name = property(lambda self: "search_knowledge")
        mock_tool.to_claude_tool.return_value = {"name": "search_knowledge", "description": "Search", "input_schema": {}}
        mock_tool.execute = AsyncMock(return_value="Found: class Foo {}")

        executor_with_db._tool_registry.get_many = MagicMock(return_value=[mock_tool])

        tool_response = _make_tool_use_response("search_knowledge", {"query": "Foo"})
        text_response = _make_claude_response("Done with tools.")

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[tool_response, text_response])
        executor_with_db._client = mock_client

        task_row = _make_task_row(tools_json='["search_knowledge"]')
        result = await executor_with_db._run_claude_task(task_row, est_cost=0.01)

        assert "Done with tools" in result["output"]
        assert mock_client.messages.create.await_count == 2
        mock_tool.execute.assert_awaited_once_with({"query": "Foo"})

    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_unknown_tool_returns_error(self, _mock_model, _mock_cost, executor_with_db):
        """Tool use with unknown tool name returns error string in result."""
        tool_response = _make_tool_use_response("nonexistent_tool", {"x": 1})
        text_response = _make_claude_response("Handled.")

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[tool_response, text_response])
        executor_with_db._client = mock_client

        task_row = _make_task_row()
        result = await executor_with_db._run_claude_task(task_row, est_cost=0.01)

        # The tool result fed back should contain "Unknown tool"
        second_call_args = mock_client.messages.create.call_args_list[1]
        messages = second_call_args.kwargs["messages"]
        user_msg = messages[-1]  # Last message is user with tool results
        tool_result_content = user_msg["content"][0]["content"]
        assert "Unknown tool" in tool_result_content

    async def test_null_client_raises(self, executor_with_db):
        """RuntimeError when _client is None."""
        executor_with_db._client = None
        task_row = _make_task_row()
        with pytest.raises(RuntimeError, match="Executor not started"):
            await executor_with_db._run_claude_task(task_row)

    @patch("backend.services.executor.calculate_cost", return_value=0.5)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_budget_exhausted_mid_loop(self, _mock_model, _mock_cost, executor_with_db):
        """When budget exhausted mid-loop, breaks with partial result."""
        executor_with_db._budget.can_spend = AsyncMock(return_value=False)

        tool_response = _make_tool_use_response("some_tool", {})
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=tool_response)
        executor_with_db._client = mock_client

        task_row = _make_task_row()
        # est_cost=0.01 but actual cost=0.5, so total_cost > est_cost triggers check
        result = await executor_with_db._run_claude_task(task_row, est_cost=0.01)

        # Should have only 1 API call (breaks after first round)
        assert mock_client.messages.create.await_count == 1

    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_context_injected_into_system_prompt(self, _mock_model, _mock_cost, executor_with_db):
        """Context from context_json appears in the system prompt."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_claude_response("Done"))
        executor_with_db._client = mock_client

        ctx = [{"type": "dependency_output", "content": "Previous task output: foo bar"}]
        task_row = _make_task_row(context_json=json.dumps(ctx))
        await executor_with_db._run_claude_task(task_row, est_cost=0.01)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "Previous task output: foo bar" in call_kwargs["system"]

    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_multi_round_token_accumulation(self, _mock_model, _mock_cost, executor_with_db):
        """Tokens from multiple rounds accumulate correctly."""
        tool_response = _make_tool_use_response("t", {}, pt=80, ct=40)
        text_response = _make_claude_response("Final", pt=120, ct=60)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[tool_response, text_response])
        executor_with_db._client = mock_client

        mock_tool = MagicMock()
        type(mock_tool).name = property(lambda self: "t")
        mock_tool.to_claude_tool.return_value = {"name": "t", "description": "t", "input_schema": {}}
        mock_tool.execute = AsyncMock(return_value="ok")
        executor_with_db._tool_registry.get_many = MagicMock(return_value=[mock_tool])

        task_row = _make_task_row(tools_json='["t"]')
        result = await executor_with_db._run_claude_task(task_row, est_cost=0.01)

        assert result["prompt_tokens"] == 200   # 80 + 120
        assert result["completion_tokens"] == 100  # 40 + 60


# ---------------------------------------------------------------------------
# TestRunOllamaTask
# ---------------------------------------------------------------------------

class TestRunOllamaTask:
    """Tests for Executor._run_ollama_task."""

    @patch("backend.services.executor.OLLAMA_HOSTS", {"local": "http://localhost:11434"})
    @patch("backend.services.executor.OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:14b")
    async def test_success_with_shared_client(self, executor_with_db):
        """Uses shared http client when available."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "Ollama output", "prompt_eval_count": 10, "eval_count": 20}
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        executor_with_db._http = mock_http

        task_row = _make_task_row(model_tier="ollama")
        result = await executor_with_db._run_ollama_task(task_row)

        assert result["output"] == "Ollama output"
        assert result["cost_usd"] == 0.0
        assert result["prompt_tokens"] == 10
        assert result["completion_tokens"] == 20
        mock_http.post.assert_awaited_once()

    @patch("backend.services.executor.OLLAMA_HOSTS", {"local": "http://localhost:11434"})
    @patch("backend.services.executor.OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:14b")
    @patch("backend.services.executor.OLLAMA_GENERATE_TIMEOUT", 30)
    async def test_success_without_shared_client(self, executor_with_db):
        """Creates ephemeral client when no shared client available."""
        executor_with_db._http = None

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "Ephemeral output", "prompt_eval_count": 5, "eval_count": 10}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.aclose = AsyncMock()

        with patch("backend.services.executor.httpx.AsyncClient", return_value=mock_client):
            task_row = _make_task_row(model_tier="ollama")
            result = await executor_with_db._run_ollama_task(task_row)

        assert result["output"] == "Ephemeral output"
        mock_client.aclose.assert_awaited_once()

    @patch("backend.services.executor.OLLAMA_HOSTS", {"local": "http://localhost:11434"})
    @patch("backend.services.executor.OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:14b")
    async def test_http_error_propagates(self, executor_with_db):
        """HTTP errors from raise_for_status propagate."""
        import httpx

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500))

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        executor_with_db._http = mock_http

        task_row = _make_task_row(model_tier="ollama")
        with pytest.raises(httpx.HTTPStatusError):
            await executor_with_db._run_ollama_task(task_row)

    @patch("backend.services.executor.OLLAMA_HOSTS", {"local": "http://localhost:11434"})
    @patch("backend.services.executor.OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:14b")
    async def test_record_spend_called(self, executor_with_db):
        """Budget record_spend called with provider=ollama, cost=0."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        executor_with_db._http = mock_http

        task_row = _make_task_row(model_tier="ollama")
        await executor_with_db._run_ollama_task(task_row)

        executor_with_db._budget.record_spend.assert_awaited_once()
        call_kwargs = executor_with_db._budget.record_spend.call_args.kwargs
        assert call_kwargs["cost_usd"] == 0.0
        assert call_kwargs["provider"] == "ollama"

    @patch("backend.services.executor.OLLAMA_HOSTS", {"local": "http://localhost:11434"})
    @patch("backend.services.executor.OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:14b")
    async def test_context_in_system_prompt(self, executor_with_db):
        """Context from context_json included in Ollama system prompt."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        executor_with_db._http = mock_http

        ctx = [{"type": "dependency_output", "content": "Previous: xyz"}]
        task_row = _make_task_row(model_tier="ollama", context_json=json.dumps(ctx))
        await executor_with_db._run_ollama_task(task_row)

        call_kwargs = mock_http.post.call_args.kwargs
        body = call_kwargs["json"]
        assert "Previous: xyz" in body["system"]


# ---------------------------------------------------------------------------
# TestExecuteTask
# ---------------------------------------------------------------------------

class TestExecuteTask:
    """Tests for Executor._execute_task (full task lifecycle)."""

    @patch("backend.services.executor.VERIFICATION_ENABLED", False)
    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_success_marks_completed(self, _mock_model, _mock_cost, tmp_db, executor_with_db):
        """Successful execution sets task status to completed."""
        await _seed_task(tmp_db)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_claude_response("All done"))
        executor_with_db._client = mock_client

        task_row = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", ("task_001",))
        await executor_with_db._execute_task(task_row, est_cost=0.01)

        row = await tmp_db.fetchone("SELECT status, output_text FROM tasks WHERE id = ?", ("task_001",))
        assert row["status"] == TaskStatus.COMPLETED
        assert row["output_text"] == "All done"

    @patch("backend.services.executor.VERIFICATION_ENABLED", False)
    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_fires_progress_events(self, _mock_model, _mock_cost, tmp_db, executor_with_db):
        """Progress events fired for task_start and task_complete."""
        await _seed_task(tmp_db)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_claude_response("Done"))
        executor_with_db._client = mock_client

        task_row = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", ("task_001",))
        await executor_with_db._execute_task(task_row, est_cost=0.01)

        event_types = [call.args[1] for call in executor_with_db._progress.push_event.call_args_list]
        assert "task_start" in event_types
        assert "task_complete" in event_types

    @patch("backend.services.executor.VERIFICATION_ENABLED", False)
    @patch("backend.services.executor.CHECKPOINT_ON_RETRY_EXHAUSTED", False)
    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_transient_error_retries(self, _mock_model, _mock_cost, tmp_db, executor_with_db):
        """Transient error with retries remaining sets task back to pending."""
        import anthropic as anth

        await _seed_task(tmp_db, retry_count=1, max_retries=5)

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_client.messages.create = AsyncMock(
            side_effect=anth.RateLimitError(
                message="rate limited",
                response=mock_resp,
                body=None,
            )
        )
        executor_with_db._client = mock_client

        task_row = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", ("task_001",))
        await executor_with_db._execute_task(task_row, est_cost=0.01)

        row = await tmp_db.fetchone("SELECT status, retry_count, error FROM tasks WHERE id = ?", ("task_001",))
        assert row["status"] == TaskStatus.PENDING
        assert row["retry_count"] == 2  # Was 1, incremented
        assert "Transient error" in row["error"]
        assert "task_001" in executor_with_db._retry_after

    @patch("backend.services.executor.VERIFICATION_ENABLED", False)
    @patch("backend.services.executor.CHECKPOINT_ON_RETRY_EXHAUSTED", True)
    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_max_retries_creates_checkpoint(self, _mock_model, _mock_cost, tmp_db, executor_with_db):
        """Max retries with checkpoint enabled creates checkpoint and sets NEEDS_REVIEW."""
        import anthropic as anth

        await _seed_task(tmp_db, retry_count=5, max_retries=5)

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_client.messages.create = AsyncMock(
            side_effect=anth.RateLimitError(
                message="rate limited",
                response=mock_resp,
                body=None,
            )
        )
        executor_with_db._client = mock_client

        task_row = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", ("task_001",))
        await executor_with_db._execute_task(task_row, est_cost=0.01)

        row = await tmp_db.fetchone("SELECT status FROM tasks WHERE id = ?", ("task_001",))
        assert row["status"] == TaskStatus.NEEDS_REVIEW

        cp = await tmp_db.fetchone("SELECT * FROM checkpoints WHERE task_id = ?", ("task_001",))
        assert cp is not None
        assert cp["checkpoint_type"] == "retry_exhausted"

    @patch("backend.services.executor.VERIFICATION_ENABLED", False)
    @patch("backend.services.executor.CHECKPOINT_ON_RETRY_EXHAUSTED", False)
    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_max_retries_no_checkpoint(self, _mock_model, _mock_cost, tmp_db, executor_with_db):
        """Max retries without checkpoint creates no checkpoint, marks FAILED."""
        import anthropic as anth

        await _seed_task(tmp_db, retry_count=5, max_retries=5)

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_client.messages.create = AsyncMock(
            side_effect=anth.RateLimitError(
                message="rate limited",
                response=mock_resp,
                body=None,
            )
        )
        executor_with_db._client = mock_client

        task_row = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", ("task_001",))
        await executor_with_db._execute_task(task_row, est_cost=0.01)

        row = await tmp_db.fetchone("SELECT status FROM tasks WHERE id = ?", ("task_001",))
        assert row["status"] == TaskStatus.FAILED

        cp = await tmp_db.fetchone("SELECT * FROM checkpoints WHERE task_id = ?", ("task_001",))
        assert cp is None

    @patch("backend.services.executor.VERIFICATION_ENABLED", False)
    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_generic_exception_marks_failed(self, _mock_model, _mock_cost, tmp_db, executor_with_db):
        """Non-transient exception marks task as failed."""
        await _seed_task(tmp_db)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=ValueError("Something broke"))
        executor_with_db._client = mock_client

        task_row = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", ("task_001",))
        await executor_with_db._execute_task(task_row, est_cost=0.01)

        row = await tmp_db.fetchone("SELECT status, error FROM tasks WHERE id = ?", ("task_001",))
        assert row["status"] == TaskStatus.FAILED
        assert "Something broke" in row["error"]

    @patch("backend.services.executor.VERIFICATION_ENABLED", False)
    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_budget_reservation_released_on_success(self, _mock_model, _mock_cost, tmp_db, executor_with_db):
        """Budget reservation released in finally block on success."""
        await _seed_task(tmp_db)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_claude_response("Done"))
        executor_with_db._client = mock_client

        task_row = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", ("task_001",))
        await executor_with_db._execute_task(task_row, est_cost=0.05)

        executor_with_db._budget.release_reservation.assert_awaited_once_with(0.05)

    @patch("backend.services.executor.VERIFICATION_ENABLED", False)
    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_budget_reservation_released_on_failure(self, _mock_model, _mock_cost, tmp_db, executor_with_db):
        """Budget reservation released even when task fails."""
        await _seed_task(tmp_db)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=ValueError("boom"))
        executor_with_db._client = mock_client

        task_row = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", ("task_001",))
        await executor_with_db._execute_task(task_row, est_cost=0.05)

        executor_with_db._budget.release_reservation.assert_awaited_once_with(0.05)

    @patch("backend.services.executor.VERIFICATION_ENABLED", False)
    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_dispatched_cleared_after_execution(self, _mock_model, _mock_cost, tmp_db, executor_with_db):
        """Task ID removed from _dispatched set after execution."""
        await _seed_task(tmp_db)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_claude_response("Done"))
        executor_with_db._client = mock_client

        executor_with_db._dispatched.add("task_001")

        task_row = await tmp_db.fetchone("SELECT * FROM tasks WHERE id = ?", ("task_001",))
        await executor_with_db._execute_task(task_row, est_cost=0.0)

        assert "task_001" not in executor_with_db._dispatched


# ---------------------------------------------------------------------------
# TestResourcesAvailable
# ---------------------------------------------------------------------------

class TestResourcesAvailable:
    """Tests for Executor._resources_available."""

    def test_ollama_offline_blocks_ollama_task(self, executor_with_db):
        executor_with_db._resource_monitor.is_available = MagicMock(return_value=False)
        task_row = _make_task_row(model_tier="ollama")
        assert executor_with_db._resources_available(task_row) is False

    def test_ollama_online_allows_ollama_task(self, executor_with_db):
        executor_with_db._resource_monitor.is_available = MagicMock(return_value=True)
        task_row = _make_task_row(model_tier="ollama")
        assert executor_with_db._resources_available(task_row) is True

    def test_anthropic_offline_blocks_claude_task(self, executor_with_db):
        executor_with_db._resource_monitor.is_available = MagicMock(return_value=False)
        task_row = _make_task_row(model_tier="haiku")
        assert executor_with_db._resources_available(task_row) is False

    def test_comfyui_offline_blocks_image_task(self, executor_with_db):
        def is_available(name):
            return name not in ("comfyui_local", "comfyui_server")
        executor_with_db._resource_monitor.is_available = MagicMock(side_effect=is_available)
        task_row = _make_task_row(model_tier="haiku", tools_json='["generate_image"]')
        assert executor_with_db._resources_available(task_row) is False

    def test_search_knowledge_needs_ollama(self, executor_with_db):
        def is_available(name):
            return name != "ollama_local"
        executor_with_db._resource_monitor.is_available = MagicMock(side_effect=is_available)
        task_row = _make_task_row(model_tier="haiku", tools_json='["search_knowledge"]')
        assert executor_with_db._resources_available(task_row) is False

    def test_lookup_type_ok_without_ollama(self, executor_with_db):
        def is_available(name):
            # anthropic is online but ollama is offline
            return name != "ollama_local"
        executor_with_db._resource_monitor.is_available = MagicMock(side_effect=is_available)
        task_row = _make_task_row(model_tier="haiku", tools_json='["lookup_type"]')
        assert executor_with_db._resources_available(task_row) is True


# ---------------------------------------------------------------------------
# TestTickDispatch
# ---------------------------------------------------------------------------

class TestTickDispatch:
    """Tests for Executor._tick dispatch logic."""

    @patch("backend.services.executor.VERIFICATION_ENABLED", False)
    @patch("backend.services.executor.WAVE_CHECKPOINTS", False)
    @patch("backend.services.executor.calculate_cost", return_value=0.001)
    @patch("backend.services.executor.get_model_id", return_value="claude-haiku-4-5-20251001")
    async def test_dispatches_pending_task(self, _mock_model, _mock_cost, tmp_db, executor_with_db):
        """Tick dispatches a pending wave-0 task."""
        await _seed_task(tmp_db, status="pending")

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_claude_response("Done"))
        executor_with_db._client = mock_client

        await executor_with_db._tick()

        # Give the dispatched task a moment to run
        await asyncio.sleep(0.2)

        # Task should have been claimed (queued then completed)
        row = await tmp_db.fetchone("SELECT status FROM tasks WHERE id = ?", ("task_001",))
        assert row["status"] in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.COMPLETED)

    @patch("backend.services.executor.WAVE_CHECKPOINTS", False)
    async def test_budget_exhausted_pauses_project(self, tmp_db, executor_with_db):
        """Tick pauses project when budget is exhausted."""
        executor_with_db._budget.can_spend = AsyncMock(return_value=False)

        await _seed_task(tmp_db, status="pending")

        await executor_with_db._tick()

        row = await tmp_db.fetchone("SELECT status FROM projects WHERE id = ?", ("proj_001",))
        assert row["status"] == ProjectStatus.PAUSED

    @patch("backend.services.executor.WAVE_CHECKPOINTS", False)
    async def test_all_completed_completes_project(self, tmp_db, executor_with_db):
        """Tick completes project when all tasks are in terminal state."""
        await _seed_task(tmp_db, status="completed")

        await executor_with_db._tick()

        row = await tmp_db.fetchone("SELECT status FROM projects WHERE id = ?", ("proj_001",))
        assert row["status"] == ProjectStatus.COMPLETED

    @patch("backend.services.executor.WAVE_CHECKPOINTS", False)
    async def test_all_blocked_fails_project(self, tmp_db, executor_with_db):
        """Tick fails project when all remaining tasks are blocked (dead project)."""
        # Create a failed task and a blocked task that depends on it.
        # _update_blocked_tasks won't unblock because the dependency is failed, not completed.
        await _seed_task(tmp_db, task_id="task_dep", status="failed")
        await _seed_task(tmp_db, task_id="task_blocked", status="blocked")
        await tmp_db.execute_write(
            "INSERT INTO task_deps (task_id, depends_on) VALUES (?, ?)",
            ("task_blocked", "task_dep"),
        )

        await executor_with_db._tick()

        row = await tmp_db.fetchone("SELECT status FROM projects WHERE id = ?", ("proj_001",))
        assert row["status"] == ProjectStatus.FAILED
