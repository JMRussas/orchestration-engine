#  Orchestration Engine - Planner
#
#  Uses Claude to generate structured plans from requirements.
#
#  Depends on: backend/config.py, services/model_router.py
#  Used by:    routes/projects.py, container.py

import json
import time
import uuid

import anthropic

from backend.config import ANTHROPIC_API_KEY, API_TIMEOUT, PLANNING_MODEL
from backend.exceptions import BudgetExhaustedError, NotFoundError, PlanParseError
from backend.models.enums import PlanningRigor, PlanStatus, ProjectStatus
from backend.services.model_router import calculate_cost

# Token estimates for budget reservation before API calls
_EST_PLANNING_INPUT_TOKENS = 2000   # system prompt (~1.5k) + requirements
_EST_PLANNING_OUTPUT_TOKENS = 2000  # plan JSON response

# Max output tokens by rigor level (more structured output needs more tokens)
_MAX_TOKENS_BY_RIGOR = {
    PlanningRigor.L1: 4096,
    PlanningRigor.L2: 6144,
    PlanningRigor.L3: 8192,
}


def _extract_json_object(text: str) -> dict | None:
    """Extract the first balanced JSON object from text.

    Uses brace-counting instead of a greedy regex to avoid capturing
    past the actual closing brace when Claude wraps JSON in explanation.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ---------------------------------------------------------------------------
# Planner system prompt: preamble (shared) + rigor-specific suffix
# ---------------------------------------------------------------------------

_PLANNING_PREAMBLE = """You are a project planner for an AI orchestration engine. Your job is to analyze requirements and produce a structured execution plan.

Requirements are numbered [R1], [R2], etc. for traceability.

Task guidelines:
- Break work into small, focused tasks. Each task should be completable in a single AI conversation.
- Keep task descriptions self-contained — include enough context for a fresh AI instance.
- Use "depends_on" to reference task indices (0-based) for ordering dependencies.
- Prefer simple tasks when possible — they use cheaper models.
- Use task_type "research" for information gathering that can run on a free local model.
- Use task_type "analysis" for summarization/comparison that can run locally.
- Use task_type "asset" for image/visual generation (uses ComfyUI).
- Use task_type "code" for writing code or technical implementation.
- Use task_type "integration" for combining outputs from other tasks.
- Use task_type "documentation" for writing docs, READMEs, etc.
- Order tasks so independent work can run in parallel.
- Map each task to the requirement IDs it satisfies using requirement_ids.
- Include verification_criteria: a concrete check to confirm task completion.
- Include affected_files: list of files this task will create or modify (best guess).

Available tools each task can request:
- search_knowledge: Semantic search across code and documentation RAG databases
- lookup_type: Exact keyword/type name lookup in RAG databases
- local_llm: Free local LLM for drafts, summaries, sub-tasks
- generate_image: Queue image generation via ComfyUI
- read_file: Read files from the project workspace
- write_file: Write files to the project workspace

"""

_TASK_SCHEMA = """{
      "title": "Short task title",
      "description": "Detailed description...",
      "task_type": "code|research|analysis|asset|integration|documentation",
      "complexity": "simple|medium|complex",
      "depends_on": [],
      "tools_needed": ["search_knowledge", "lookup_type", "local_llm", "generate_image", "read_file", "write_file"],
      "requirement_ids": ["R1", "R3"],
      "verification_criteria": "How to verify this task was completed correctly",
      "affected_files": ["src/auth.ts", "db/schema.sql"]
    }"""

_RIGOR_SUFFIX_L1 = f"""Produce a JSON plan with this exact structure:
{{
  "summary": "Brief summary of what will be built",
  "tasks": [
    {_TASK_SCHEMA}
  ]
}}

- Aim for 3-15 tasks. Too few means tasks are too large; too many means overhead.

Respond with ONLY the JSON plan, no markdown fences or explanation."""

_RIGOR_SUFFIX_L2 = f"""Produce a JSON plan organized into phases. Each phase groups related tasks into a logical stage of work.

