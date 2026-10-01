"""Experimentos del run 2 de ecosim (plan 2026-09-28-ecosim2, subtask
`integracion2`). Ventana 05:30–00:30, dañadas dinámicas, bodega finita, tope
de bicis por movimiento, pares ±1 fuera de lo principal y selección fuera de
muestra.

    uv run python -m ecosim.run --all          # todo, desde cero
    uv run python -m ecosim.run --stage v1     # un paso: v1 | lambda | h | grid | eval | sens | falla | tablas

Días (`ecosim/days.json`): **selección** (15) para todo lo que se elige y
**evaluación** (15, los del run 1) para reportar. Nada se elige en evaluación.

Pasos (en este orden en `--all`):

1. **v1** (evaluación): baseline y replay de Ecobici (`rebal`, t0, sin ±1,
   dañadas dinámicas) contra GBFS; sensibilidades t1, con ±1 y dañadas fijas
   (baseline fijo y replay `stock` fijo = método del run 1). E y F por día y
   por franja; stock y dañadas simuladas contra cada snapshot. Si la
   diferencia media en E o F pasa de 25%, `--all` se detiene aquí.
2. **lambda** (selección): oracle (f = 60, H = 2, L = 60) con λ ∈ LAMBDA_GRID
   y las dos cotas de retiro (⌊p⌋ y ⌊p − σ⌋). Cota de retiro = la de menor
   E+F medio en toda la rejilla (λ × día); λ = codo de Kneedle sobre la
   curva de esa cota. Se congelan en `results/frozen.json`.
3. **h** (selección): oracle y `ma` (f = 60) con h = 1, 2, 3, …; cada brazo
   para en el primer h que no mejora E+F en ≥ 1% sobre h − 1; h_max = el
   mayor de los dos. Los dos se completan hasta h_max.
4. **grid** (selección): `ma` y `model` con f ∈ {15, 60, 180} × h ∈ 1..h_max
   (sin f > 60·h + L) y `daily` con h ∈ 1..h_max. Mejor (f, h) por variante
   (menor E+F medio); el oracle usa su mejor h de la búsqueda. Mejor brazo
   real = el de menor E+F de selección entre ma/model/daily. Se congela.
5. **eval** (evaluación): baseline, replay de Ecobici (y sus versiones con
   dañadas fijas), oracle, ma, model y daily con lo congelado y L = 60;
   luego L ∈ {45, 30} para oracle, ma y model.
6. **sens** (evaluación): oracle y el mejor brazo real con tope por
   movimiento 14/42 (y sin tope), tope por hora con ±1, dañadas fijas,
   bodega ilimitada, bodega con el taller (|A − R| de `stock`), μ = 0, la
   otra cota de retiro, y todo como en el run 1 a la vez.
7. **falla**: 20 (estación, bloque de 60 min) con más E+F en el mejor brazo
   real, contra oracle, Ecobici, baseline y el error de su pronóstico.
8. **tablas**: `results/tablas.md` y `results/resumen.csv`.

Métrica principal: E y F sin las estaciones-día fuera de servicio a las 05:30
(`initial_state.out_of_service`), en todos los brazos y en lo observado; la
cifra con ellas va en `E_all`/`F_all`. Franjas (bloques de 60 min desde
05:30): mañana 05:30–12:30, tarde 12:30–18:30, noche 18:30–00:30.

Cómputo: cada corrida es un proceso de 1 thread (HiGHS con `threads=1` y sin
corte por tiempo en la práctica, ver HIGHS_TIME_LIMIT; BLAS/OpenMP/Polars con
1 thread en los hijos, ver CHILD_THREAD_ENV);
`--jobs` corridas a la vez (por defecto 8 = los 2 cupos del semáforo × 4
threads). Las corridas se guardan en `resultados.csv` al terminar cada lote y
se reutilizan por su llave (`run`): `--stage` retoma lo que falte; `--all`
borra todo y empieza de cero.

Los insumos (viajes, snapshots, dañadas, movimientos de Ecobici,
pronósticos) son salidas de los otros módulos y no se regeneran aquí.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time as _time
import warnings
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd

from ecosim import config as C
from ecosim import contracts as K
from ecosim import data, medicion, sim
from ecosim import pronostico as P
from ecosim.asignador import Asignador
from ecosim.days import load_days

RESULTS = C.REPO_ROOT / "ecosim" / "results"
V1_DIR = RESULTS / "v1"
RUN1_DIR = RESULTS / "run1"                       # números del run 1 (copiados del tag `ecosim-run1`)
STATS_JSON = RESULTS / "medicion" / "ecobici_stats.json"
OBS_CSV = RESULTS / "medicion" / "ecobici_observado.csv"
FROZEN_JSON = RESULTS / "frozen.json"
RESULTADOS_CSV = RESULTS / "resultados.csv"
# E/F por estación × bloque de las corridas de evaluación con L = 60 (intermedio, fuera de git)
BLOCKS_PARQUET = C.DERIVED / "run2_station_block.parquet"
V1_BLOCKS_PARQUET = C.DERIVED / "run2_v1_estacion_bloque.parquet"

LAMBDA_GRID = [0, 5, 15, 30, 60, 120, 240]
RETIROS = ("floor", "sigma")       # ⌊p⌋ y ⌊p − σ⌋ (Asignador(retiro=...))
MU = 1.0
L_MAIN = 60
H_LAMBDA = 2                       # λ se elige con H = 2 (plan)
F_ORACLE = 60                      # el contenido del oracle no depende de f
F_H_SEARCH = 60                    # `ma` de la búsqueda de h
H_ARMS = ("oracle", "ma")
H_MIN_GAIN = 0.01                  # h sube mientras E+F mejore ≥ 1% sobre h − 1
F_GRID = tuple(C.FORECAST_REFRESH_MIN)
GRID_ARMS = ("ma", "model")
POLICY_ARMS = ("oracle", "ma", "model", "daily")
REAL_ARMS = ("ma", "model", "daily")
L_GRID_ARMS = ("oracle", "ma", "model")
L_SENS = (45, 30)
HIGHS_THREADS = 1
# Límite de HiGHS por decisión (s). El del Asignador (4 s) es de reloj de pared:
# cuando se alcanza, la solución depende de la carga de la máquina (y de si se
# durmió), y `--all` deja de reproducir. Con 600 s ninguna decisión lo alcanza,
# así que todas terminan en el óptimo (mip_rel_gap) y el resultado es
# determinista. Máximo observado en el run 2: 333–361 s, siempre con λ = 5
# (sel_lambda y sens_lam_min_seleccion); en los brazos principales de
# evaluación, ≤ 3.9 s. Sigue siendo reloj de pared: si alguna decisión no
# llega al óptimo (p. ej. la máquina hiberna a mitad de un solve), `run_one`
# lanza error (`check_optimal`) en vez de guardar una corrida no reproducible.
HIGHS_TIME_LIMIT = 600.0
# Threads de BLAS/OpenMP/Polars en los procesos hijos del pool: 1 cada uno, así
# `--jobs 8` son 8 threads (heavy.py fija 4 en el proceso padre).
CHILD_THREAD_ENV = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "POLARS_MAX_THREADS")
JOBS = 8
CHUNK = 40                         # corridas por lote antes de guardar
NO_CAP = 10**6                     # "sin tope"
V1_STOP = 0.25                     # diferencia media > 25% en E o F → parar tras V1
V1_OK = 0.10                       # criterio de V1
N_BLOCKS = C.WINDOW_MIN // 60      # 19 bloques de 60 min desde 05:30
FRANJAS = {"manana": (0, 7), "tarde": (7, 13), "noche": (13, N_BLOCKS)}   # bloques [a, b)
# replay de Ecobici: brazo → (when, variant, undo) de sim.ecobici_orders
REPLAY = {
    "ecobici": ("t0", "rebal", False),
    "ecobici_t1": ("t1", "rebal", False),
    "ecobici_undo": ("t0", "rebal", True),
    "ecobici_stock": ("t0", "stock", None),       # método del run 1 (va siempre con dañadas fijas)
}
V1_ARMS = [("baseline", "auto"), ("baseline", "fixed"), ("ecobici", "auto"), ("ecobici_t1", "auto"),
           ("ecobici_undo", "auto"), ("ecobici_stock", "fixed")]
MAIN_REPLAY = "ecobici|auto"
TIME_COLS = ["seconds", "decision_s_mean", "decision_s_max"]
WALK_COLS = ["walk_m_dep", "walk_m_arr"]
KEY_COLS = ["split", "tag", "arm", "damage", "f", "H", "L", "lam", "mu", "tope_hora", "tope_bodega",
            "max_move", "retiro"]


# ======================================================================
# Insumos
# ======================================================================

def eval_days() -> list[str]:
    return load_days("evaluacion")


def sel_days() -> list[str]:
    return load_days("seleccion")


def stats() -> dict:
    return json.loads(STATS_JSON.read_text())


def base_params() -> dict:
    """Topes de medición (hora corregida, sin pares ±1): enteros publicados
    por `medicion` (`tope_hora` = p95 por ventana de 60 min, `tope_bodega` =
    ⌈|A − R| medio del rebalanceo⌉), y 22 bicis por movimiento."""
    s = stats()
    return {"tope_hora": int(s["tope_hora"]), "tope_bodega": int(s["tope_bodega"]),
            "max_move": int(C.MAX_BIKES_PER_MOVE)}


def tope_bodega_taller() -> int:
    """Sensibilidad: bodega con el taller dentro (|A − R| medio de `stock`,
    ≈ 74), redondeada hacia arriba como `tope_bodega`."""
    return int(math.ceil(stats()["mean_abs_stock_warehouse"]))


@lru_cache(maxsize=4)
def day_inputs(day: str):
    d = C.as_date(day)
    return data.initial_state(d), data.trips(d), data.neighbors(d)


@lru_cache(maxsize=4)
def damage(day: str, mode: str):
    return sim.load_damage_events(C.as_date(day), mode)[0]


@lru_cache(maxsize=8)
def forecast(day: str, variant: str, f: int) -> pd.DataFrame:
    p = P.forecast_path(day, variant, f)
    if not p.exists():
        raise FileNotFoundError(f"{p}: corre `uv run python -m ecosim.pronostico`")
    return pd.read_parquet(p)


def forecast_horizon_h() -> int:
    """h máximo que cubren las series re-emitidas (`horizon_h`)."""
    d = sel_days()[0]
    return int(min(forecast(d, v, F_H_SEARCH)["horizon_h"].min() for v in ("oracle", "ma", "model")))


# ======================================================================
# Una corrida
# ======================================================================

class _Recorder:
    """Envuelve al Asignador y guarda el estado de HiGHS y el tiempo por decisión."""

    def __init__(self, a: Asignador):
        self.a, self.status, self.secs = a, [], []

    def decide(self, t, state, pending, fc, params):
        out = self.a.decide(t, state, pending, fc, params)
        p = self.a.last_plan
        if p is not None:
            self.status.append(p.status)
            self.secs.append(p.total_s)
        return out


def station_blocks(res: K.DayResult, init: pd.DataFrame) -> tuple[list, np.ndarray, np.ndarray]:
    """E y F por bloque de 60 min anclado a las 05:30 (0..18) y estación:
    (estaciones, E[bloque, estación], F[bloque, estación]). Llena = sin
    anclaje libre para una disponible: bicis ≥ cap − dañadas(minuto) −
    anclajes deshabilitados (las dañadas cambian durante el día)."""
    bm = res.extra["bikes_minute"]                      # (1140, n)
    dm = res.extra["disabled_minute"]
    names = list(res.extra["stations"])
    ini = init.set_index("short_name").loc[names]
    room = (ini["cap"] - ini["docks_disabled"]).to_numpy()[None, :] - dm
    empty = (bm == 0).reshape(N_BLOCKS, 60, -1).sum(axis=1)
    full = (bm >= room).reshape(N_BLOCKS, 60, -1).sum(axis=1)
    assert int(empty.sum()) == res.metrics["E"] and int(full.sum()) == res.metrics["F"]
    return names, empty, full


def _blocks_long(run: str, names, empty, full) -> pd.DataFrame:
    return pd.DataFrame({
        "run": run,
        "short_name": np.tile(names, N_BLOCKS),
        "block": np.repeat(np.arange(N_BLOCKS), len(names)).astype("int16"),
        "E": empty.ravel().astype("int32"), "F": full.ravel().astype("int32"),
    })


def config_id(s: dict) -> str:
    """Llave de una configuración (sin el día)."""
    if s["arm"] == "baseline" or s["arm"] in REPLAY:
        return f"{s['tag']}|{s['arm']}|{s['damage']}"
    return (f"{s['tag']}|{s['arm']}|f{int(s['f'])}|H{int(s['H'])}|L{int(s['L'])}|lam{float(s['lam']):g}"
            f"|mu{float(s['mu']):g}|th{int(s['tope_hora'])}|tb{int(s['tope_bodega'])}|mm{int(s['max_move'])}"
            f"|{s['retiro']}|{s['damage']}")


def spec_id(s: dict) -> str:
    return f"{config_id(s)}|{s['day']}"


def _pspec(day, split, tag, arm, f, H, L, lam, mu, tope_hora, tope_bodega, max_move, retiro,
           damage="auto") -> dict:
    return dict(day=day, split=split, tag=tag, arm=arm, damage=damage, f=int(f), H=int(H), L=int(L),
                lam=float(lam), mu=float(mu), tope_hora=int(tope_hora), tope_bodega=int(tope_bodega),
                max_move=int(max_move), retiro=retiro)


def _fixed(day, split, tag, arm, damage="auto") -> dict:
    if arm in REPLAY and REPLAY[arm][1] != "rebal":
        damage = sim.REPLAY_DAMAGE[REPLAY[arm][1]]    # stock/avail: siempre dañadas fijas
    return dict(day=day, split=split, tag=tag, arm=arm, damage=damage, f=np.nan, H=np.nan, L=np.nan,
                lam=np.nan, mu=np.nan, tope_hora=np.nan, tope_bodega=np.nan, max_move=np.nan, retiro="")


def check_optimal(row: dict, statuses: dict | None = None):
    """Una corrida de política con alguna decisión no óptima (HiGHS cortado por
    tiempo) no es reproducible: error, no se guarda."""
    if row.get("non_optimal", 0):
        raise RuntimeError(f"{row['run']}: {row['non_optimal']} decisiones no óptimas {statuses or ''} "
                           f"(¿límite de tiempo de HiGHS o la máquina se durmió?); vuelve a correr la etapa")


def pool(jobs: int) -> ProcessPoolExecutor:
    """Pool de procesos con 1 thread de BLAS/OpenMP/Polars cada uno (los hijos
    heredan el entorno al arrancar)."""
    import os
    for k in CHILD_THREAD_ENV:
        os.environ[k] = "1"
    return ProcessPoolExecutor(max_workers=jobs)


def make_policy(retiro: str) -> Asignador:
    """El asignador de todas las corridas: 1 thread y sin corte por tiempo en la práctica."""
    return Asignador(threads=HIGHS_THREADS, retiro=retiro, time_limit=HIGHS_TIME_LIMIT)


def run_one(spec: dict, record_at=None, keep_result=False, blocks=False):
    """Corre un (día, brazo, configuración). Devuelve (renglón, bloques | None[, DayResult])."""
    warnings.simplefilter("ignore", UserWarning)   # los fallbacks se cuentan aparte
    day, arm = spec["day"], spec["arm"]
    init, trips, nbrs = day_inputs(day)
    start, end = C.day_bounds(day)
    ev = damage(day, spec["damage"])
    t0 = _time.perf_counter()
    rec = None
    kw = dict(record_at=record_at, day=day, arm=arm, damage_events=ev)
    if arm == "baseline":
        res = sim.simulate(init, trips, nbrs, start, end, **kw)
    elif arm in REPLAY:
        when, variant, undo = REPLAY[arm]
        if spec["damage"] != sim.REPLAY_DAMAGE[variant] and variant != "rebal":
            raise ValueError(f"{arm}: el replay `{variant}` va con dañadas {sim.REPLAY_DAMAGE[variant]}")
        res = sim.simulate(init, trips, nbrs, start, end,
                           orders=sim.ecobici_orders(day, when=when, variant=variant, undo=undo), **kw)
    else:
        f = 0 if arm == "daily" else int(spec["f"])
        params = K.PolicyParams(
            lead_min=int(spec["L"]), H_horas=int(spec["H"]), lam=float(spec["lam"]), mu=float(spec["mu"]),
            tope_hora=int(spec["tope_hora"]), tope_bodega=int(spec["tope_bodega"]), block_min=60,
            max_bikes_per_move=int(spec["max_move"]), refresh_min=f, extra={"variant": arm})
        rec = _Recorder(make_policy(spec["retiro"]))
        res = sim.simulate(init, trips, nbrs, start, end, policy=rec, lead_min=int(spec["L"]),
                           forecast=forecast(day, arm, f), params=params, **kw)
    secs = _time.perf_counter() - t0
    m, x = res.metrics, res.extra
    oos = init.set_index("short_name").loc[list(x["stations"]), "out_of_service"].to_numpy(bool)
    names, empty, full = station_blocks(res, init)
    row = {"run": spec_id(spec), **spec,
           "E": m["E"] - x["E_out_of_service"], "F": m["F"] - x["F_out_of_service"],
           "E_all": m["E"], "F_all": m["F"]}
    row["EF"] = row["E"] + row["F"]
    oa = x["orders_applied"]
    oa = oa[oa["applied"] != 0]
    blk = ((pd.to_datetime(oa["effective_at"]) - pd.Timestamp(start)) // pd.Timedelta(minutes=60)).to_numpy()
    for fr, (a, b) in FRANJAS.items():
        row[f"E_{fr}"] = int(empty[a:b, ~oos].sum())
        row[f"F_{fr}"] = int(full[a:b, ~oos].sum())
        row[f"EF_{fr}"] = row[f"E_{fr}"] + row[f"F_{fr}"]
        row[f"moves_{fr}"] = int(((blk >= a) & (blk < b)).sum())
    assert sum(row[f"EF_{fr}"] for fr in FRANJAS) == row["EF"]
    assert sum(row[f"moves_{fr}"] for fr in FRANJAS) == m["moves"]
    row.update({k: m[k] for k in K.DAY_METRICS if k not in ("E", "F")})
    # metros caminados: sumas de flotantes que pueden variar en el último bit
    # entre corridas (orden de la suma en numpy); a milímetros son deterministas
    for k in WALK_COLS:
        row[k] = round(float(m[k]), 3)
    row.update({
        "bikes_moved": m["A"] + m["R"],
        "detours_dep_far": x["detours_dep_far"], "dep_failed": x["dep_failed"], "arr_failed": x["arr_failed"],
        "reparaciones_no_aplicables": x["reparaciones_no_aplicables"],
        "recorte_fuera_de_servicio": x["recorte_fuera_de_servicio"],
        "bodega_min": x["bodega_min"], "bodega_max": x["bodega_max"],
        "n_orders": int(len(x["orders_applied"])), "seconds": round(secs, 2),
    })
    if rec is not None:
        st = pd.Series(rec.status, dtype=object)
        row.update({
            "fallbacks": len(rec.a.fallbacks), "n_decisions": len(st),
            "non_optimal": int((~st.isin(["Optimal", "noop"])).sum()),
            "decision_s_mean": round(float(np.mean(rec.secs)), 3) if rec.secs else 0.0,
            "decision_s_max": round(float(np.max(rec.secs)), 3) if rec.secs else 0.0,
        })
        check_optimal(row, st.value_counts().to_dict())
    sb = _blocks_long(row["run"], names, empty, full) if blocks else None
    return (row, sb, res) if keep_result else (row, sb)


def _run_one_star(a):
    spec, blocks = a
    return run_one(spec, blocks=blocks)


# ======================================================================
# Persistencia: resultados.csv es la unión de todas las corridas (llave `run`)
# ======================================================================

def load_results() -> pd.DataFrame:
    if not RESULTADOS_CSV.exists():
        return pd.DataFrame(columns=["run"])
    return pd.read_csv(RESULTADOS_CSV, dtype={"day": str, "retiro": str, "run": str},
                       keep_default_na=True).assign(retiro=lambda d: d["retiro"].fillna(""))


def _upsert(rows: pd.DataFrame, sb: pd.DataFrame | None):
    old = load_results()
    if len(old):
        old = old[~old["run"].isin(rows["run"])]
    out = pd.concat([old, rows], ignore_index=True) if len(old) else rows
    out = out.sort_values(KEY_COLS + ["day"], na_position="first", kind="stable")
    out.to_csv(RESULTADOS_CSV, index=False)
    if sb is not None and len(sb):
        oldb = pd.read_parquet(BLOCKS_PARQUET) if BLOCKS_PARQUET.exists() else None
        if oldb is not None:
            oldb = oldb[~oldb["run"].isin(sb["run"].unique())]
        pd.concat([x for x in (oldb, sb) if x is not None], ignore_index=True).to_parquet(
            BLOCKS_PARQUET, index=False)


def run_many(specs: list[dict], jobs: int = JOBS, label: str = "", blocks: bool = False) -> pd.DataFrame:
    """Corre las corridas que falten (las ya guardadas se reutilizan por su
    llave) y devuelve los renglones de todas, en el orden de `specs`."""
    ids = [spec_id(s) for s in specs]
    if len(set(ids)) != len(ids):
        raise ValueError(f"[{label}] corridas duplicadas")
    done = set(load_results()["run"])
    todo = [s for s, i in zip(specs, ids) if i not in done]
    t = _time.perf_counter()
    print(f"[{label}] {len(specs)} corridas ({len(specs) - len(todo)} ya guardadas)", flush=True)
    ex = pool(jobs) if jobs > 1 and len(todo) > 1 else None
    try:
        for c in range(0, len(todo), CHUNK):
            chunk = todo[c:c + CHUNK]
            args = [(s, blocks) for s in chunk]
            outs = list(ex.map(_run_one_star, args, chunksize=1)) if ex else [_run_one_star(a) for a in args]
            rows = pd.DataFrame([o[0] for o in outs])
            sb = pd.concat([o[1] for o in outs], ignore_index=True) if blocks else None
            _upsert(rows, sb)
            print(f"[{label}] {c + len(chunk)}/{len(todo)} nuevas, {(_time.perf_counter() - t) / 60:.1f} min",
                  flush=True)
    finally:
        if ex:
            ex.shutdown()
    res = load_results().set_index("run")
    return res.loc[ids].reset_index()


# ======================================================================
# 1. V1: simulador vs GBFS observado (días de evaluación)
# ======================================================================

def _snap_state(day: str) -> pd.DataFrame:
    """Snapshots con la hora del estado del feed (commit − 30 s), como
    medición; sin los de commit ≥ 00:30 (igual que `medicion.observed_ef`)."""
    s = medicion.state_time(data.snapshots(day))
    s = s[s["t_commit"] < pd.Timestamp(C.day_bounds(day)[1])]
    return s.sort_values(["short_name", "t"]).reset_index(drop=True)


def _segments(s: pd.DataFrame, day: str) -> pd.DataFrame:
    """Escalón entre snapshots consecutivos por estación: [t_k, t_{k+1}) recortado a la ventana."""
    start, end = (pd.Timestamp(x) for x in C.day_bounds(day))
    t1 = s.groupby("short_name")["t"].shift(-1).fillna(end)
    return s.assign(a=s["t"].clip(lower=start, upper=end), b=t1.clip(lower=start, upper=end))


def _block_minutes(seg: pd.DataFrame, flag: pd.Series, day: str) -> pd.DataFrame:
    """Minutos con `flag` por estación y bloque de 60 min (tiempo continuo)."""
    start = pd.Timestamp(C.day_bounds(day)[0])
    a = ((seg["a"] - start) / pd.Timedelta(minutes=1)).to_numpy()
    b = ((seg["b"] - start) / pd.Timedelta(minutes=1)).to_numpy()
    fl = flag.to_numpy()
    out = []
    for k in range(N_BLOCKS):
        ov = np.clip(np.minimum(b, 60 * (k + 1)) - np.maximum(a, 60 * k), 0, None) * fl
        out.append(pd.DataFrame({"short_name": seg["short_name"].to_numpy(), "block": k, "m": ov}))
    return pd.concat(out).groupby(["short_name", "block"], as_index=False)["m"].sum()


def _franja(block):
    b = np.asarray(block)
    return np.select([b < FRANJAS["manana"][1], b < FRANJAS["tarde"][1]], ["manana", "tarde"], "noche")


def v1_label(arm: str, dmg: str) -> str:
    return f"{arm}|{dmg}"


def v1_day(day: str) -> tuple[list[dict], list[pd.DataFrame]]:
    """Brazos de V1 de un día con registro del stock en cada snapshot real."""
    init = day_inputs(day)[0]
    oos = set(init.loc[init["out_of_service"], "short_name"])
    snaps = _snap_state(day)
    start, end = (pd.Timestamp(x) for x in C.day_bounds(day))
    seg = _segments(snaps, day)
    seg = seg[~seg["short_name"].isin(oos)]
    ok = ~seg["blank"]
    # observado por estación × bloque (escalón GBFS; los blancos no cuentan)
    obs = (_block_minutes(seg, ok & seg["bikes"].eq(0), day).rename(columns={"m": "E_obs"})
           .merge(_block_minutes(seg, ok & seg["docks"].eq(0), day).rename(columns={"m": "F_obs"}),
                  on=["short_name", "block"])
           .merge(_block_minutes(seg, ok & seg["bikes"].eq(0) & seg["disabled"].gt(0), day)
                  .rename(columns={"m": "E_obs_con_danadas"}), on=["short_name", "block"]))
    obs["franja"] = _franja(obs["block"])
    obs_fr = obs.groupby("franja")[["E_obs", "F_obs"]].sum()
    w = snaps[(snaps["t"] >= start) & (snaps["t"] < end) & ~snaps["blank"] & ~snaps["short_name"].isin(oos)]
    dis = w.groupby("t")["disabled"].sum()
    dis_info = {"danadas_0530": int(init.loc[~init["out_of_service"], "disabled"].sum()),
                "danadas_obs_max": int(dis.max()), "danadas_obs_fin": int(dis.iloc[-1]),
                "E_obs_con_danadas": float(obs["E_obs_con_danadas"].sum())}
    seg = seg.assign(tr=seg["t"].clip(lower=start))
    seg = seg[seg["a"] < seg["b"]]
    rec_times = sorted(seg["tr"].unique())
    rows, per = [], []
    for arm, dmg in V1_ARMS:
        row, _, res = run_one(_fixed(day, "evaluacion", "v1", arm, dmg), record_at=rec_times, keep_result=True)
        names = list(res.extra["stations"])
        rt = pd.DatetimeIndex(res.extra["record_times"])
        simb = pd.DataFrame(res.extra["bikes_record"], index=rt, columns=names).stack().rename("bikes_sim")
        simd = pd.DataFrame(res.extra["disabled_record"], index=rt, columns=names).stack().rename("dis_sim")
        sm = pd.concat([simb, simd], axis=1).reset_index()
        sm.columns = ["tr", "short_name", "bikes_sim", "dis_sim"]
        g = seg[["short_name", "t", "tr", "bikes", "disabled", "blank"]].merge(sm, on=["short_name", "tr"])
        inw = ~g["blank"] & (g["t"] >= start) & (g["t"] < end)
        err = g.loc[inw, "bikes_sim"] - g.loc[inw, "bikes"]
        derr = g.loc[inw, "dis_sim"] - g.loc[inw, "disabled"]
        _, empty, full = station_blocks(res, init)
        simt = pd.DataFrame({"short_name": np.tile(names, N_BLOCKS),
                             "block": np.repeat(np.arange(N_BLOCKS), len(names)),
                             "E": empty.ravel(), "F": full.ravel()})
        simt = simt[~simt["short_name"].isin(oos)]
        row.update({
            "label": v1_label(arm, dmg),
            "E_obs": float(obs["E_obs"].sum()), "F_obs": float(obs["F_obs"].sum()),
            **{f"{c}_obs_{fr}": float(obs_fr.loc[fr, f"{c}_obs"]) for fr in FRANJAS for c in "EF"},
            "stock_mae": float(err.abs().mean()), "stock_bias": float(err.mean()),
            "stock_exact": float((err == 0).mean()), "n_station_snapshots": int(inw.sum()),
            "danadas_mae": float(derr.abs().mean()), "danadas_bias": float(derr.mean()),
            "danadas_sim_fin": int(res.extra["disabled_minute"][-1][
                ~init.set_index("short_name").loc[names, "out_of_service"].to_numpy(bool)].sum()),
            **dis_info,
        })
        rows.append(row)
        e = g.loc[inw].assign(err=err, block=((g.loc[inw, "t"] - start) // pd.Timedelta(minutes=60)).astype(int))
        mae = e.groupby(["short_name", "block"], as_index=False).agg(
            stock_err_mean=("err", "mean"), stock_abs_err=("err", lambda v: v.abs().mean()))
        p = simt.merge(obs, on=["short_name", "block"], how="left").merge(mae, on=["short_name", "block"], how="left")
        p.insert(0, "label", v1_label(arm, dmg))
        p.insert(0, "day", day)
        per.append(p)
    return rows, per


def stage_v1(jobs=JOBS) -> pd.DataFrame:
    V1_DIR.mkdir(parents=True, exist_ok=True)
    days = eval_days()
    rows, per = [], []
    ex = pool(min(jobs, len(days))) if jobs > 1 else None
    outs = ex.map(v1_day, days) if ex else map(v1_day, days)
    for k, (r, p) in enumerate(outs, 1):
        rows += r
        per += p
        print(f"[v1] {k}/{len(days)} días", flush=True)
    if ex:
        ex.shutdown()
    df = pd.DataFrame(rows)
    obs_csv = pd.read_csv(OBS_CSV, dtype={"day": str}).set_index("day")
    for c in ["E", "F"]:
        df[f"rel_{c}"] = (df[c] - df[f"{c}_obs"]) / df[f"{c}_obs"]
        for fr in FRANJAS:
            df[f"rel_{c}_{fr}"] = (df[f"{c}_{fr}"] - df[f"{c}_obs_{fr}"]) / df[f"{c}_obs_{fr}"]
        # control: lo observado + fuera de servicio = `ecobici_observado.csv` (inclusivo)
        df[f"{c}_obs_medicion_csv"] = df["day"].map(obs_csv[c])
    df = df.drop(columns=[c for c in TIME_COLS if c in df]).sort_values(["label", "day"], kind="stable")
    df.to_csv(V1_DIR / "v1_por_dia.csv", index=False)
    per = pd.concat(per, ignore_index=True)
    per.to_parquet(V1_BLOCKS_PARQUET, index=False)
    v1_diag(per)
    s = v1_summary(df)
    (V1_DIR / "v1_resumen.json").write_text(json.dumps(s, indent=2, ensure_ascii=False))
    print(json.dumps(s["criterio"], indent=2, ensure_ascii=False))
    return df


def v1_diag(per: pd.DataFrame):
    """Dónde se desvía el replay principal de lo observado: estaciones, bloques y franjas (suma de 15 días)."""
    e = per[per["label"] == MAIN_REPLAY].copy()
    e["dE"] = e["E"] - e["E_obs"]
    e["dF"] = e["F"] - e["F_obs"]
    sums = ["E", "E_obs", "E_obs_con_danadas", "F", "F_obs", "dE", "dF"]
    st = e.groupby("short_name").agg(**{c: (c, "sum") for c in sums}, stock_err_mean=("stock_err_mean", "mean"))
    st["abs_dEF"] = st["dE"].abs() + st["dF"].abs()
    st.sort_values("abs_dEF", ascending=False).head(20).reset_index().to_csv(
        V1_DIR / "v1_diag_estaciones.csv", index=False)
    n = e["day"].nunique()
    bl = e.groupby("block").agg(**{c: (c, "sum") for c in sums}, stock_abs_err=("stock_abs_err", "mean"),
                                stock_err_mean=("stock_err_mean", "mean"))
    bl[sums] = bl[sums] / n          # minutos por día
    bl.insert(0, "hora", [f"{(5 * 60 + 30 + 60 * b) // 60 % 24:02d}:30" for b in bl.index])
    bl.reset_index().to_csv(V1_DIR / "v1_diag_bloques.csv", index=False)


def v1_summary(df: pd.DataFrame) -> dict:
    out = {}
    for lab, g in df.groupby("label", sort=False):
        o = {"E_sim": g["E"].mean(), "F_sim": g["F"].mean(), "E_obs": g["E_obs"].mean(), "F_obs": g["F_obs"].mean(),
             "mean_abs_rel_E": g["rel_E"].abs().mean(), "mean_abs_rel_F": g["rel_F"].abs().mean(),
             "mean_rel_E": g["rel_E"].mean(), "mean_rel_F": g["rel_F"].mean()}
        for fr in FRANJAS:
            for c in "EF":
                o[f"{c}_sim_{fr}"] = g[f"{c}_{fr}"].mean()
                o[f"{c}_obs_{fr}"] = g[f"{c}_obs_{fr}"].mean()
                o[f"mean_abs_rel_{c}_{fr}"] = g[f"rel_{c}_{fr}"].abs().mean()
                o[f"mean_rel_{c}_{fr}"] = g[f"rel_{c}_{fr}"].mean()
        o.update({k: g[k].mean() for k in [
            "stock_mae", "stock_bias", "stock_exact", "danadas_mae", "danadas_bias", "danadas_sim_fin",
            "danadas_obs_fin", "danadas_obs_max", "danadas_0530", "E_obs_con_danadas",
            "detours_dep", "detours_arr", "detours_dep_far", "trips_served", "clipped", "moves",
            "A", "R", "danos_aplicados", "danos_no_aplicables", "taller_aplicado", "taller_no_aplicable",
            "reparaciones_no_aplicables"]})
        o["dias_E_abajo"] = int((g["rel_E"] < 0).sum())
        out[lab] = o
    e = out[MAIN_REPLAY]
    crit = {"umbral": V1_OK, "brazo": MAIN_REPLAY,
            "E_ok": bool(e["mean_abs_rel_E"] <= V1_OK), "F_ok": bool(e["mean_abs_rel_F"] <= V1_OK)}
    for fr in FRANJAS:
        crit[f"{fr}_ok"] = bool(max(e[f"mean_abs_rel_E_{fr}"], e[f"mean_abs_rel_F_{fr}"]) <= V1_OK)
    crit["grave"] = bool(max(e["mean_abs_rel_E"], e["mean_abs_rel_F"]) > V1_STOP)
    out["criterio"] = crit
    return out


# ======================================================================
# 2. Cota de retiro y λ con el oracle (selección) → frozen.json
# ======================================================================

def kneedle_lambda(curve: pd.DataFrame) -> tuple[float, dict]:
    """Codo (Kneedle) de E+F contra movimientos (medias por λ). λ que dan el
    mismo número de movimientos son un solo punto: gana el menor λ."""
    from kneed import KneeLocator
    g = curve.sort_values(["moves", "lam"]).drop_duplicates("moves")
    kl = KneeLocator(g["moves"].to_numpy(float), g["EF"].to_numpy(float), curve="convex", direction="decreasing")
    if kl.knee is None:
        raise RuntimeError(f"Kneedle no encontró codo en la curva λ:\n{g}")
    k = g[g["moves"] == kl.knee].iloc[0]
    return float(k["lam"]), {"curve": "convex", "direction": "decreasing", "x": "movimientos (media por día)",
                             "y": "E+F sin fuera de servicio (media por día)",
                             "knee_moves": float(k["moves"]), "knee_EF": float(k["EF"])}


def choose_retiro(rows: pd.DataFrame) -> tuple[str, dict]:
    """Cota de retiro con menor E+F medio en toda la rejilla λ × día (regla
    fijada antes de ver resultados). También cuenta en cuántos (λ, día) gana."""
    m = rows.groupby("retiro")["EF"].mean()
    pick = str(m.idxmin())
    pv = rows.pivot_table(index=["lam", "day"], columns="retiro", values="EF")
    other = [r for r in RETIROS if r != pick][0]
    return pick, {"regla": "menor E+F medio sobre λ ∈ LAMBDA_GRID × días de selección (oracle, f60, H2, L60)",
                  "EF_medio": {k: float(v) for k, v in m.items()},
                  "gana_en": int((pv[pick] < pv[other]).sum()), "empata_en": int((pv[pick] == pv[other]).sum()),
                  "de": int(len(pv))}


def stage_lambda(jobs=JOBS) -> dict:
    bp = base_params()
    specs = [_pspec(d, "seleccion", "sel_lambda", "oracle", F_ORACLE, H_LAMBDA, L_MAIN, lam, MU,
                    bp["tope_hora"], bp["tope_bodega"], bp["max_move"], r)
             for r in RETIROS for lam in LAMBDA_GRID for d in sel_days()]
    rows = run_many(specs, jobs, "lambda", blocks=False)
    curve = rows.groupby(["retiro", "lam"], as_index=False)[["EF", "E", "F", "moves", "bikes_moved",
                                                            "recorte_bodega"]].mean()
    curve.to_csv(RESULTS / "lambda_grid.csv", index=False)
    retiro, rinfo = choose_retiro(rows)
    lam, knee = kneedle_lambda(curve[curve["retiro"] == retiro])
    other = {}
    for r in RETIROS:
        if r != retiro:
            try:
                other[r] = kneedle_lambda(curve[curve["retiro"] == r])[0]
            except RuntimeError:
                other[r] = None
    _plot_lambda(curve, retiro, lam)
    s = stats()
    fz = {
        "dias_seleccion": sel_days(),
        "lam": lam, "mu": MU, "retiro": retiro, "L": L_MAIN, **bp,
        "fuente_topes": "ecosim/results/medicion/ecobici_stats.json: tope_hora (p95 sin ±1, hora corregida), "
                        "tope_bodega (⌈|A − R| medio del rebalanceo sin taller⌉); max_move = config.MAX_BIKES_PER_MOVE",
        "tope_hora_con_undo": int(s["tope_hora_con_undo"]), "tope_bodega_taller": tope_bodega_taller(),
        "lambda_elegido_con": {"arm": "oracle", "f": F_ORACLE, "H": H_LAMBDA, "L": L_MAIN, "dias": "seleccion"},
        "lambda_grid": LAMBDA_GRID, "kneedle": knee, "lambda_otra_cota": other,
        "retiro_eleccion": rinfo,
        "curva": curve.to_dict(orient="records"),
        "highs_threads": HIGHS_THREADS, "highs_time_limit_s": HIGHS_TIME_LIMIT,
    }
    _write_frozen(fz)
    print(json.dumps({k: fz[k] for k in ["lam", "retiro", "tope_hora", "tope_bodega", "max_move"]}))
    return fz


def _write_frozen(fz: dict):
    FROZEN_JSON.write_text(json.dumps(fz, indent=2, ensure_ascii=False, default=float))


def frozen() -> dict:
    if not FROZEN_JSON.exists():
        raise FileNotFoundError("falta results/frozen.json: corre `--stage lambda` primero")
    return json.loads(FROZEN_JSON.read_text())


def _plot_lambda(curve: pd.DataFrame, retiro: str, lam: float):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    for r, color in zip(RETIROS, ["#2a6f97", "#8a5a44"]):
        g = curve[curve["retiro"] == r].sort_values("lam")
        ax.plot(g["moves"], g["EF"], "o-", color=color, label=f"retiro {r}")
        for _, x in g.iterrows():
            ax.annotate(f"λ={x['lam']:g}", (x["moves"], x["EF"]), textcoords="offset points", xytext=(5, 4),
                        fontsize=7, color=color)
    k = curve[(curve["retiro"] == retiro) & (curve["lam"] == lam)].iloc[0]
    ax.plot([k["moves"]], [k["EF"]], "o", ms=12, mfc="none", mec="#c1121f", mew=2)
    ax.set_xlabel("movimientos por día (media, 15 días de selección)")
    ax.set_ylabel("E + F (minutos-estación por día)")
    ax.set_title(f"Oracle, f60, H=2, L=60 — retiro {retiro}, codo Kneedle: λ = {lam:g}")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(RESULTS / "lambda_codo.png", dpi=120)
    plt.close(fig)


def _policy(fz, day, split, tag, arm, f, H, L=L_MAIN, **over) -> dict:
    p = dict(lam=fz["lam"], mu=fz["mu"], tope_hora=fz["tope_hora"], tope_bodega=fz["tope_bodega"],
             max_move=fz["max_move"], retiro=fz["retiro"], damage="auto")
    p.update(over)
    return _pspec(day, split, tag, arm, f, H, L, p["lam"], p["mu"], p["tope_hora"], p["tope_bodega"],
                  p["max_move"], p["retiro"], p["damage"])


# ======================================================================
# 3. Búsqueda de h (selección)
# ======================================================================

def h_stop(ef_by_h: dict) -> int | None:
    """Primer h (≥ 2) cuyo E+F no mejora ≥ H_MIN_GAIN sobre h − 1; None si todavía mejora."""
    for h in sorted(ef_by_h):
        if h - 1 in ef_by_h and ef_by_h[h - 1] - ef_by_h[h] < H_MIN_GAIN * ef_by_h[h - 1]:
            return h
    return None


def _h_spec(fz, arm, h, day):
    f = F_ORACLE if arm == "oracle" else F_H_SEARCH
    return _policy(fz, day, "seleccion", "sel_h", arm, f, h)


def stage_h(jobs=JOBS) -> dict:
    fz = frozen()
    days = sel_days()
    cap = forecast_horizon_h()
    ef = {a: {} for a in H_ARMS}
    stop: dict = {}
    h = 1
    while len(stop) < len(H_ARMS):
        active = [a for a in H_ARMS if a not in stop]
        if h > cap:
            raise RuntimeError(f"h = {h} sigue mejorando en {active} y los pronósticos cubren h ≤ {cap}: "
                               "regenerar pronósticos con H_MAX_H mayor (pronostico.py)")
        rows = run_many([_h_spec(fz, a, h, d) for a in active for d in days], jobs, f"h={h}")
        for a in active:
            ef[a][h] = float(rows.loc[rows["arm"] == a, "EF"].mean())
            s = h_stop(ef[a])
            if s is not None:
                stop[a] = s
        h += 1
    h_max = max(stop.values())
    extra = [_h_spec(fz, a, hh, d) for a in H_ARMS for hh in range(1, h_max + 1)
             if hh not in ef[a] for d in days]
    if extra:
        rows = run_many(extra, jobs, "h completar")
        for a in H_ARMS:
            for hh, g in rows[rows["arm"] == a].groupby("H"):
                ef[a][int(hh)] = float(g["EF"].mean())
    fz["h_busqueda"] = {"regla": f"primer h que no mejora E+F ≥ {H_MIN_GAIN:.0%} sobre h − 1 (oracle y ma f60)",
                        "EF": {a: {str(k): v for k, v in sorted(ef[a].items())} for a in H_ARMS},
                        "para_en": stop, "cobertura_pronostico_h": cap}
    fz["h_max"] = int(h_max)
    _write_frozen(fz)
    print(json.dumps(fz["h_busqueda"], indent=2))
    return fz


# ======================================================================
# 4. Grid de (f, h) por variante (selección)
# ======================================================================

def grid_combos(h_max: int, L: int = L_MAIN) -> list[tuple[str, int, int]]:
    """(brazo, f, h) del grid: ma/model × f × h sin f > 60·h + L; daily × h (f = 0)."""
    out = [(a, f, h) for a in GRID_ARMS for f in F_GRID for h in range(1, h_max + 1) if f <= 60 * h + L]
    return out + [("daily", 0, h) for h in range(1, h_max + 1)]


def _grid_spec(fz, arm, f, h, day):
    # `ma` f60 ya corrió en la búsqueda de h: misma corrida (misma llave)
    tag = "sel_h" if (arm, f) == ("ma", F_H_SEARCH) else "sel_grid"
    return _policy(fz, day, "seleccion", tag, arm, f, h)


def best_configs(table: pd.DataFrame) -> pd.DataFrame:
    """Mejor (f, H) por brazo: menor E+F medio; empates → menor H, luego mayor f."""
    t = table.assign(_nf=-table["f"])
    return (t.sort_values(["arm", "EF", "H", "_nf"]).drop_duplicates("arm").drop(columns="_nf")
            .reset_index(drop=True))


def stage_grid(jobs=JOBS) -> dict:
    fz = frozen()
    days = sel_days()
    combos = grid_combos(fz["h_max"])
    rows = run_many([_grid_spec(fz, a, f, h, d) for a, f, h in combos for d in days], jobs, "grid")
    orc = run_many([_h_spec(fz, "oracle", h, d) for h in range(1, fz["h_max"] + 1) for d in days], jobs, "oracle h")
    allr = pd.concat([rows, orc], ignore_index=True)
    table = allr.groupby(["arm", "f", "H"], as_index=False)[["EF", "E", "F", "moves", "bikes_moved",
                                                            "recorte_bodega", "fallbacks"]].mean()
    table.to_csv(RESULTS / "grid_seleccion.csv", index=False)
    best = best_configs(table)
    fz["grid"] = {"combos": len(combos), "tabla": "results/grid_seleccion.csv"}
    fz["best"] = {r["arm"]: {"f": int(r["f"]), "H": int(r["H"]), "EF_seleccion": float(r["EF"])}
                  for _, r in best.iterrows()}
    real = best[best["arm"].isin(REAL_ARMS)].sort_values("EF").iloc[0]
    fz["best_real"] = str(real["arm"])
    _write_frozen(fz)
    print(json.dumps({"best": fz["best"], "best_real": fz["best_real"]}, indent=2))
    return fz


# ======================================================================
# 5. Evaluación con todo congelado
# ======================================================================

EVAL_FIXED = [("baseline", "auto"), ("baseline", "fixed"), ("ecobici", "auto"), ("ecobici_stock", "fixed")]


def _eval_spec(fz, arm, day, tag="eval", L=L_MAIN, **over):
    b = fz["best"][arm]
    return _policy(fz, day, "evaluacion", tag, arm, b["f"], b["H"], L, **over)


def stage_eval(jobs=JOBS):
    fz = frozen()
    days = eval_days()
    specs = [_fixed(d, "evaluacion", "eval", a, dm) for a, dm in EVAL_FIXED for d in days]
    specs += [_eval_spec(fz, a, d) for a in POLICY_ARMS for d in days]
    run_many(specs, jobs, "eval L=60", blocks=True)
    specs = [_eval_spec(fz, a, d, "eval_L", L) for a in L_GRID_ARMS for L in L_SENS for d in days]
    run_many(specs, jobs, "eval L=45,30")


# ======================================================================
# 6. Sensibilidades (evaluación): oracle y el mejor brazo real
# ======================================================================

def lam_min_seleccion(fz: dict) -> float:
    """λ de la rejilla con menor E+F medio en selección (oracle, cota congelada):
    alternativa a Kneedle, también fuera de muestra; se reporta como sensibilidad."""
    c = pd.DataFrame(fz["curva"])
    c = c[c["retiro"] == fz["retiro"]].sort_values(["EF", "lam"])
    return float(c["lam"].iloc[0])


def sens_variants(fz: dict) -> list[tuple[str, dict]]:
    other = [r for r in RETIROS if r != fz["retiro"]][0]
    return [
        ("sens_lam_min_seleccion", {"lam": lam_min_seleccion(fz)}),
        ("sens_mm14", {"max_move": 14}),
        ("sens_mm42", {"max_move": 42}),
        ("diag_mm_sin_tope", {"max_move": NO_CAP}),
        ("sens_tope_con_undo", {"tope_hora": fz["tope_hora_con_undo"]}),
        ("sens_danadas_fijas", {"damage": "fixed"}),
        ("sens_bodega_ilimitada", {"tope_bodega": NO_CAP}),
        ("sens_bodega_taller", {"tope_bodega": fz["tope_bodega_taller"]}),
        ("sens_mu0", {"mu": 0.0}),
        (f"sens_retiro_{other}", {"retiro": other}),
        # las decisiones del run 1 a la vez: dañadas fijas, bodega ilimitada, sin tope por movimiento, ±1
        ("diag_como_run1", {"damage": "fixed", "tope_bodega": NO_CAP, "max_move": NO_CAP,
                            "tope_hora": fz["tope_hora_con_undo"]}),
    ]


def stage_sens(jobs=JOBS):
    fz = frozen()
    specs = [_eval_spec(fz, a, d, tag, **over)
             for tag, over in sens_variants(fz) for a in ("oracle", fz["best_real"]) for d in eval_days()]
    run_many(specs, jobs, "sens")


# ======================================================================
# 7. Dónde falla
# ======================================================================

def load_blocks() -> pd.DataFrame:
    """E/F por estación × bloque de las corridas de evaluación (L = 60), sin
    las estaciones-día fuera de servicio (como la métrica principal)."""
    sb = pd.read_parquet(BLOCKS_PARQUET)
    sb["key"] = sb["run"].str.rsplit("|", n=1).str[0]
    sb["day"] = sb["run"].str.rsplit("|", n=1).str[1]
    oos = {f"{d}|{s}" for d in eval_days() for s in day_inputs(d)[0].query("out_of_service")["short_name"]}
    return sb[~(sb["day"] + "|" + sb["short_name"]).isin(oos)].reset_index(drop=True)


def forecast_errors(variant: str, f: int, L: int = L_MAIN, days=None) -> pd.DataFrame:
    """Por (estación, bloque de 60): salidas/llegadas reales y las del
    pronóstico que ve la primera decisión que alcanza el bloque (emisión más
    reciente con issued_at ≤ inicio del bloque − L; la primera si no hay),
    suma de los días; error absoluto del neto por día sumado; días censurados."""
    rows = []
    for d in days or eval_days():
        fc = forecast(d, variant, f)
        orc = forecast(d, "oracle", F_ORACLE).drop_duplicates(["short_name", "block_start"])
        start = pd.Timestamp(C.day_bounds(d)[0])
        issues = np.sort(fc["issued_at"].unique())
        fc = fc.assign(_lim=fc["block_start"] - pd.Timedelta(minutes=L))
        pick = np.searchsorted(issues, fc["_lim"].to_numpy(), side="right") - 1
        fc = fc[fc["issued_at"].to_numpy() == issues[np.clip(pick, 0, None)]]
        k = fc.merge(orc[["short_name", "block_start", "departures", "arrivals"]],
                     on=["short_name", "block_start"], suffixes=("", "_real"))
        assert not k.duplicated(["short_name", "block_start"]).any()
        k["block"] = ((k["block_start"] - start) // pd.Timedelta(minutes=60)).astype(int)
        a = k.groupby(["short_name", "block"])[["departures", "arrivals", "departures_real", "arrivals_real"]].sum()
        a["abs_err_net"] = ((a["arrivals"] - a["departures"]) - (a["arrivals_real"] - a["departures_real"])).abs()
        universe = sorted(orc["short_name"].unique())
        cz = P.censored(d, universe)
        cens = pd.DataFrame({"short_name": np.repeat(universe, N_BLOCKS),
                             "block": np.tile(np.arange(N_BLOCKS), len(universe)),
                             "censurado": cz.ravel().astype(int)}).set_index(["short_name", "block"])
        rows.append(a.join(cens))
    t = pd.concat(rows).groupby(level=[0, 1]).sum()
    return t.rename(columns={"departures": "sal_pron", "arrivals": "lleg_pron", "departures_real": "sal_real",
                             "arrivals_real": "lleg_real", "censurado": "dias_censurado"})


def stage_falla() -> pd.DataFrame:
    fz = frozen()
    real, days = fz["best_real"], eval_days()
    sb = load_blocks()

    def ef_of(key):
        t = sb[sb["key"] == key].groupby(["short_name", "block"])[["E", "F"]].sum()
        assert len(t) > 0, key
        return t

    best_t = ef_of(config_id(_eval_spec(fz, real, days[0])))
    orc_t = ef_of(config_id(_eval_spec(fz, "oracle", days[0])))
    eco_t = ef_of(config_id(_fixed(days[0], "evaluacion", "eval", "ecobici")))
    base_t = ef_of(config_id(_fixed(days[0], "evaluacion", "eval", "baseline")))
    out = best_t.assign(EF=best_t["E"] + best_t["F"]).sort_values("EF", ascending=False).head(20)
    out = out.join((orc_t["E"] + orc_t["F"]).rename("EF_oracle"))
    out = out.join((eco_t["E"] + eco_t["F"]).rename("EF_ecobici"))
    out = out.join((base_t["E"] + base_t["F"]).rename("EF_baseline"))
    b = fz["best"][real]
    out = out.join(forecast_errors(real, 0 if real == "daily" else b["f"]))
    out = out.reset_index()
    out.insert(1, "hora", [f"{(5 * 60 + 30 + 60 * int(x)) // 60 % 24:02d}:30" for x in out["block"]])
    share = {"EF_top20_mejor_real": int(out["EF"].sum()),
             "EF_total_mejor_real_15dias": int((best_t["E"] + best_t["F"]).sum()),
             "EF_top20_oracle": int(out["EF_oracle"].sum())}
    meta = {"mejor_real": {"arm": real, **b}, "oracle": fz["best"]["oracle"], **share}
    (RESULTS / "donde_falla.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    out.to_csv(RESULTS / "donde_falla.csv", index=False)
    # E+F por bloque (media por día): mejor real, oracle, Ecobici, baseline
    byb = pd.DataFrame({k: (t["E"] + t["F"]).groupby(level=1).sum() / len(days)
                        for k, t in [("mejor_real", best_t), ("oracle", orc_t), ("ecobici", eco_t),
                                     ("baseline", base_t)]})
    byb.insert(0, "hora", [f"{(5 * 60 + 30 + 60 * int(x)) // 60 % 24:02d}:30" for x in byb.index])
    byb.reset_index().to_csv(RESULTS / "ef_por_bloque.csv", index=False)
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    return out


# ======================================================================
# 8. Tablas
# ======================================================================

SUM_COLS = ["E", "F", "EF", "E_all", "F_all", "EF_manana", "EF_tarde", "EF_noche", "moves", "moves_manana",
            "moves_tarde", "moves_noche", "bikes_moved", "stations_touched", "A", "R", "warehouse", "clipped",
            "recorte_bodega", "recorte_por_movimiento", "recorte_fuera_de_servicio", "bodega_min", "bodega_max",
            "danos_no_aplicables", "taller_no_aplicable", "detours_dep", "detours_arr", "detours_dep_far",
            "walk_m_dep", "walk_m_arr", "trips_served"]


def summary(res: pd.DataFrame) -> pd.DataFrame:
    """Media por día por configuración; en evaluación, días en que gana al
    replay principal de Ecobici en E+F (sin fuera de servicio)."""
    r = res[res["tag"] != "v1"].copy()
    eco = r[(r["tag"] == "eval") & (r["arm"] == "ecobici") & (r["damage"] == "auto")].set_index("day")["EF"]
    r["gana_a_ecobici"] = np.where(r["split"] == "evaluacion", (r["EF"] < r["day"].map(eco)).astype(float), np.nan)
    agg = {c: "mean" for c in SUM_COLS if c in r}
    agg["gana_a_ecobici"] = "sum"
    for c in ["fallbacks", "non_optimal"]:
        if c in r:
            agg[c] = "sum"
    if "decision_s_mean" in r:
        agg["decision_s_mean"] = "mean"
        agg["decision_s_max"] = "max"
    s = r.groupby(KEY_COLS, dropna=False, as_index=False, sort=False).agg(agg)
    g = r.groupby(KEY_COLS, dropna=False, sort=False)
    s["n_dias"] = g.size().to_numpy()
    s["recorte_bodega_max"] = g["recorte_bodega"].max().to_numpy()
    s["recorte_por_movimiento_max"] = g["recorte_por_movimiento"].max().to_numpy()
    return s


def _md(df: pd.DataFrame, fmt=None) -> str:
    fmt = fmt or {}
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c in fmt:
                cells.append(fmt[c].format(v))
            elif isinstance(v, (float, np.floating)):
                cells.append("" if pd.isna(v) else (f"{v:,.0f}" if abs(v) >= 100 else f"{v:.3g}"))
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def run1_arms() -> pd.DataFrame:
    """Tabla de brazos del run 1 (05:30–12:30, sus 15 días): mejor
    configuración de L = 60 por brazo (in-sample en el run 1)."""
    s = pd.read_csv(RUN1_DIR / "resumen.csv")
    a = s[(s["tag"] == "arms") & ((s["L"] == 60) | s["L"].isna())]
    a = a.sort_values(["arm", "EF"]).drop_duplicates("arm")
    return a[["arm", "block", "H", "EF", "E", "F", "moves", "gana_a_ecobici"]].reset_index(drop=True)


def stage_tablas():
    res = load_results()
    s = summary(res)
    s.to_csv(RESULTS / "resumen.csv", index=False)
    fz = frozen()
    parts = ["# Tablas generadas por `ecosim.run` (run 2; no editar a mano)\n",
             "E y F en minutos-estación por día, sin estaciones-día fuera de servicio. "
             "Franjas: mañana 05:30–12:30, tarde 12:30–18:30, noche 18:30–00:30.\n"]
    # --- V1
    v1 = json.loads((V1_DIR / "v1_resumen.json").read_text())
    t = pd.DataFrame([{"brazo": k, **v} for k, v in v1.items() if k != "criterio"])
    parts.append("## V1: simulador vs GBFS (evaluación, media de 15 días)\n")
    parts.append(_md(t[["brazo", "E_sim", "E_obs", "mean_abs_rel_E", "mean_rel_E", "F_sim", "F_obs",
                        "mean_abs_rel_F", "mean_rel_F", "stock_mae", "stock_bias", "danadas_mae", "danadas_bias",
                        "detours_dep", "clipped", "moves"]]))
    parts.append("\n### V1 por franja (|dif| media por día)\n")
    cols = ["brazo"] + [f"mean_abs_rel_{c}_{fr}" for fr in FRANJAS for c in "EF"]
    parts.append(_md(t[cols], {c: "{:.1%}" for c in cols[1:]}))
    parts.append("\n### V1 por franja (medias: simulado / observado)\n")
    cols = ["brazo"] + [f"{c}_{w}_{fr}" for fr in FRANJAS for c in "EF" for w in ("sim", "obs")]
    parts.append(_md(t[cols]))
    parts.append(f"\nCriterio: `{json.dumps(v1['criterio'], ensure_ascii=False)}`\n")
    v1d = pd.read_csv(V1_DIR / "v1_por_dia.csv", dtype={"day": str})
    e = v1d[v1d["label"] == MAIN_REPLAY][["day", "E", "E_obs", "rel_E", "F", "F_obs", "rel_F", "rel_E_manana",
                                          "rel_E_tarde", "rel_E_noche", "stock_mae", "danadas_mae"]]
    parts.append("### V1 por día (replay principal: rebal, t0, sin ±1, dañadas dinámicas)\n")
    parts.append(_md(e, {c: "{:+.1%}" for c in ["rel_E", "rel_F", "rel_E_manana", "rel_E_tarde", "rel_E_noche"]}))
    # --- selección
    parts.append("\n## Selección (15 días de selección)\n")
    parts.append(f"Cota de retiro: **{fz['retiro']}** — `{json.dumps(fz['retiro_eleccion'], ensure_ascii=False)}`\n")
    parts.append("### Grid de λ (oracle, f60, H=2, L=60)\n")
    parts.append(_md(pd.read_csv(RESULTS / "lambda_grid.csv")))
    parts.append(f"\nλ congelado: **{fz['lam']:g}** (Kneedle sobre la cota {fz['retiro']}; con la otra cota: "
                 f"{fz['lambda_otra_cota']})\n")
    if "h_busqueda" in fz:
        hb = fz["h_busqueda"]
        ht = pd.DataFrame(hb["EF"]).rename_axis("h").reset_index()
        parts.append(f"### Búsqueda de h (E+F medio; {hb['regla']})\n")
        parts.append(_md(ht))
        parts.append(f"\nPara en: {hb['para_en']} → h_max = **{fz['h_max']}**\n")
    if "best" in fz:
        parts.append("### Grid (f, h) por variante (E+F medio de selección)\n")
        g = pd.read_csv(RESULTS / "grid_seleccion.csv")
        parts.append(_md(g.pivot_table(index=["arm", "f"], columns="H", values="EF").reset_index()))
        parts.append(f"\nMejor por variante: `{json.dumps(fz['best'])}`; mejor brazo real: **{fz['best_real']}**\n")
    # --- evaluación
    cols = ["brazo", "f", "H", "L", "EF", "E", "F", "EF_manana", "EF_tarde", "EF_noche", "moves", "bikes_moved",
            "A", "R", "warehouse", "bodega_min", "bodega_max", "clipped", "recorte_bodega", "recorte_bodega_max",
            "recorte_por_movimiento", "gana_a_ecobici", "fallbacks", "non_optimal", "decision_s_mean", "n_dias"]
    ev = s[s["tag"].isin(["eval", "eval_L"])].copy()
    ev["brazo"] = ev["arm"] + np.where(ev["damage"] == "fixed", " (dañadas fijas)", "")
    if len(ev):
        parts.append("\n## Brazos en evaluación (media por día; gana_a_ecobici = días de 15 con menos E+F "
                     "que el replay principal)\n")
        parts.append(_md(ev[ev["tag"] == "eval"][[c for c in cols if c in ev]]))
        parts.append("\n### Lead time L ∈ {45, 30}\n")
        parts.append(_md(ev[ev["tag"] == "eval_L"][[c for c in cols if c in ev]]))
    sens = s[s["tag"].str.startswith(("sens", "diag"))].copy()
    if len(sens):
        base = s[(s["tag"] == "eval") & s["arm"].isin(["oracle", fz["best_real"]])]
        sens = pd.concat([base, sens])
        parts.append("\n## Sensibilidades (evaluación; oracle y mejor brazo real con su configuración congelada)\n")
        parts.append(_md(sens[[c for c in ["tag", "arm", "lam", "max_move", "tope_hora", "tope_bodega", "mu", "retiro",
                                           "damage", "EF", "E", "F", "EF_manana", "moves", "bikes_moved",
                                           "warehouse", "clipped", "recorte_bodega", "recorte_por_movimiento",
                                           "gana_a_ecobici", "fallbacks"] if c in sens]]))
    # --- comparación con el run 1 (mañana)
    if len(ev) and (RUN1_DIR / "resumen.csv").exists():
        r1 = run1_arms().rename(columns={"EF": "EF_run1", "moves": "moves_run1", "gana_a_ecobici": "gana_run1",
                                         "block": "bloque_run1", "H": "H_run1"})
        m = ev[ev["tag"] == "eval"][["brazo", "arm", "damage", "EF_manana", "moves_manana", "EF", "moves"]]
        # el replay del run 1 es `stock` con dañadas fijas y ±1: se compara con los dos replays del run 2
        m["arm_run1"] = m["arm"].replace({"ecobici_stock": "ecobici"})
        m = m.merge(r1.rename(columns={"arm": "arm_run1"})[["arm_run1", "bloque_run1", "H_run1", "EF_run1", "moves_run1", "gana_run1"]], on="arm_run1", how="left")
        parts.append("\n## Run 2 recortado a 05:30–12:30 contra el run 1 (mismos 15 días)\n")
        parts.append("Run 1: su mejor (bloque, H) de L = 60, in-sample; su replay es `stock` con dañadas fijas y ±1. "
                     "Run 2: configuración congelada en selección; E+F y movimientos de la franja de la mañana.\n")
        parts.append(_md(m))
        d1 = s[(s["tag"] == "diag_como_run1")][["arm", "EF_manana", "moves_manana", "EF"]]
        if len(d1):
            parts.append("\nDecisiones del run 1 a la vez (`diag_como_run1`):\n")
            parts.append(_md(d1))
    if (RESULTS / "donde_falla.csv").exists():
        parts.append("\n## Dónde falla (20 estación × bloque con más E+F, suma de 15 días)\n")
        parts.append(f"`{(RESULTS / 'donde_falla.json').read_text()}`\n")
        parts.append(_md(pd.read_csv(RESULTS / "donde_falla.csv", dtype={"short_name": str})))
        parts.append("\n### E+F por bloque (media por día, evaluación)\n")
        parts.append(_md(pd.read_csv(RESULTS / "ef_por_bloque.csv")))
    (RESULTS / "tablas.md").write_text("\n".join(parts) + "\n")
    print(f"escrito {RESULTS / 'tablas.md'}")


# ======================================================================
# CLI
# ======================================================================

STAGES = ["v1", "lambda", "h", "grid", "eval", "sens", "falla", "tablas"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="todos los pasos, desde cero")
    ap.add_argument("--stage", choices=STAGES, action="append")
    ap.add_argument("--jobs", type=int, default=JOBS)
    ap.add_argument("--force", action="store_true", help="seguir aunque V1 falle gravemente")
    a = ap.parse_args(argv)
    stages = STAGES if a.all else (a.stage or [])
    if not stages:
        ap.error("usa --all o --stage")
    if a.all:
        for p in [RESULTADOS_CSV, BLOCKS_PARQUET, FROZEN_JSON]:
            p.unlink(missing_ok=True)
    t = _time.perf_counter()
    for st in stages:
        print(f"== {st} ==", flush=True)
        if st == "v1":
            df = stage_v1(a.jobs)
            if v1_summary(df)["criterio"]["grave"] and not a.force:
                print("V1 falla gravemente (> 25%): se detiene aquí (usa --force para seguir).")
                return 2
        elif st == "lambda":
            stage_lambda(a.jobs)
        elif st == "h":
            stage_h(a.jobs)
        elif st == "grid":
            stage_grid(a.jobs)
        elif st == "eval":
            stage_eval(a.jobs)
        elif st == "sens":
            stage_sens(a.jobs)
        elif st == "falla":
            stage_falla()
        elif st == "tablas":
            stage_tablas()
    print(f"listo en {(_time.perf_counter() - t) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
