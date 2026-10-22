"""Planificateur (B : Agent). Tout le calcul est ici, le LLM ne calcule rien.

Deux passes (README section 10) :
1. Affectation : chaque service reçoit le bus le plus juste qui le couvre avec la marge demandée
   (titulaires d'abord, réserve B31+ seulement si aucun titulaire ne convient).
2. Recharge : on remplit, par créneaux de 30 min, ce que chaque bus doit encore recevoir pour
   atteindre le SoC visé, dans les créneaux les moins chers, sans dépasser la puissance du dépôt,
   avec un bus par chargeur et par créneau.

`check_plan` ne connaît que le modèle de l'agent : il ne juge jamais sur la vérité cachée
(c'est le rôle de `metrics.compare_plans`).
"""
import time
from datetime import date, datetime, timedelta

from app import data, model
from app.contract import Constraints, Plan, PlanCheck

SLOT_H = 0.5                       # créneaux de 30 min
READY_BEFORE_MIN = 30              # le bus doit être chargé 30 min avant le départ
DEFAULT_MARGIN_PCT = 15.0
DEFAULT_CHARGE_START = "21:00"     # veille au soir
ADVANCED_CHARGE_START = "19:00"    # si advance_charging
RESERVE_FROM = 31                  # B31 à B40 : bus de réserve (convention, pas de colonne)
FALLBACK_PRICE = 0.20
EPS = 1e-9


def is_reserve(bus_id: str) -> bool:
    digits = "".join(ch for ch in bus_id if ch.isdigit())
    return bool(digits) and int(digits) >= RESERVE_FROM


def _window_start(night: date, constraints: Constraints) -> datetime:
    hhmm = constraints.get("charge_start") or (
        ADVANCED_CHARGE_START if constraints.get("advance_charging") else DEFAULT_CHARGE_START
    )
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.combine(night - timedelta(days=1), datetime.min.time()).replace(hour=hour, minute=minute)


def _ready_time(night: date, service: dict) -> datetime:
    start = datetime.fromisoformat(service["start_time"])
    return datetime.combine(night, start.time()) - timedelta(minutes=READY_BEFORE_MIN)


# --- passe 1 : affectation -----------------------------------------------------

def _assign(buses, services, temp, margin_pct, use_reserve):
    """Renvoie ([(bus, service, need_kwh, soc_target)], [service_id non couverts])."""
    free = list(buses)
    picked, uncovered = [], []
    for svc in sorted(services, key=lambda s: (-s["distance_km"], s["service_id"])):
        options = []
        for bus in free:
            need = model.predict_consumption(bus["bus_id"], svc["service_id"], temp)
            full = model.available_kwh(bus["bus_id"], temp, 1.0)
            if full <= 0:
                continue
            soc_target = need / (1 - margin_pct / 100) / full
            if soc_target <= 1 + EPS:
                options.append((bus, need, min(soc_target, 1.0), full))
        titulars = [o for o in options if not is_reserve(o[0]["bus_id"])]
        pool = titulars or (options if use_reserve else [])
        if not pool:
            uncovered.append(svc["service_id"])
            continue
        bus, need, soc_target, _ = min(pool, key=lambda o: (o[3], o[0]["bus_id"]))  # le plus juste
        free.remove(bus)
        picked.append((bus, svc, need, soc_target))
    return picked, uncovered


# --- passe 2 : recharge --------------------------------------------------------

def _price_by_hour(night: date) -> dict[datetime, float]:
    return {datetime.fromisoformat(p["ts"]).replace(minute=0, second=0): p["eur_kwh"]
            for p in data.get_prices(night)}