{{
  "summary": "Brief summary of what will be built",
  "phases": [
    {{
      "name": "Phase name (e.g. 'Foundation', 'Core Logic', 'Integration')",
      "description": "What this phase accomplishes and why it comes at this point",
      "tasks": [
        {_TASK_SCHEMA}
      ]
    }}
  ],
  "open_questions": [
    {{
      "question": "An ambiguity or decision in the requirements",
      "proposed_answer": "How you propose to handle it",
      "impact": "What changes if the answer differs"
    }}
  ]
}}

Phase guidelines:
- Group related tasks into 2-5 phases that represent logical stages of work.
- Name phases clearly: "Research & Discovery", "Core Implementation", "Integration & Testing", etc.
- Earlier phases should have no dependencies on later phases.
- depends_on indices are GLOBAL across all phases (0-based from the first task in the first phase).
- Aim for 3-15 total tasks across all phases.

Open questions:
- Surface 1-5 ambiguities, assumptions, or decisions that could affect the plan.
- Each must include a proposed_answer so the user can approve or override quickly.

Respond with ONLY the JSON plan, no markdown fences or explanation."""

_RIGOR_SUFFIX_L3 = f"""Produce a thorough JSON plan organized into phases with risk analysis and test strategy.

{{
  "summary": "Brief summary of what will be built",
  "phases": [
    {{
      "name": "Phase name (e.g. 'Foundation', 'Core Logic', 'Integration')",
      "description": "What this phase accomplishes and why it comes at this point",
      "tasks": [
        {_TASK_SCHEMA}
      ]
    }}
  ],
  "open_questions": [
    {{
      "question": "An ambiguity or decision in the requirements",
      "proposed_answer": "How you propose to handle it",
      "impact": "What changes if the answer differs"
    }}
  ],
  "risk_assessment": [
    {{
      "risk": "Description of a technical or schedule risk",
      "likelihood": "low|medium|high",
      "impact": "low|medium|high",
      "mitigation": "How to reduce or handle this risk"
    }}
  ],
  "test_strategy": {{
    "approach": "Overall testing approach description",
    "test_tasks": ["Task titles that represent test/verification work"],
    "coverage_notes": "What areas need testing and how"
  }}
}}

Phase guidelines:
- Group related tasks into 2-5 phases that represent logical stages of work.
- Name phases clearly: "Research & Discovery", "Core Implementation", "Integration & Testing", etc.
- Earlier phases should have no dependencies on later phases.
- depends_on indices are GLOBAL across all phases (0-based from the first task in the first phase).
- Aim for 5-15 total tasks across all phases.

Open questions:
- Surface 1-5 ambiguities, assumptions, or decisions that could affect the plan.
- Each must include a proposed_answer so the user can approve or override quickly.

Risk assessment:
- Identify 2-5 technical, integration, or scope risks.
- Be concrete — reference specific requirements or tasks.

Test strategy:
- Describe the overall approach to verifying the work.
- Reference specific tasks that perform testing/verification.
- Note coverage gaps the user should be aware of.

