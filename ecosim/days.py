"""Cortes reproducibles de los días del run 3.

`uv run python -m ecosim.days` reconstruye `days.json` desde el feed.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import holidays
import numpy as np
import pandas as pd

from ecosim import config as C
from ecosim import data


def day_type(d: date) -> str:
    if d in holidays.MX(years=d.year):
        return "holiday"
    return "weekend" if d.weekday() >= 5 else "weekday"


def _dates(start: date, end: date):
    for i in range((end - start).days + 1):
        yield start + timedelta(days=i)


def candidate_days(start: date, end: date) -> list[dict]:
    return [{"day": d.isoformat(), "type": day_type(d),
             "coverage": round(data.coverage(d), 4),
             "open_offset_min": _initial_photo_offset(d)} for d in _dates(start, end)]


def _sample(candidates: list[dict], weekday: int, other: int,
            seed: int | np.random.Generator) -> list[dict]:
    eligible = [c for c in candidates if c["coverage"] >= C.COVERAGE_MIN
                and c["open_offset_min"] is not None]
    wd = [c for c in eligible if c["type"] == "weekday"]
    we = [c for c in eligible if c["type"] != "weekday"]
    if len(wd) < weekday or len(we) < other:
        raise ValueError(f"pool insuficiente: {len(wd)} entre semana, {len(we)} otros")
    rng = seed if isinstance(seed, np.random.Generator) else np.random.default_rng(seed)
    out = [wd[i] for i in rng.choice(len(wd), weekday, replace=False)]
    out += [we[i] for i in rng.choice(len(we), other, replace=False)]
    return sorted(out, key=lambda c: c["day"])


def selection_days(candidates: list[dict]) -> list[dict]:
    return _sample(candidates, C.N_SEL_WEEKDAY, C.N_SEL_WEEKEND, C.SELECTION_SEED)


def curve_days(candidates: list[dict]) -> list[dict]:
    out = []
    rng = np.random.default_rng(C.CURVE_SEED)
    for month in ("2025-09", "2025-10", "2025-11", "2025-12"):
        pool = [c for c in candidates if c["day"].startswith(month)]
        out.extend(_sample(pool, 6, 2, rng))
    return sorted(out, key=lambda c: c["day"])


def _initial_photo_offset(d: date) -> float | None:
    try:
        raw = data._raw_snapshots(d)
    except FileNotFoundError:
        return None
    raw = raw[raw["short_name"].str.fullmatch(C.SHORT_NAME_RE)]
    opening = raw[raw["t"].dt.hour == 5].groupby("t")["is_renting"].agg(["mean", "size"])
    renting = opening["mean"].where(opening["size"] >= 0.95 * opening["size"].max())
    valid = renting[renting >= C.INITIAL_OPEN_RENTING_MIN]
    if valid.empty:
        return None
    return float((valid.index[0] - pd.Timestamp(C.day_bounds(d)[0])) / pd.Timedelta(minutes=1))


def build() -> dict:
    august = candidate_days(date(2025, 8, 1), date(2025, 8, 31))
    test = candidate_days(date(2025, 9, 1), date(2026, 1, 31))
    seleccion = selection_days(august)
    prueba = {m: [c for c in test if c["day"].startswith(m) and c["coverage"] >= C.COVERAGE_MIN
                  and c["open_offset_min"] is not None]
              for m in ("2025-09", "2025-10", "2025-11", "2025-12", "2026-01")}
    curva = curve_days(test)
    prod = {}
    trip_days = data.trip_days(date(2026, 2, 1), date(2026, 8, 31))
    for m in range(2, 9):
        month = f"2026-{m:02d}"
        start = date(2026, m, 1)
        end = C._month_offset(2026, m, 1) - timedelta(days=1)
        candidates = []
        for d in _dates(start, end):
            offset = _initial_photo_offset(d)
            full_trips = d in trip_days and (m != 3 or d <= C.TRIPS_2026_MARCH_LAST_COMPLETE)
            if offset is not None and offset <= 10 and full_trips and data.coverage(d) >= C.COVERAGE_MIN:
                candidates.append({"day": d.isoformat(), "type": day_type(d),
                                   "offset_min": round(offset, 2), "coverage": round(data.coverage(d), 4)})
        prod[month] = candidates[:8]
    out = {
        "coverage_def": "fracción de 20 bloques (19 de 60 min y uno de 30 min) en [05:00, 00:30) con foto",
        "coverage_min": C.COVERAGE_MIN,
        "selection_seed": C.SELECTION_SEED, "curve_seed": C.CURVE_SEED,
        "seleccion": seleccion, "prueba": prueba, "curva": curva, "prod_2026": prod,
        "coverage_by_month": json.loads(C.COVERAGE_AUDIT.read_text())["months"],
    }
    C.DAYS_JSON.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return out


def load_days(kind: str = "seleccion", month: str | None = None) -> list[str]:
    js = json.loads(C.DAYS_JSON.read_text())
    if kind in ("seleccion", "curva"):
        rows = js[kind]
    elif kind in ("prueba", "prod_2026") and month is not None:
        rows = js[kind][month]
    else:
        raise ValueError(f"kind/mes inválido: {kind!r}, {month!r}")
    return [r["day"] for r in rows]


if __name__ == "__main__":
    result = build()
    print("seleccion:", len(result["seleccion"]), "días")
    print("prueba:", {m: len(v) for m, v in result["prueba"].items()})
    print("curva:", len(result["curva"]), "días")
    print("prod_2026:", {m: len(v) for m, v in result["prod_2026"].items()})
    print("→", C.DAYS_JSON)
