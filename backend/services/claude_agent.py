#  Orchestration Engine - Claude Agent
#
#  Runs a single task via the Claude API with multi-turn tool support.
#  Extracted from executor.py for modularity.
#
#  Depends on: config.py, services/budget.py, services/model_router.py, tools/registry.py
#  Used by:    services/task_lifecycle.py

import json
import logging

from backend.config import API_TIMEOUT, MAX_TOOL_ROUNDS
from backend.models.enums import ModelTier
from backend.services.model_router import calculate_cost, get_model_id

logger = logging.getLogger("orchestration.executor")


async def run_claude_task(
    *,
    task_row,
    est_cost: float = 0.0,
    client,
    tool_registry,
    budget,
    progress,
) -> dict:
    """Execute a task via the Claude API with tool support.

    Args:
        task_row: Task database row.
        est_cost: The original reserved cost estimate. Used for mid-loop
            budget checks — if actual spend exceeds the estimate, we verify
            the global budget hasn't been exhausted before continuing.
        client: anthropic.AsyncAnthropic instance.
        tool_registry: ToolRegistry for tool definitions and execution.
        budget: BudgetManager instance.
        progress: ProgressManager instance.
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
    tools = tool_registry.get_many(tool_names)
    tool_defs = [t.to_claude_tool() for t in tools]
    tool_map = {t.name: t for t in tools}

    # Initial message
    messages = [{"role": "user", "content": task_row["description"]}]

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

        await budget.record_spend(
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
        if total_cost > est_cost and not await budget.can_spend(0.001):
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

                await progress.push_event(
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
