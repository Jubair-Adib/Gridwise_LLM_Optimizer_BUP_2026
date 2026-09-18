from typing import Any, Dict, List

ALLOWED_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}

REQUIRED_ADJUSTMENT_KEYS = {
    "solar_reduction": {"hours", "factor"},
    "minimum_battery_reserve": {"hours", "minimum_energy_kwh"},
    "no_charge_window": {"hours"},
    "no_discharge_window": {"hours"},
    "max_grid_window": {"hours", "max_grid_kwh"},
}


def _safe_no_op(note_index: int, reason: str) -> Dict[str, Any]:
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": f"Rejected by guardrails: {reason}",
    }


def _validate_hours(hours: Any) -> bool:
    if not isinstance(hours, list) or len(hours) == 0:
        return False
    if not all(isinstance(h, int) and not isinstance(h, bool) for h in hours):
        return False
    if not all(0 <= h <= 23 for h in hours):
        return False
    if len(set(hours)) != len(hours):
        return False
    return hours == sorted(hours)


def _validate_entry(entry: Dict[str, Any], note_count: int) -> Dict[str, Any]:
    note_index = entry.get("note_index")
    if not isinstance(note_index, int) or not (0 <= note_index < note_count):
        return None

    directive_type = entry.get("directive_type")
    if directive_type not in ALLOWED_DIRECTIVE_TYPES:
        return _safe_no_op(note_index, "unsupported directive_type")

    applies = entry.get("applies")
    explanation = entry.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        explanation = "No explanation provided."

    if directive_type == "no_op":
        if applies is not False:
            return _safe_no_op(note_index, "no_op must use applies=false")
        return {
            "note_index": note_index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": explanation,
        }

    if applies is not True:
        return _safe_no_op(note_index, "non no_op directive must use applies=true")

    adjustment = entry.get("structured_adjustment")
    if not isinstance(adjustment, dict):
        return _safe_no_op(note_index, "missing structured_adjustment")

    required_keys = REQUIRED_ADJUSTMENT_KEYS[directive_type]
    if not required_keys.issubset(adjustment.keys()):
        return _safe_no_op(note_index, "structured_adjustment missing required keys")

    hours = adjustment.get("hours")
    if not _validate_hours(hours):
        return _safe_no_op(note_index, "invalid hours array")

    if directive_type == "solar_reduction":
        factor = adjustment.get("factor")
        if not isinstance(factor, (int, float)) or isinstance(factor, bool):
            return _safe_no_op(note_index, "invalid factor")
        if not (0.0 <= float(factor) <= 1.0):
            return _safe_no_op(note_index, "factor out of range")
        clean_adjustment = {"hours": hours, "factor": float(factor)}

    elif directive_type == "minimum_battery_reserve":
        value = adjustment.get("minimum_energy_kwh")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return _safe_no_op(note_index, "invalid minimum_energy_kwh")
        clean_adjustment = {"hours": hours, "minimum_energy_kwh": float(value)}

    elif directive_type == "max_grid_window":
        value = adjustment.get("max_grid_kwh")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return _safe_no_op(note_index, "invalid max_grid_kwh")
        clean_adjustment = {"hours": hours, "max_grid_kwh": float(value)}

    else:
        clean_adjustment = {"hours": hours}

    return {
        "note_index": note_index,
        "applies": True,
        "directive_type": directive_type,
        "structured_adjustment": clean_adjustment,
        "explanation": explanation,
    }


def validate_directives(raw_entries: Any, note_count: int) -> List[Dict[str, Any]]:
    result_by_index: Dict[int, Dict[str, Any]] = {}

    if isinstance(raw_entries, list):
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            validated = _validate_entry(entry, note_count)
            if validated is None:
                continue
            idx = validated["note_index"]
            if idx not in result_by_index:
                result_by_index[idx] = validated

    final: List[Dict[str, Any]] = []
    for i in range(note_count):
        if i in result_by_index:
            final.append(result_by_index[i])
        else:
            final.append(_safe_no_op(i, "missing or duplicate interpretation entry"))
    return final
