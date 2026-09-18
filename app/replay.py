from typing import Any, Dict, List

TOLERANCE = 0.05


def replay_and_validate(hours: List[Dict[str, Any]], battery: Dict[str, Any], directives: List[Dict[str, Any]], hourly_plan: List[Dict[str, Any]]) -> List[str]:
    problems: List[str] = []
    n = 24

    if len(hourly_plan) != n or sorted(e["hour"] for e in hourly_plan) != list(range(n)):
        problems.append("hourly_plan must contain exactly 24 unique hours 0 through 23")
        return problems

    plan_by_hour = {e["hour"]: e for e in hourly_plan}

    effective_solar = [float(hours[h]["solar_kwh"]) for h in range(n)]
    min_reserve = [float(battery["minimum_energy_kwh"]) for h in range(n)]
    max_grid = [float("inf")] * n
    charge_blocked = [False] * n
    discharge_blocked = [False] * n

    for d in directives:
        if not d.get("applies"):
            continue
        adj = d.get("structured_adjustment") or {}
        dtype = d.get("directive_type")
        directive_hours = adj.get("hours", [])

        if dtype == "solar_reduction":
            factor = float(adj.get("factor", 1.0))
            for h in directive_hours:
                effective_solar[h] = float(hours[h]["solar_kwh"]) * factor
        elif dtype == "minimum_battery_reserve":
            level = float(adj.get("minimum_energy_kwh", 0.0))
            for h in directive_hours:
                min_reserve[h] = max(min_reserve[h], level)
        elif dtype == "no_charge_window":
            for h in directive_hours:
                charge_blocked[h] = True
        elif dtype == "no_discharge_window":
            for h in directive_hours:
                discharge_blocked[h] = True
        elif dtype == "max_grid_window":
            cap = float(adj.get("max_grid_kwh", float("inf")))
            for h in directive_hours:
                max_grid[h] = min(max_grid[h], cap)

    capacity = float(battery["capacity_kwh"])
    initial_energy = float(battery["initial_energy_kwh"])
    max_charge_rate = float(battery["max_charge_kwh_per_hour"])
    max_discharge_rate = float(battery["max_discharge_kwh_per_hour"])
    prev_energy = initial_energy

    for h in range(n):
        entry = plan_by_hour[h]
        demand = float(hours[h]["demand_kwh"])
        grid = float(entry["grid_kwh"])
        solar_used = float(entry["solar_used_kwh"])
        action = entry["battery_action"]
        magnitude = float(entry["battery_kwh"])
        after = float(entry["battery_energy_after_kwh"])

        if grid < -TOLERANCE:
            problems.append(f"hour {h}: negative grid_kwh")
        if solar_used < -TOLERANCE or solar_used > effective_solar[h] + TOLERANCE:
            problems.append(f"hour {h}: solar_used_kwh exceeds effective solar")
        if grid > max_grid[h] + TOLERANCE:
            problems.append(f"hour {h}: grid_kwh violates max_grid_window")

        charge_kwh = magnitude if action == "charge" else 0.0
        discharge_kwh = magnitude if action == "discharge" else 0.0

        if action == "charge" and charge_kwh > max_charge_rate + TOLERANCE:
            problems.append(f"hour {h}: charge exceeds max_charge_kwh_per_hour")
        if action == "discharge" and discharge_kwh > max_discharge_rate + TOLERANCE:
            problems.append(f"hour {h}: discharge exceeds max_discharge_kwh_per_hour")
        if action == "charge" and charge_blocked[h] and charge_kwh > TOLERANCE:
            problems.append(f"hour {h}: charging occurred during a no_charge_window")
        if action == "discharge" and discharge_blocked[h] and discharge_kwh > TOLERANCE:
            problems.append(f"hour {h}: discharging occurred during a no_discharge_window")

        expected_after = prev_energy + charge_kwh - discharge_kwh
        if abs(expected_after - after) > TOLERANCE:
            problems.append(f"hour {h}: battery_energy_after_kwh inconsistent with battery action")
        if after < min_reserve[h] - TOLERANCE or after > capacity + TOLERANCE:
            problems.append(f"hour {h}: battery_energy_after_kwh out of bounds")

        balance = grid + solar_used + discharge_kwh - demand - charge_kwh
        if abs(balance) > TOLERANCE:
            problems.append(f"hour {h}: energy balance equation violated")

        prev_energy = after

    if abs(prev_energy - initial_energy) > TOLERANCE:
        problems.append("end-of-day battery neutrality violated")

    return problems
