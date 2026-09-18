# GridWise LLM Optimizer

Smart Campus Energy Optimization Challenge — BUP CSE Fest 2026 Hackathon, Online Preliminary Round.

## Architecture

```
Energy Data + Operator Notes -> LLM Interpreter -> Guardrail Validator -> Math Optimizer -> Final Replay Validator -> API Response
```

- **LLM Interpreter** (`app/llm_interpreter.py`): sends the operator notes to a Google Gemini model with a strict system prompt and few-shot examples, and asks for one `directive_interpretation` entry per note, in order.
- **Guardrail Validator** (`app/guardrails.py`): treats the LLM output as untrusted data. It checks directive types, note-index coverage, hour ranges, and required numeric shapes, and silently downgrades anything malformed to a safe `no_op` rather than trusting or crashing on it.
- **Math Optimizer** (`app/optimizer.py`): a linear program (SciPy HiGHS solver) over 24 hourly grid, solar, charge, discharge, and battery-state variables. It applies every validated directive as a hard constraint and minimizes total grid electricity cost.
- **Final Replay Validator** (`app/replay.py`): independently recomputes the energy balance, battery bounds, rate limits, and end-of-day neutrality from the returned plan, exactly as the judge harness will.

## Requirements

- Python 3.11+
- A Gemini API key (`GEMINI_API_KEY`) — free at https://aistudio.google.com/apikey

## Local Quickstart

```bash
git clone <your-repo-url>
cd gridwise
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set GEMINI_API_KEY
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In a second terminal:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "ok"}
```

Run the public sample cases against the running service:

```bash
python3 tests/test_public_samples.py
```

## Environment Variables

| Variable | Required | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | Yes | API key used by the LLM interpretation module. |
| `LLM_MODEL` | No (default `gemini-2.0-flash`) | Gemini model used to interpret operator notes. |
| `PORT` | No (default `8000`) | Port the service listens on. |

## Model / Provider

Language model: Google Gemini (`gemini-2.0-flash` by default, configurable via `LLM_MODEL`; `gemini-1.5-flash` or `gemini-2.0-flash-lite` work as drop-in replacements if you want a different free-tier quota/latency trade-off). The model is called exclusively to convert `operator_notes` into structured directives; it never writes the schedule directly. All numeric scheduling is produced by the deterministic optimizer. The interpreter requests Gemini's native JSON mode (`response_mime_type="application/json"`) and falls back to a plain call with brace-extraction parsing if that is rejected.

## Optimizer / Solver

Linear program built with `scipy.optimize.linprog` (`method="highs"`). Decision variables per hour: `grid_kwh`, `solar_used_kwh`, `battery_charge_kwh`, `battery_discharge_kwh`, `battery_energy_after_kwh`. Objective: minimize `sum(grid_kwh * tariff_bdt_per_kwh)` plus a negligible regularization term that discourages pointless simultaneous charge/discharge cycling. Constraints encode the energy-balance equation, effective solar after `solar_reduction`, battery bounds and rate limits, `no_charge_window` / `no_discharge_window`, `minimum_battery_reserve`, `max_grid_window`, and end-of-day battery neutrality.

## Sample Request / Response

Request:

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @tests/sample_request.json
```

Response shape:

```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
      "explanation": "Solar availability is reduced to 25% during the panel-cleaning window."
    }
  ],
  "hourly_plan": [
    {"hour": 0, "grid_kwh": 90, "solar_used_kwh": 0, "battery_action": "idle", "battery_kwh": 0, "battery_energy_after_kwh": 110}
  ],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365,
  "peak_grid_kwh": 175,
  "plan_summary": "Applied 1 operator directive(s) (solar_reduction) and optimized the remaining schedule for minimum grid cost while restoring end-of-day battery neutrality."
}
```

## Docker Fallback

Build and run locally:

```bash
docker build -t gridwise-llm:latest .
docker run --rm -p 8000:8000 -e GEMINI_API_KEY=your_key gridwise-llm:latest
curl http://localhost:8000/health
```

Pull from registry (replace with your pushed image reference):

```bash
docker pull <your-dockerhub-username>/gridwise-llm:latest
docker run --rm -p 8000:8000 -e GEMINI_API_KEY=your_key <your-dockerhub-username>/gridwise-llm:latest
```

The image exposes port `8000`, binds to `0.0.0.0`, and contains no baked-in secrets — the API key is supplied at runtime via `-e`.

## Dependencies

FastAPI, Uvicorn, Pydantic, Google Gen AI SDK, SciPy, NumPy, httpx, python-dotenv. See `requirements.txt` for pinned versions.

## Known Limitations

- The optimizer assumes organizer scoring scenarios are feasible, as stated in the Problem Statement, and does not attempt to resolve mutually contradictory hard directives.
- LLM interpretation failures (timeouts, provider errors, malformed output) fall back to treating all notes as `no_op` for that request rather than blocking the response, so the service always returns a valid schedule.
- The regularization term in the objective is small enough to never change the reported optimal cost, but it does select a specific tie-broken schedule among equally optimal ones.
- Gemini's free tier (via Google AI Studio) has requests-per-minute and requests-per-day limits that vary by model. Under the 4-hour judging window with repeated hidden-test traffic, transient `429` rate-limit errors are possible; the interpreter treats any provider error as a safe `no_op` fallback rather than failing the request, but this does cost interpretation credit for that case, so check your quota on https://aistudio.google.com before the round and consider `gemini-2.0-flash-lite` (higher free-tier throughput) or a second key as backup if traffic looks tight.

## Secret Handling

No API keys or secrets are committed to this repository. `.env` is git-ignored. The Docker image reads `ANTHROPIC_API_KEY` from the runtime environment only.
