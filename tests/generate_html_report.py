import json
import os
import sys
import webbrowser

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
SAMPLES_PATH = os.path.join(os.path.dirname(__file__), "public_sample_cases.json")
REPORT_PATH = os.path.join(os.path.dirname(__file__), "report.html")


def run_case(client: httpx.Client, case: dict) -> dict:
    case_id = case["id"]
    payload = case["input"]
    reference_cost = case["expected_output"]["total_cost_bdt"]

    try:
        response = client.post("/optimize-energy", json=payload, timeout=30.0)
    except Exception as exc:
        return {"id": case_id, "status": "ERROR", "detail": str(exc), "cost": None, "reference": reference_cost, "delta": None}

    if response.status_code != 200:
        return {"id": case_id, "status": "FAIL", "detail": f"HTTP {response.status_code}: {response.text[:200]}", "cost": None, "reference": reference_cost, "delta": None}

    body = response.json()
    required_top = {"scenario_id", "directive_interpretation", "hourly_plan", "total_grid_kwh", "total_cost_bdt", "peak_grid_kwh", "plan_summary"}
    missing = required_top - body.keys()
    if missing:
        return {"id": case_id, "status": "FAIL", "detail": f"missing fields {missing}", "cost": None, "reference": reference_cost, "delta": None}

    if len(body["directive_interpretation"]) != len(payload["operator_notes"]):
        return {"id": case_id, "status": "FAIL", "detail": "directive_interpretation length mismatch", "cost": None, "reference": reference_cost, "delta": None}

    if len(body["hourly_plan"]) != 24:
        return {"id": case_id, "status": "FAIL", "detail": "hourly_plan does not have 24 entries", "cost": None, "reference": reference_cost, "delta": None}

    recalculated_grid = sum(e["grid_kwh"] for e in body["hourly_plan"])
    recalculated_cost = sum(e["grid_kwh"] * h["tariff_bdt_per_kwh"] for e, h in zip(body["hourly_plan"], payload["hours"]))

    if abs(recalculated_grid - body["total_grid_kwh"]) > 0.5 or abs(recalculated_cost - body["total_cost_bdt"]) > 0.5:
        return {"id": case_id, "status": "FAIL", "detail": "reported totals do not match recalculated hourly_plan", "cost": body["total_cost_bdt"], "reference": reference_cost, "delta": None}

    cost = body["total_cost_bdt"]
    delta = (cost - reference_cost) / reference_cost * 100
    return {"id": case_id, "status": "PASS", "detail": body["plan_summary"], "cost": cost, "reference": reference_cost, "delta": delta}


def render_html(health_ok: bool, results: list) -> str:
    passed = sum(1 for r in results if r["status"] == "PASS")
    total = len(results)

    rows = ""
    for r in results:
        status_class = "pass" if r["status"] == "PASS" else "fail"
        cost_cell = f"{r['cost']:.2f}" if r["cost"] is not None else "-"
        ref_cell = f"{r['reference']:.2f}" if r["reference"] is not None else "-"
        delta_cell = f"{r['delta']:.2f}%" if r["delta"] is not None else "-"
        delta_class = "delta-good" if (r["delta"] is not None and r["delta"] < 2) else ("delta-warn" if (r["delta"] is not None and r["delta"] < 10) else "delta-bad" if r["delta"] is not None else "")
        rows += f"""
        <tr>
          <td class="mono">{r['id']}</td>
          <td><span class="badge {status_class}">{r['status']}</span></td>
          <td class="mono">{cost_cell}</td>
          <td class="mono">{ref_cell}</td>
          <td class="mono {delta_class}">{delta_cell}</td>
          <td class="detail">{r['detail']}</td>
        </tr>"""

    health_badge = '<span class="badge pass">OK</span>' if health_ok else '<span class="badge fail">DOWN</span>'
    pass_rate_class = "delta-good" if passed == total else ("delta-warn" if passed >= total * 0.7 else "delta-bad")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>GridWise Test Report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; background:#0d1321; color:#f6f7f4; margin:0; padding:48px; }}
  h1 {{ font-size:32px; margin:0 0 8px; }}
  .sub {{ color:#8b929a; margin:0 0 32px; }}
  .summary {{ display:flex; gap:24px; margin-bottom:32px; }}
  .card {{ background:#161d2c; border:1px solid #2a3244; border-radius:12px; padding:24px 32px; flex:1; }}
  .card .big {{ font-size:40px; font-weight:700; }}
  .card .label {{ color:#8b929a; font-size:14px; margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; background:#161d2c; border-radius:12px; overflow:hidden; }}
  th, td {{ padding:14px 16px; text-align:left; border-bottom:1px solid #2a3244; font-size:15px; }}
  th {{ background:#1a2233; color:#c7cdd3; font-weight:600; }}
  .mono {{ font-family: Consolas, Menlo, monospace; }}
  .detail {{ color:#c7cdd3; max-width:420px; }}
  .badge {{ padding:4px 12px; border-radius:999px; font-size:13px; font-weight:600; }}
  .badge.pass {{ background:#12351f; color:#7fd9a8; }}
  .badge.fail {{ background:#3a1414; color:#f2a3a3; }}
  .delta-good {{ color:#7fd9a8; }}
  .delta-warn {{ color:#e08e2b; }}
  .delta-bad {{ color:#f2635e; }}
</style>
</head>
<body>
  <h1>GridWise LLM Optimizer &mdash; Test Report</h1>
  <p class="sub">Public sample cases run against {BASE_URL}</p>
  <div class="summary">
    <div class="card">
      <div class="big">{health_badge}</div>
      <div class="label">/health status</div>
    </div>
    <div class="card">
      <div class="big {pass_rate_class}">{passed}/{total}</div>
      <div class="label">structural checks passed</div>
    </div>
  </div>
  <table>
    <tr><th>Case</th><th>Status</th><th>Cost (BDT)</th><th>Reference (BDT)</th><th>Delta</th><th>Detail</th></tr>
    {rows}
  </table>
</body>
</html>"""


def main() -> None:
    with open(SAMPLES_PATH, "r") as f:
        data = json.load(f)

    with httpx.Client(base_url=BASE_URL) as client:
        try:
            health_response = client.get("/health")
            health_ok = health_response.status_code == 200 and health_response.json().get("status") == "ok"
        except Exception:
            health_ok = False

        results = [run_case(client, case) for case in data["cases"]]

    html = render_html(health_ok, results)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Report written to {REPORT_PATH}")
    webbrowser.open(f"file://{os.path.abspath(REPORT_PATH)}")


if __name__ == "__main__":
    main()
