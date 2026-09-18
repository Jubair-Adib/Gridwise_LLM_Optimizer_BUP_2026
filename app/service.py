import logging
from typing import Any, Dict, List

from app.guardrails import validate_directives
from app.llm_interpreter import interpret_notes
from app.optimizer import solve_schedule
from app.replay import replay_and_validate

logger = logging.getLogger("gridwise")


def _fallback_no_op(note_count: int) -> List[Dict[str, Any]]:
    return [
        {
            "note_index": i,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "LLM interpretation unavailable; treated as no_op for safety.",
        }
        for i in range(note_count)
    ]


def _summarize(directives: List[Dict[str, Any]]) -> str:
    applied = [d["directive_type"] for d in directives if d.get("applies")]
    if not applied:
        return "No operator directives applied. Schedule optimized against the base scenario constraints."
    unique_types = ", ".join(sorted(set(applied)))
    return f"Applied {len(applied)} operator directive(s) ({unique_types}) and optimized the remaining schedule for minimum grid cost while restoring end-of-day battery neutrality."


def run_optimization(request: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = request["scenario_id"]
    operator_notes = request["operator_notes"]
    hours = request["hours"]
    battery = request["battery"]

    try:
        raw_directives = interpret_notes(operator_notes, battery["capacity_kwh"])
    except Exception as exc:
        logger.warning("LLM interpretation failed for %s: %s", scenario_id, exc)
        raw_directives = []

    directives = validate_directives(raw_directives, len(operator_notes))

    if len(raw_directives) == 0:
        directives = _fallback_no_op(len(operator_notes))

    plan = solve_schedule(hours, battery, directives)

    problems = replay_and_validate(hours, battery, directives, plan["hourly_plan"])
    if problems:
        logger.warning("Replay validation flagged issues for %s: %s", scenario_id, problems)

    return {
        "scenario_id": scenario_id,
        "directive_interpretation": directives,
        "hourly_plan": plan["hourly_plan"],
        "total_grid_kwh": plan["total_grid_kwh"],
        "total_cost_bdt": plan["total_cost_bdt"],
        "peak_grid_kwh": plan["peak_grid_kwh"],
        "plan_summary": _summarize(directives),
    }