def _schedule_charging(picked, night, window_start, constraints):
    """Renvoie (entrées Charging, kWh livrés par bus)."""
    if not picked:
        return [], {}
    cap = data.get_depot()["max_power_kw"]
    chargers = sorted((c for c in data.get_chargers() if c["status"] == "ok"),
                      key=lambda c: (-c["power_kw"], c["charger_id"]))
    prices = _price_by_hour(night)
    ready = {b["bus_id"]: _ready_time(night, s) for b, s, _, _ in picked}
    horizon = max(ready.values())
    slots, t = [], window_start
    while t + timedelta(hours=SLOT_H) <= horizon:
        slots.append(t)
        t += timedelta(hours=SLOT_H)
    price = [prices.get(s.replace(minute=0), FALLBACK_PRICE) for s in slots]
    depot_used = [0.0] * len(slots)
    busy: set[tuple[str, int]] = set()

    def needed_kwh(bus, soc_target):
        return max(0.0, soc_target - bus["soc_evening"]) * bus["battery_kwh"] * bus["soh"]

    order = sorted(picked, key=lambda p: (ready[p[0]["bus_id"]], -needed_kwh(p[0], p[3]), p[0]["bus_id"]))
    entries: list[dict] = []
    delivered: dict[str, float] = {}

    for bus, _svc, _need, soc_target in order:
        todo = needed_kwh(bus, soc_target)
        if todo <= EPS:
            continue
        limit = sum(1 for s in slots if s + timedelta(hours=SLOT_H) <= ready[bus["bus_id"]])
        best = None  # (complete, -cost, power_kw, charger, chosen)
        for ch in chargers:
            remaining, chosen = todo, []
            for i in sorted(range(limit), key=lambda i: (price[i], i)):
                if remaining <= EPS:
                    break
                power = min(ch["power_kw"], cap - depot_used[i])
                if (ch["charger_id"], i) in busy or power <= EPS:
                    continue
                kwh = min(power * SLOT_H, remaining)
                chosen.append((i, kwh / SLOT_H, kwh))
                remaining -= kwh
            got = todo - remaining
            cost = sum(k * price[i] for i, _, k in chosen)
            key = (remaining <= EPS, -cost if remaining <= EPS else got, -ch["power_kw"])
            if best is None or key > best[0]:
                best = (key, ch, chosen)
        _, ch, chosen = best
        if not chosen:
            continue
        for i, power, _ in chosen:
            depot_used[i] += power
            busy.add((ch["charger_id"], i))
        delivered[bus["bus_id"]] = sum(k for _, _, k in chosen)
        entries.extend(_merge_runs(bus["bus_id"], ch["charger_id"], chosen, slots, price))
    entries.sort(key=lambda e: (e["start"], e["charger_id"]))
    return entries, delivered


def _merge_runs(bus_id, charger_id, chosen, slots, price):
    """Fusionne les créneaux contigus à puissance identique en une seule entrée Charging."""
    runs: list[list] = []
    for i, power, kwh in sorted(chosen):
        if runs and runs[-1][-1][0] == i - 1 and abs(runs[-1][-1][1] - power) < 1e-6:
            runs[-1].append((i, power, kwh))
        else:
            runs.append([(i, power, kwh)])
    out = []
    for run in runs:
        start = slots[run[0][0]]
        out.append({
            "bus_id": bus_id,
            "charger_id": charger_id,
            "start": start.isoformat(),
            "end": (start + timedelta(hours=SLOT_H * len(run))).isoformat(),
            "kwh": sum(k for _, _, k in run),
            "cost_eur": round(sum(k * price[i] for i, _, k in run), 2),
        })
    return out


# --- API publique --------------------------------------------------------------

def run_planner(constraints: Constraints) -> Plan:
    t0 = time.perf_counter()
    night = date.fromisoformat(constraints["night_date"])
    strategy = constraints["strategy"]
    forecast = data.get_forecast(night)
    real_temp = constraints.get("temp_override_c", min(p["temp_c"] for p in forecast))
    model_temp = 15.0 if strategy == "naive" else real_temp  # le plan naïf ignore la météo
    margin = float(constraints.get("margin_pct", DEFAULT_MARGIN_PCT))
    excluded = set(constraints.get("exclude_buses", []))
    buses = [b for b in data.get_buses() if b["bus_id"] not in excluded]

    picked, uncovered = _assign(
        buses, data.get_services(), model_temp, margin, constraints.get("use_reserve", True)
    )
    charging, delivered = _schedule_charging(
        picked, night, _window_start(night, constraints), constraints
    )

    assignments, dropped = [], set()
    for bus, svc, need, _ in picked:
        soc = min(1.0, bus["soc_evening"] + delivered.get(bus["bus_id"], 0.0) / (bus["battery_kwh"] * bus["soh"]))
        avail = model.available_kwh(bus["bus_id"], model_temp, soc)
        if avail + EPS < need:  # la recharge n'a pas suffi : le plan l'avoue au lieu de le cacher
            uncovered.append(svc["service_id"])
            dropped.add(bus["bus_id"])
            continue
        assignments.append({
            "bus_id": bus["bus_id"],
            "service_id": svc["service_id"],
            "predicted_kwh": round(need, 1),
            "available_kwh": round(avail, 1),
            "margin_pct": round((avail - need) / avail * 100, 1),
        })
    charging = [c for c in charging if c["bus_id"] not in dropped]
    assignments.sort(key=lambda a: a["service_id"])
    uncovered.sort()

    return {
        "plan_id": f"{night.isoformat()}-{strategy}",
        "night_date": night.isoformat(),
        "strategy": strategy,
        "forecast_min_temp_c": real_temp,
        "assignments": assignments,
        "charging": charging,
        "uncovered_services": uncovered,
        "kpis": {
            "services_uncovered": len(uncovered),
            "charging_cost_eur": round(sum(c["cost_eur"] for c in charging), 1),
            "replan_seconds": round(time.perf_counter() - t0, 2),
        },
        "explanation": "",
    }


