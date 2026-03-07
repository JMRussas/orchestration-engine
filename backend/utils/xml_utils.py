#  Orchestration Engine - XML Plan Utilities
#
#  Extraction and parsing of XML plans from LLM output.
#  Converts XML plan format to dicts matching the existing PlanData shape
#  so downstream code (decomposer, routes, frontend) stays unchanged.
#
#  Depends on: (none — stdlib only)
#  Used by:    services/planner.py, services/decomposer.py

import re
import xml.etree.ElementTree as ET


def extract_xml_plan(text: str) -> str | None:
    """Extract the <plan>...</plan> block from LLM response text.

    Handles markdown fences, preamble text, and <thinking> blocks.
    Returns the raw XML string (including the <plan> tags) or None.
    """
    # Strip markdown fences
    text = re.sub(r"```(?:xml)?\s*\n?", "", text)

    # Find <plan and </plan>
    start = text.find("<plan")
    if start == -1:
        return None

    end = text.find("</plan>")
    if end == -1:
        return None

    return text[start:end + len("</plan>")]


def _text(el: ET.Element | None) -> str:
    """Get text content of an element, defaulting to empty string."""
    if el is None:
        return ""
    return (el.text or "").strip()


def _split_csv(value: str) -> list[str]:
    """Split a comma-separated string, filtering empty values."""
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_task(task_el: ET.Element) -> dict:
    """Convert a <task> element to a dict matching the JSON task schema."""
    task = {
        "title": _text(task_el.find("title")),
        "description": _text(task_el.find("description")),
        "task_type": _text(task_el.find("task_type")) or "code",
        "complexity": _text(task_el.find("complexity")) or "medium",
        "tools_needed": _split_csv(_text(task_el.find("tools_needed"))),
        "requirement_ids": _split_csv(_text(task_el.find("requirement_ids"))),
        "verification_criteria": _text(task_el.find("verification_criteria")),
        "affected_files": _split_csv(_text(task_el.find("affected_files"))),
    }

    # depends_on: comma-separated integers
    deps_text = _text(task_el.find("depends_on"))
    if deps_text:
        task["depends_on"] = [int(d.strip()) for d in deps_text.split(",") if d.strip()]
    else:
        task["depends_on"] = []

    # C# specific fields (optional)
    for field in ("target_class", "target_signature"):
        val = _text(task_el.find(field))
        if val:
            task[field] = val

    # C# list fields
    for field in ("available_methods", "constructor_params"):
        val = _text(task_el.find(field))
        if val:
            task[field] = _split_csv(val)

    return task


def _parse_question(q_el: ET.Element) -> dict:
    """Convert a <question> element to a dict."""
    return {
        "question": _text(q_el.find("ask")),
        "proposed_answer": _text(q_el.find("proposed")),
        "impact": _text(q_el.find("impact")),
    }


def _parse_risk(r_el: ET.Element) -> dict:
    """Convert a <risk> element to a dict."""
    return {
        "risk": _text(r_el.find("description")),
        "likelihood": _text(r_el.find("likelihood")) or "medium",
        "impact": _text(r_el.find("impact")) or "medium",
        "mitigation": _text(r_el.find("mitigation")),
    }


def parse_plan_xml(xml_str: str) -> dict:
    """Parse an XML plan string into a dict matching the PlanData shape.

    Supports L1 (flat tasks), L2 (phased + questions), L3 (+ risks + test strategy),
    and C# reflection plans (+ target_class, target_signature, assembly_config).

    Returns a dict identical in structure to what the JSON planner produces,
    so downstream code (decomposer, routes, frontend) needs no changes.
    """
    root = ET.fromstring(xml_str)

    result: dict = {
        "summary": _text(root.find("summary")),
    }

    # L1: flat <tasks> container
    tasks_el = root.find("tasks")
    if tasks_el is not None:
        result["tasks"] = [_parse_task(t) for t in tasks_el.findall("task")]

    # L2+: <phases> container
    phases_el = root.find("phases")
    if phases_el is not None:
        phases = []
        for phase_el in phases_el.findall("phase"):
            phase = {
                "name": phase_el.get("name", ""),
                "description": _text(phase_el.find("description")),
                "tasks": [_parse_task(t) for t in phase_el.findall("task")],
            }
            phases.append(phase)
        result["phases"] = phases

    # L2+: <questions>
    questions_el = root.find("questions")
    if questions_el is not None:
        result["open_questions"] = [_parse_question(q) for q in questions_el.findall("question")]

    # L3: <risks>
    risks_el = root.find("risks")
    if risks_el is not None:
        result["risk_assessment"] = [_parse_risk(r) for r in risks_el.findall("risk")]

    # L3: <test_strategy>
    ts_el = root.find("test_strategy")
    if ts_el is not None:
        result["test_strategy"] = {
            "approach": _text(ts_el.find("approach")),
            "test_tasks": _split_csv(_text(ts_el.find("test_tasks"))),
            "coverage_notes": _text(ts_el.find("coverage_notes")),
        }

    # C#: <assembly_config>
    ac_el = root.find("assembly_config")
    if ac_el is not None:
        result["assembly_config"] = {
            "new_files": _split_csv(_text(ac_el.find("new_files"))),
            "modified_files": _split_csv(_text(ac_el.find("modified_files"))),
        }

    return result
