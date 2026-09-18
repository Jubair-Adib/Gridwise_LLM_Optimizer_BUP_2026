from typing import Any, Dict, List

import numpy as np
from scipy.optimize import linprog

TOLERANCE = 0.01
EPSILON = 1e-4


def _apply_directives(hours: List[Dict[str, Any]], battery: Dict[str, Any], directives: List[Dict[str, Any]]):
    n = 24
    effective_solar = [float(hours[h]["solar_kwh"]) for h in range(n)]
    min_reserve = [float(battery["minimum_energy_kwh"]) for h in range(n)]
    max_grid = [float("inf")] * n
    charge_blocked = [False] * n
    discharge_blocked = [False] * n

    for d in directives:
        if not d.get("applies"):
            continue
        adj = d.get("structured_adjustment") or {}
        directive_hours = adj.get("hours", [])
        dtype = d.get("directive_type")

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

    return effective_solar, min_reserve, max_grid, charge_blocked, discharge_blocked


def solve_schedule(hours: List[Dict[str, Any]], battery: Dict[str, Any], directives: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = 24
    effective_solar, min_reserve, max_grid, charge_blocked, discharge_blocked = _apply_directives(hours, battery, directives)

    capacity = float(battery["capacity_kwh"])
    initial_energy = float(battery["initial_energy_kwh"])
    max_charge_rate = float(battery["max_charge_kwh_per_hour"])
    max_discharge_rate = float(battery["max_discharge_kwh_per_hour"])
    tariff = [float(hours[h]["tariff_bdt_per_kwh"]) for h in range(n)]
    demand = [float(hours[h]["demand_kwh"]) for h in range(n)]

    num_vars = 5 * n
    g_slice = slice(0, n)
    su_slice = slice(n, 2 * n)
    c_slice = slice(2 * n, 3 * n)
    d_slice = slice(3 * n, 4 * n)
    b_slice = slice(4 * n, 5 * n)

    cost = np.zeros(num_vars)
    for h in range(n):
        cost[h] = tariff[h]
        cost[2 * n + h] = EPSILON
        cost[3 * n + h] = EPSILON

    A_eq = []
    b_eq = []

    for h in range(n):
        row = np.zeros(num_vars)
        row[h] = 1.0
        row[n + h] = 1.0
        row[3 * n + h] = 1.0
        row[2 * n + h] = -1.0
        A_eq.append(row)
        b_eq.append(demand[h])

    for h in range(n):
        row = np.zeros(num_vars)
        row[4 * n + h] = 1.0
        row[2 * n + h] = -1.0
        row[3 * n + h] = 1.0
        if h == 0:
            b_eq.append(initial_energy)
        else:
            row[4 * n + h - 1] = -1.0
            b_eq.append(0.0)
        A_eq.append(row)

    row = np.zeros(num_vars)
    row[4 * n + n - 1] = 1.0
    A_eq.append(row)
    b_eq.append(initial_energy)

    bounds = []
    for h in range(n):
        upper_grid = max_grid[h] if max_grid[h] != float("inf") else None
        bounds.append((0.0, upper_grid))
    for h in range(n):
        bounds.append((0.0, effective_solar[h]))
    for h in range(n):
        upper_c = 0.0 if charge_blocked[h] else max_charge_rate
        bounds.append((0.0, upper_c))
    for h in range(n):
        upper_d = 0.0 if discharge_blocked[h] else max_discharge_rate
        bounds.append((0.0, upper_d))
    for h in range(n):
        bounds.append((min_reserve[h], capacity))

    result = linprog(cost, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")

    if not result.success:
        raise RuntimeError(f"optimization infeasible: {result.message}")

    x = result.x
    grid = x[g_slice]
    solar_used = x[su_slice]
    charge = x[c_slice]
    discharge = x[d_slice]
    battery_after = x[b_slice]

    hourly_plan = []
    for h in range(n):
        c_h = max(0.0, float(charge[h]))
        d_h = max(0.0, float(discharge[h]))
        if c_h > TOLERANCE and c_h >= d_h:
            action = "charge"
            magnitude = c_h
        elif d_h > TOLERANCE:
            action = "discharge"
            magnitude = d_h
        else:
            action = "idle"
            magnitude = 0.0

        hourly_plan.append(
            {
                "hour": h,
                "grid_kwh": round(max(0.0, float(grid[h])), 4),
                "solar_used_kwh": round(max(0.0, float(solar_used[h])), 4),
                "battery_action": action,
                "battery_kwh": round(magnitude, 4),
                "battery_energy_after_kwh": round(float(battery_after[h]), 4),
            }
        )

    total_grid = round(sum(entry["grid_kwh"] for entry in hourly_plan), 4)
    total_cost = round(sum(entry["grid_kwh"] * tariff[h] for h, entry in enumerate(hourly_plan)), 4)
    peak_grid = round(max(entry["grid_kwh"] for entry in hourly_plan), 4)

    return {
        "hourly_plan": hourly_plan,
        "total_grid_kwh": total_grid,
        "total_cost_bdt": total_cost,
        "peak_grid_kwh": peak_grid,
    }