def check_plan(plan: Plan, margin_pct: float = DEFAULT_MARGIN_PCT) -> PlanCheck:
    """Vérifie un plan avec le seul modèle de l'agent : couverture, marges, puissance, chargeurs."""
    issues = [f"Service {s} non couvert" for s in plan["uncovered_services"]]
    low = [a["bus_id"] for a in plan["assignments"] if a["margin_pct"] < margin_pct - 0.05]
    issues += [
        f"Marge faible sur {a['bus_id']} : {a['margin_pct']} % (< {margin_pct:g} %)"
        for a in plan["assignments"] if a["bus_id"] in low
    ]

    runs = []
    for c in plan["charging"]:
        s, e = datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"])
        hours = (e - s).total_seconds() / 3600
        runs.append((s, e, c, c["kwh"] / hours if hours > 0 else 0.0))

    peak = max((sum(p for s2, e2, _, p in runs if s2 <= s < e2) for s, _, _, _ in runs), default=0.0)
    max_kw = data.get_depot()["max_power_kw"]
    if peak > max_kw + 1e-6:
        issues.append(f"Puissance du dépôt dépassée : {peak:.0f} kW pour {max_kw:.0f} kW autorisés")

    for i, (s1, e1, c1, _) in enumerate(runs):
        for s2, e2, c2, _ in runs[i + 1:]:
            if s1 < e2 and s2 < e1:
                if c1["charger_id"] == c2["charger_id"]:
                    issues.append(
                        f"Chargeur {c1['charger_id']} utilisé par {c1['bus_id']} et {c2['bus_id']} en même temps"
                    )
                if c1["bus_id"] == c2["bus_id"]:
                    issues.append(f"Bus {c1['bus_id']} branché sur deux chargeurs en même temps")

    return {
        "ok": not issues,
        "uncovered_services": list(plan["uncovered_services"]),
        "low_margin_buses": low,
        "peak_power_kw": round(peak, 1),
        "max_power_kw": max_kw,
        "issues": issues,
    }


def reassess_plan(plan: Plan, temp_c: float) -> dict:
    """Réévalue un plan existant à une autre température, avec le modèle de l'agent.

    C'est l'étape « détecter » : combien de services de l'ancien plan ne tiennent plus ?
    """
    buses = {b["bus_id"]: b for b in data.get_buses()}
    charged: dict[str, float] = {}
    for c in plan["charging"]:
        charged[c["bus_id"]] = charged.get(c["bus_id"], 0.0) + c["kwh"]
    infeasible = []
    for a in plan["assignments"]:
        b = buses[a["bus_id"]]
        soc = min(1.0, b["soc_evening"] + charged.get(a["bus_id"], 0.0) / (b["battery_kwh"] * b["soh"]))
        need = model.predict_consumption(a["bus_id"], a["service_id"], temp_c)
        avail = model.available_kwh(a["bus_id"], temp_c, soc)
        if avail + EPS < need:
            infeasible.append({
                "service_id": a["service_id"],
                "bus_id": a["bus_id"],
                "missing_kwh": round(need - avail, 1),
            })
    return {"temp_c": temp_c, "infeasible": infeasible}


def diff_plans(old: Plan, new: Plan) -> dict:
    """Ce qui change entre deux plans, calculé en code : le LLM ne fait que le mettre en mots."""
    old_bus = {a["service_id"]: a["bus_id"] for a in old["assignments"]}
    new_bus = {a["service_id"]: a["bus_id"] for a in new["assignments"]}
    reassigned = [
        {"service_id": s, "from_bus": old_bus[s], "to_bus": b, "to_reserve": is_reserve(b)}
        for s, b in sorted(new_bus.items())
        if s in old_bus and old_bus[s] != b
    ]
    reserve_activated = sorted(
        b for b in set(new_bus.values()) - set(old_bus.values()) if is_reserve(b)
    )

    def first_start(plan):
        starts: dict[str, datetime] = {}
        for c in plan["charging"]:
            t = datetime.fromisoformat(c["start"])
            starts[c["bus_id"]] = min(t, starts.get(c["bus_id"], t))
        return starts

    before, after = first_start(old), first_start(new)
    advanced = [
        {"bus_id": b, "hours_earlier": round((before[b] - t).total_seconds() / 3600, 1)}
        for b, t in sorted(after.items())
        if b in before and before[b] - t >= timedelta(minutes=30)
    ]
    return {
        "reassigned": reassigned,
        "reserve_activated": reserve_activated,
        "charging_advanced": advanced,
        "cost_delta_eur": round(new["kpis"]["charging_cost_eur"] - old["kpis"]["charging_cost_eur"], 1),
        "uncovered_before": list(old["uncovered_services"]),
        "uncovered_after": list(new["uncovered_services"]),
    }
