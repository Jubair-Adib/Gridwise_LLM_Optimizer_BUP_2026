import json
import os
import time
from typing import Any, List

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

MODEL_NAME = os.environ.get("LLM_MODEL", "gemini-2.0-flash")
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.6

SYSTEM_PROMPT = """You are the operator-note interpretation module of GridWise, a campus microgrid scheduler.

You convert short natural-language operator notes into a strict JSON array of machine-checkable directives that a downstream deterministic optimizer consumes. You never write energy schedules yourself.

Supported directive_type values, and the exact structured_adjustment shape each one requires:
- solar_reduction: {"hours": [int...], "factor": number} where factor is the usable fraction of solar that REMAINS (an 80 percent reduction means factor = 0.2).
- minimum_battery_reserve: {"hours": [int...], "minimum_energy_kwh": number}
- no_charge_window: {"hours": [int...]}
- no_discharge_window: {"hours": [int...]}
- max_grid_window: {"hours": [int...], "max_grid_kwh": number}
- no_op: structured_adjustment must be null

Rules:
1. Produce exactly one entry per operator note, in note_index order (0 to N-1), no missing or duplicate indices.
2. A note that does not affect the current 24-hour energy schedule must use applies=false, directive_type="no_op", structured_adjustment=null. Every other directive must use applies=true.
3. Time windows are whole hours, start-inclusive and end-exclusive. "1 PM to 3 PM" means hours [13, 14]. "6 PM until 9 PM" means hours [18, 19, 20].
4. Hours arrays must contain unique integers from 0 to 23 in ascending order.
5. Convert relative or percentage language into absolute numbers using only values explicitly given in the note or the provided battery capacity. Never invent demand, tariff, or battery limits that were not given to you.
6. Do not hard-code specific wording. The same directive can be phrased many different ways; extract the underlying meaning.
7. Output ONLY a raw JSON object of the exact shape below, no markdown fences, no commentary.

Output shape:
{"directive_interpretation": [{"note_index": 0, "applies": true, "directive_type": "solar_reduction", "structured_adjustment": {"hours": [13, 14], "factor": 0.2}, "explanation": "short reason"}]}
"""

FEW_SHOT_EXAMPLES = [
    {
        "note": "Solar output will drop to about 20% from 1 PM to 3 PM.",
        "interpretation": {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
            "explanation": "Solar availability is reduced to 20 percent during the stated window.",
        },
    },
    {
        "note": "Keep at least 50% of the battery capacity stored from 6 PM until 9 PM.",
        "interpretation": {
            "note_index": 0,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [18, 19, 20], "minimum_energy_kwh": 100.0},
            "explanation": "50 percent of the stated battery capacity converted to an absolute kWh reserve.",
        },
    },
    {
        "note": "The cafeteria menu changes tomorrow.",
        "interpretation": {
            "note_index": 0,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "This note has no effect on the current 24-hour energy schedule.",
        },
    },
]


def _build_user_message(operator_notes: List[str], battery_capacity_kwh: float) -> str:
    notes_block = "\n".join(f"{i}: {note}" for i, note in enumerate(operator_notes))
    examples_block = json.dumps(FEW_SHOT_EXAMPLES, indent=2)
    return (
        f"Battery capacity for this scenario: {battery_capacity_kwh} kWh\n\n"
        f"Operator notes:\n{notes_block}\n\n"
        f"Reference examples of correct interpretation style (indices in these examples do not apply to this request):\n{examples_block}\n\n"
        "Return the directive_interpretation JSON object for the operator notes above."
    )


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start : end + 1])


def _call_with_retries(client: genai.Client, contents: str, use_json_mode: bool):
    config_kwargs = dict(
        system_instruction=SYSTEM_PROMPT,
        temperature=0,
        max_output_tokens=2000,
    )
    if use_json_mode:
        config_kwargs["response_mime_type"] = "application/json"

    config = types.GenerateContentConfig(**config_kwargs)

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return client.models.generate_content(model=MODEL_NAME, contents=contents, config=config)
        except (ClientError, ServerError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise last_error


def interpret_notes(operator_notes: List[str], battery_capacity_kwh: float) -> Any:
    client = genai.Client()
    user_message = _build_user_message(operator_notes, battery_capacity_kwh)

    try:
        response = _call_with_retries(client, user_message, use_json_mode=True)
    except Exception:
        response = _call_with_retries(client, user_message, use_json_mode=False)

    raw_text = response.text or ""
    parsed = _extract_json(raw_text)
    return parsed.get("directive_interpretation", [])
