import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm_interpreter import interpret_notes
from app.guardrails import validate_directives

SAMPLES_PATH = "tests/public_sample_cases.json"


def show_case(case: dict) -> None:
    notes = case["input"]["operator_notes"]
    capacity = case["input"]["battery"]["capacity_kwh"]
    expected = case["expected_output"]["directive_interpretation"]

    raw = interpret_notes(notes, capacity)
    validated = validate_directives(raw, len(notes))

    print(f"\n=== {case['id']} ===")
    for i, note in enumerate(notes):
        print(f"\nNote {i}: {note}")
        exp = next((e for e in expected if e["note_index"] == i), None)
        got = next((v for v in validated if v["note_index"] == i), None)
        print(f"  expected: {json.dumps(exp, sort_keys=True)}")
        print(f"  got:      {json.dumps(got, sort_keys=True)}")
        if exp and got:
            mismatch = (exp["directive_type"] != got["directive_type"]) or (
                exp.get("structured_adjustment") != got.get("structured_adjustment")
            )
            print(f"  {'MISMATCH' if mismatch else 'match'}")


def main() -> None:
    with open(SAMPLES_PATH, "r") as f:
        data = json.load(f)

    case_ids = sys.argv[1:] or [c["id"] for c in data["cases"]]
    for case in data["cases"]:
        if case["id"] in case_ids:
            show_case(case)


if __name__ == "__main__":
    main()
