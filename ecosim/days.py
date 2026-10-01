"""Días de evaluación y de selección (plan 2026-09-28-ecosim2, "Días").

`uv run python -m ecosim.days` escribe `ecosim/days.json` con dos listas de 15
días de sep–nov 2025 (sin el 2025-11-30, cuya ventana llega a diciembre):

* `evaluacion`: los 15 días del run 1 (`config.RUN1_EVAL_DAYS`), conservados si
  cumplen la cobertura nueva (≥ 90% de los bloques de 60 min de [05:30, 00:30)
  con al menos un snapshot crudo). Uno que no cumpla se reemplaza por otro del
  mismo estrato al azar con semilla `EVAL_SEED` (20260928).
* `seleccion`: 15 días nuevos, disjuntos de los de evaluación, con cobertura
  ≥ 90%, al azar con semilla `SELECTION_SEED` (20260929), estratificados 11
  entre semana + 4 fin de semana o festivo (MX).

`days` es un alias de `evaluacion` (compatibilidad con el código del run 1).
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import holidays
import numpy as np

from ecosim import config as C
from ecosim import data


def day_type(d: date) -> str:
    if d in holidays.MX(years=d.year):
        return "holiday"
    return "weekend" if d.weekday() >= 5 else "weekday"


def _weekday(c: dict) -> bool:
    return c["type"] == "weekday"


def candidate_days() -> list[dict]:
    out, d = [], C.EVAL_START
    while d <= C.EVAL_END:
        out.append({"day": d.isoformat(), "type": day_type(d), "coverage": round(data.coverage(d), 4)})
        d += timedelta(days=1)
    return out


def evaluation_days(cands: list[dict]) -> tuple[list[dict], list[dict]]:
    """Los días del run 1 que cumplen la cobertura; cada uno que no cumpla se
    reemplaza (en orden de fecha) por uno del mismo estrato elegido al azar
    con `EVAL_SEED` entre los candidatos que cumplen y no están ya elegidos.
    Devuelve (días, reemplazos)."""
    by_day = {c["day"]: c for c in cands}
    ok = [c for c in cands if c["coverage"] >= C.COVERAGE_MIN]
    chosen = [by_day[d] for d in C.RUN1_EVAL_DAYS]
    keep = [c for c in chosen if c["coverage"] >= C.COVERAGE_MIN]
    rng = np.random.default_rng(C.EVAL_SEED)
    out, repl = list(keep), []
    for c in chosen:
        if c in keep:
            continue
        taken = {x["day"] for x in out} | set(C.RUN1_EVAL_DAYS)
        pool = [x for x in ok if _weekday(x) == _weekday(c) and x["day"] not in taken]
        new = pool[rng.choice(len(pool))]
        out.append(new)
        repl.append({"run1": c["day"], "coverage": c["coverage"], "nuevo": new["day"]})
    return sorted(out, key=lambda c: c["day"]), repl


def selection_days(cands: list[dict], exclude: list[str]) -> list[dict]:
    """11 entre semana + 4 fin de semana/festivo al azar (`SELECTION_SEED`),
    sin reemplazo, entre los candidatos con cobertura ≥ 0.90 que no están en
    `exclude`."""
    ok = [c for c in cands if c["coverage"] >= C.COVERAGE_MIN and c["day"] not in set(exclude)]
    wd = [c for c in ok if _weekday(c)]
    we = [c for c in ok if not _weekday(c)]
    rng = np.random.default_rng(C.SELECTION_SEED)
    pick_wd = rng.choice(len(wd), C.N_SEL_WEEKDAY, replace=False)
    pick_we = rng.choice(len(we), C.N_SEL_WEEKEND, replace=False)
    chosen = [wd[i] for i in pick_wd] + [we[i] for i in pick_we]
    return sorted(chosen, key=lambda c: c["day"])


def build() -> dict:
    cands = candidate_days()
    ev, repl = evaluation_days(cands)
    sel = selection_days(cands, [c["day"] for c in ev])
    ok = [c for c in cands if c["coverage"] >= C.COVERAGE_MIN]
    out = {
        "range": [C.EVAL_START.isoformat(), C.EVAL_END.isoformat()],
        "coverage_min": C.COVERAGE_MIN,
        "coverage_def": "fracción de los 19 bloques de 60 min en [05:30, 00:30 del día siguiente) "
                        "con ≥1 snapshot GBFS crudo",
        "eval_seed": C.EVAL_SEED,
        "selection_seed": C.SELECTION_SEED,
        "pool": {
            "weekday": sum(_weekday(c) for c in ok),
            "weekend_or_holiday": sum(not _weekday(c) for c in ok),
            "excluded_low_coverage": [c["day"] for c in cands if c["coverage"] < C.COVERAGE_MIN],
            "excluded_sealed_window": ["2025-11-30"],
        },
        "reemplazos_evaluacion": repl,
        "evaluacion": ev,
        "seleccion": sel,
        "days": ev,
        "all_candidates": cands,
    }
    C.DAYS_JSON.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return out


def load_days(kind: str = "evaluacion") -> list[str]:
    """Días ('YYYY-MM-DD') en orden: `kind` = "evaluacion" (por defecto),
    "seleccion" o "todos" (las dos listas juntas, ordenadas)."""
    js = json.loads(C.DAYS_JSON.read_text())
    if kind == "todos":
        return sorted(d["day"] for d in js["evaluacion"] + js["seleccion"])
    if kind not in ("evaluacion", "seleccion"):
        raise ValueError(f"kind: 'evaluacion', 'seleccion' o 'todos', llegó {kind!r}")
    return [d["day"] for d in js[kind]]


if __name__ == "__main__":
    out = build()
    for kind in ("evaluacion", "seleccion"):
        ds = out[kind]
        n_wd = sum(_weekday(d) for d in ds)
        print(f"{kind}: {len(ds)} días ({n_wd} entre semana, {len(ds) - n_wd} fin de semana/festivo)")
        for d in ds:
            print(" ", d["day"], d["type"], d["coverage"])
    print("reemplazos:", out["reemplazos_evaluacion"] or "ninguno")
    print("excluidos por cobertura:", out["pool"]["excluded_low_coverage"])
    print("→", C.DAYS_JSON)