Respond with ONLY the JSON plan, no markdown fences or explanation."""

_RIGOR_SUFFIXES = {
    PlanningRigor.L1: _RIGOR_SUFFIX_L1,
    PlanningRigor.L2: _RIGOR_SUFFIX_L2,
    PlanningRigor.L3: _RIGOR_SUFFIX_L3,
}


def _build_system_prompt(rigor: PlanningRigor) -> str:
    """Build the full system prompt for the given planning rigor level."""
    return _PLANNING_PREAMBLE + _RIGOR_SUFFIXES[rigor]


class PlannerService:
    """Injectable service that generates plans from project requirements."""

    def __init__(self, *, db, budget):
        self._db = db
        self._budget = budget

    async def generate(
        self,
        project_id: str,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> dict:
        """Generate a structured plan for a project using Claude.

        Args:
            project_id: The project to plan for.
            client: Optional shared Anthropic client. If None, creates (and closes) one.

        Returns the plan dict and updates the database.
        """
        db = self._db
        budget = self._budget

        # Get project
        row = await db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not row:
            raise NotFoundError(f"Project {project_id} not found")

        requirements = row["requirements"]
        project_name = row["name"]

        # Read planning rigor from project config
        config = json.loads(row["config_json"]) if row["config_json"] else {}
        rigor_str = config.get("planning_rigor", "L2")
        try:
            rigor = PlanningRigor(rigor_str)
        except ValueError:
            rigor = PlanningRigor.L2

        system_prompt = _build_system_prompt(rigor)
        max_tokens = _MAX_TOKENS_BY_RIGOR[rigor]

        # Reserve budget before making the API call (prevents TOCTOU race)
        estimated_cost = calculate_cost(PLANNING_MODEL, _EST_PLANNING_INPUT_TOKENS, _EST_PLANNING_OUTPUT_TOKENS)
        if not await budget.reserve_spend(estimated_cost):
            raise BudgetExhaustedError("Budget limit reached. Cannot generate plan.")

        # Update project status
        await db.execute_write(
            "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
            (ProjectStatus.PLANNING, time.time(), project_id),
        )

        # Use provided client or create a temporary one
        owns_client = client is None
        if owns_client:
            client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

        # Number requirements for traceability
        req_lines = [line for line in requirements.strip().split("\n") if line.strip()]
        if req_lines:
            numbered = "\n".join(f"[R{i+1}] {line}" for i, line in enumerate(req_lines))
        else:
            numbered = requirements
        user_msg = f"Project: {project_name}\n\nRequirements:\n{numbered}"

        try:
            response = await client.messages.create(
                model=PLANNING_MODEL,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}],
                timeout=API_TIMEOUT,
            )

            # Extract text and tokens
            if not response.content:
                raise PlanParseError("Claude returned an empty response")

            response_text = response.content[0].text
            prompt_tokens = response.usage.input_tokens
            completion_tokens = response.usage.output_tokens
            cost = calculate_cost(PLANNING_MODEL, prompt_tokens, completion_tokens)

            # Parse the plan JSON
            try:
                plan_data = json.loads(response_text)
            except json.JSONDecodeError:
                # Try to extract JSON from the response (in case of markdown fences).
                # Use a balanced-brace approach to find the outermost JSON object,
                # instead of a greedy regex that could match too much.
                plan_data = _extract_json_object(response_text)
                if plan_data is None:
                    raise PlanParseError("Failed to parse plan JSON from Claude response")

        except Exception:
            # Reset project status so it's not stuck in PLANNING
            await db.execute_write(
                "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                (ProjectStatus.DRAFT, time.time(), project_id),
            )
            await budget.release_reservation(estimated_cost)
            raise
        finally:
            if owns_client:
                await client.close()

        # Determine plan version
        version_row = await db.fetchone(
            "SELECT COALESCE(MAX(version), 0) as v FROM plans WHERE project_id = ?",
            (project_id,),
        )
        version = (version_row["v"] if version_row else 0) + 1

        # Supersede any previous draft plans
        await db.execute_write(
            "UPDATE plans SET status = ? WHERE project_id = ? AND status = ?",
            (PlanStatus.SUPERSEDED, project_id, PlanStatus.DRAFT),
        )

        # Store the plan
        plan_id = uuid.uuid4().hex[:12]
        now = time.time()
        await db.execute_write(
            "INSERT INTO plans (id, project_id, version, model_used, prompt_tokens, "
            "completion_tokens, cost_usd, plan_json, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (plan_id, project_id, version, PLANNING_MODEL, prompt_tokens,
             completion_tokens, cost, json.dumps(plan_data), PlanStatus.DRAFT, now),
        )

        # Record spending and release reservation
        await budget.record_spend(
            cost_usd=cost,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            provider="anthropic",
            model=PLANNING_MODEL,
            purpose="planning",
            project_id=project_id,
        )
        await budget.release_reservation(estimated_cost)

        # Update project status back to draft (awaiting approval)
        await db.execute_write(
            "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
            (ProjectStatus.DRAFT, time.time(), project_id),
        )

        return {
            "plan_id": plan_id,
            "version": version,
            "plan": plan_data,
            "model_used": PLANNING_MODEL,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost,
        }


async def generate_plan(
    project_id: str,
    *,
    db,
    budget,
    client: anthropic.AsyncAnthropic | None = None,
) -> dict:
    """Convenience wrapper for backward compatibility with tests and direct callers."""
    return await PlannerService(db=db, budget=budget).generate(project_id, client=client)
