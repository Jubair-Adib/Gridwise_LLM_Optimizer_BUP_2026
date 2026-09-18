import json
import os
import sys

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
SAMPLES_PATH = os.path.join(os.path.dirname(__file__), "public_sample_cases.json")


def check_health(client: httpx.Client) -> bool:
    response = client.get("/health")
    ok = response.status_code == 200 and response.json().get("status") == "ok"
    print(f"[health] {'PASS' if ok else 'FAIL'} -> {response.status_code} {response.text}")
    return ok


def check_case(client: httpx.Client, case: dict) -> bool:
    case_id = case["id"]
    payload = case["input"]
    response = client.post("/optimize-energy", json=payload, timeout=30.0)

    if response.status_code != 200:
        print(f"[{case_id}] FAIL -> status {response.status_code}: {response.text[:300]}")
        return False

    body = response.json()
    required_top = {"scenario_id", "directive_interpretation", "hourly_plan", "total_grid_kwh", "total_cost_bdt", "peak_grid_kwh", "plan_summary"}
    missing = required_top - body.keys()
    if missing:
        print(f"[{case_id}] FAIL -> missing top-level fields {missing}")
        return False

    if len(body["directive_interpretation"]) != len(payload["operator_notes"]):
        print(f"[{case_id}] FAIL -> directive_interpretation length mismatch")
        return False

    if len(body["hourly_plan"]) != 24:
        print(f"[{case_id}] FAIL -> hourly_plan does not have 24 entries")
        return False

    recalculated_grid = sum(e["grid_kwh"] for e in body["hourly_plan"])
    recalculated_cost = sum(e["grid_kwh"] * h["tariff_bdt_per_kwh"] for e, h in zip(body["hourly_plan"], payload["hours"]))

    if abs(recalculated_grid - body["total_grid_kwh"]) > 0.5:
        print(f"[{case_id}] FAIL -> total_grid_kwh mismatch")
        return False

    if abs(recalculated_cost - body["total_cost_bdt"]) > 0.5:
        print(f"[{case_id}] FAIL -> total_cost_bdt mismatch")
        return False

    reference_cost = case["expected_output"]["total_cost_bdt"]
    delta_pct = abs(body["total_cost_bdt"] - reference_cost) / reference_cost * 100
    print(f"[{case_id}] PASS -> cost {body['total_cost_bdt']:.2f} BDT (reference {reference_cost:.2f}, delta {delta_pct:.2f}%)")
    return True


def main() -> None:
    with open(SAMPLES_PATH, "r") as f:
        data = json.load(f)

    with httpx.Client(base_url=BASE_URL) as client:
        if not check_health(client):
            sys.exit(1)

        results = [check_case(client, case) for case in data["cases"]]

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} public sample cases passed structural checks.")
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
