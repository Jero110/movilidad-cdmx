"""Replay de un día para la app (subtask ecosim2-app): graba un "frame" por
paso de 15 min (05:30 → 00:30) de un brazo, con el estado, los viajes, la
decisión del asignador y sus insumos, los movimientos de Ecobici y las
métricas acumuladas.

    uv run python -m ecosim.replay                         # 15 días de evaluación × 4 brazos
    uv run python -m ecosim.replay --days 2025-09-03       # un día
    uv run python -m ecosim.replay --split seleccion       # los 15 de selección
    uv run python -m ecosim.replay --results-dir ../ecosim2-integracion/ecosim/results

**Mismo motor que los resultados.** Cada corrida arma los mismos insumos y
llama a `sim.simulate` con los mismos argumentos que `run.run_one` (run 2):
`initial_state`/`trips`/`neighbors` del día, dañadas `auto`, replay de Ecobici
`rebal` t0 sin ±1, y para las políticas `Asignador(threads=1, retiro,
time_limit=600)` con los parámetros congelados (`frozen.json`: λ, μ, retiro,
L, topes, bicis por movimiento; f y H del mejor (f, H) de cada brazo). La
política va envuelta en `_Grabadora`, que solo lee `last_plan` y el estado:
no cambia ninguna decisión. `EF` = E + F sin las estaciones fuera de servicio
a las 05:30, igual que `resultados.csv`; el replay lo compara renglón contra
renglón (`check` en cada archivo) y el test lo exige con tolerancia 0.

`frozen.json` y `resultados.csv` se leen en tiempo de ejecución de
`--results-dir` (o `ECOSIM_RESULTS_DIR`; por defecto `ecosim/results`). Al
cambiar lo congelado basta con volver a correr este módulo.

Salida en `data/derived/ecosim/replay/`:

* `{day}/dia.json`: estaciones (orden de `sim`), viajes reales del día
  (origen, destino, minuto de salida y de llegada desde las 05:30) y
  movimientos de Ecobici medidos por GBFS por paso.
* `{day}/{brazo}.json`: frames del brazo (ver `frames`).
* `index.json`: días, brazos, parámetros, E+F final y comparación con
  `resultados.csv`, series acumuladas para comparar brazos lado a lado.

Brazos: el mejor brazo real congelado (`best_real`), `oracle`, `ecobici`
(replay de Ecobici) y `baseline` (sin rebalanceo).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time as _time
import warnings
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ecosim import config as C
from ecosim import contracts as K
from ecosim import data, sim
from ecosim import pronostico as P
from ecosim.asignador import Asignador
from ecosim.days import load_days

OUT_DIR = C.DERIVED / "replay"
STEP = C.STEP_MIN
N_STEPS = C.WINDOW_MIN // STEP              # 76 pasos de 15 min: 05:30 … 00:15
N_FRAMES = N_STEPS + 1                      # + el cierre de las 00:30
HIGHS_THREADS = 1                           # = run.HIGHS_THREADS
HIGHS_TIME_LIMIT = 600.0                    # = run.HIGHS_TIME_LIMIT (determinista)
REPLAY_ECOBICI = ("t0", "rebal", False)     # = run.REPLAY["ecobici"]
DAMAGE = "auto"
KEY_COLS = ["arm", "damage", "f", "H", "L", "lam", "mu", "tope_hora", "tope_bodega", "max_move", "retiro"]

ARM_LABEL = {
    "daily": "Pronóstico diario (ma 4 semanas, sin corrección)",
    "ma": "Promedio 4 semanas + corrección intradía",
    "model": "LightGBM + corrección intradía",
    "oracle": "Oracle (pronóstico perfecto)",
    "ecobici": "Ecobici (movimientos reales medidos por GBFS)",
    "baseline": "Sin rebalanceo",
}


# ======================================================================
# Parámetros congelados y resultados (se leen en tiempo de ejecución)
# ======================================================================

def results_dir(path=None) -> Path:
    return Path(path or os.environ.get("ECOSIM_RESULTS_DIR") or C.REPO_ROOT / "ecosim" / "results")


def rel(path) -> str:
    """Ruta relativa a la raíz del repo si está dentro de él (lo que se
    guarda en disco y se muestra en la UI); si no, absoluta."""
    p = Path(path).resolve()
    try:
        return str(p.relative_to(C.REPO_ROOT.resolve()))
    except ValueError:
        return str(p)


def load_frozen(rdir=None) -> dict:
    p = results_dir(rdir) / "frozen.json"
    fz = json.loads(p.read_text())
    missing = [k for k in ("best", "best_real", "lam", "mu", "retiro", "L", "tope_hora", "tope_bodega", "max_move")
               if k not in fz]
    if missing:
        raise ValueError(f"{p}: frozen.json sin {missing} (¿es el del run 2 completo?)")
    return fz


def frozen_sha(rdir=None) -> str:
    return hashlib.sha256((results_dir(rdir) / "frozen.json").read_bytes()).hexdigest()[:12]


def arms(fz: dict) -> list[str]:
    """Mejor brazo real, oracle, Ecobici y baseline (sin repetir)."""
    out = [fz["best_real"], "oracle", "ecobici", "baseline"]
    return list(dict.fromkeys(out))


def spec(fz: dict, arm: str, day: str) -> dict:
    """Configuración de una corrida, con las mismas columnas que `resultados.csv`."""
    if arm in ("baseline", "ecobici"):
        return dict(day=day, arm=arm, damage=DAMAGE, f=np.nan, H=np.nan, L=np.nan, lam=np.nan, mu=np.nan,
                    tope_hora=np.nan, tope_bodega=np.nan, max_move=np.nan, retiro="")
    b = fz["best"][arm]
    return dict(day=day, arm=arm, damage=DAMAGE, f=int(b["f"]), H=int(b["H"]), L=int(fz["L"]),
                lam=float(fz["lam"]), mu=float(fz["mu"]), tope_hora=int(fz["tope_hora"]),
                tope_bodega=int(fz["tope_bodega"]), max_move=int(fz["max_move"]), retiro=str(fz["retiro"]))


def resultados_row(s: dict, rdir=None) -> pd.Series | None:
    """Renglón de `resultados.csv` con la misma configuración y día (cualquier tag)."""
    p = results_dir(rdir) / "resultados.csv"
    if not p.exists():
        return None
    r = pd.read_csv(p, dtype={"day": str, "retiro": str}).assign(retiro=lambda d: d["retiro"].fillna(""))
    m = (r["day"] == s["day"])
    for c in KEY_COLS:
        v = s[c]
        m &= r[c].isna() if isinstance(v, float) and np.isnan(v) else (r[c] == v)
    r = r[m]
    return None if r.empty else r.iloc[0]


# ======================================================================
# Corrida con grabación
# ======================================================================

class _Grabadora:
    """Envuelve al Asignador: pasa la llamada tal cual y guarda lo que vio y
    decidió (estado, pendientes, `last_plan`). Solo lee."""

    def __init__(self, a: Asignador):
        self.a = a
        self.log: dict = {}

    def decide(self, t, state, pending, fc, params):
        st = state.stations
        seen = {
            "bikes": int(st["bikes"].sum()), "disabled": int(st["disabled"].sum()),
            "empty": int((st["bikes"] == 0).sum()), "full": int((st["docks"] == 0).sum()),
            "warehouse": int(state.warehouse),
            "pending": [(str(o.short_name), int(o.delta), pd.Timestamp(o.effective_at)) for o in pending],
        }
        out = self.a.decide(t, state, pending, fc, params)
        p = self.a.last_plan
        rec = {"seen": seen, "orders": [(str(o.short_name), int(o.delta)) for o in out], "plan": None}
        if p is not None:
            idx = np.arange(len(p.names))
            c_y, c_0 = p.cost[idx, p.y], p.cost[idx, p.phat]
            touched = np.flatnonzero(p.x != 0)
            rec["plan"] = {
                "status": p.status, "objective": None if not np.isfinite(p.objective) else float(p.objective),
                "solve_s": round(float(p.solve_s), 3), "total_s": round(float(p.total_s), 3),
                "issued_at": pd.Timestamp(p.issued_at),
                "moves_avail": int(p.moves_avail), "bodega_bounds": [int(p.bodega_bounds[0]), int(p.bodega_bounds[1])],
                "n_decidibles": int(((p.r + p.u) > 0).sum()),
                "EF_sub_sin": float(c_0.sum()), "EF_sub_con": float(c_y.sum()),
                "stations": [{"s": str(p.names[i]), "x": int(p.x[i]), "p": round(float(p.p[i]), 2),
                              "phat": int(p.phat[i]), "r": int(p.r[i]), "u": int(p.u[i]), "K": int(p.K[i]),
                              "c0": float(c_0[i]), "c1": float(c_y[i])} for i in touched],
            }
        self.log[pd.Timestamp(t)] = rec
        return out


def run(day: str, arm: str, fz: dict):
    """Corre (día, brazo) igual que `run.run_one` y devuelve
    (DayResult, grabadora | None, spec, init, trips)."""
    warnings.simplefilter("ignore", UserWarning)
    s = spec(fz, arm, day)
    d = C.as_date(day)
    init, trips, nbrs = data.initial_state(d), data.trips(d), data.neighbors(d)
    start, end = C.day_bounds(day)
    ev = sim.load_damage_events(d, DAMAGE)[0]
    kw = dict(day=day, arm=arm, damage_events=ev)
    rec = None
    if arm == "baseline":
        res = sim.simulate(init, trips, nbrs, start, end, **kw)
    elif arm == "ecobici":
        when, variant, undo = REPLAY_ECOBICI
        res = sim.simulate(init, trips, nbrs, start, end,
                           orders=sim.ecobici_orders(day, when=when, variant=variant, undo=undo), **kw)
    else:
        f = 0 if arm == "daily" else int(s["f"])
        params = K.PolicyParams(
            lead_min=int(s["L"]), H_horas=int(s["H"]), lam=float(s["lam"]), mu=float(s["mu"]),
            tope_hora=int(s["tope_hora"]), tope_bodega=int(s["tope_bodega"]), block_min=60,
            max_bikes_per_move=int(s["max_move"]), refresh_min=f, extra={"variant": arm})
        p = P.forecast_path(day, arm, f)
        if not p.exists():
            raise FileNotFoundError(f"{p}: corre `uv run python -m ecosim.pronostico`")
        reset_highs_threads()
        rec = _Grabadora(Asignador(threads=HIGHS_THREADS, retiro=s["retiro"], time_limit=HIGHS_TIME_LIMIT))
        res = sim.simulate(init, trips, nbrs, start, end, policy=rec, lead_min=int(s["L"]),
                           forecast=pd.read_parquet(p), params=params, **kw)
    return res, rec, s, init, trips


def reset_highs_threads():
    """HiGHS fija su pool de threads con la primera corrida del proceso y
    después rechaza otro número (status "Not Set" → el asignador cae a "no
    mover nada"). En un proceso que ya corrió HiGHS con otro número de
    threads (p. ej. la suite de tests), se reinicia el pool antes de correr."""
    import highspy
    highspy.Highs.resetGlobalScheduler(True)


def ef_final(res: K.DayResult) -> dict:
    """E, F y E+F sin fuera de servicio (métrica principal de `resultados.csv`)."""
    m, x = res.metrics, res.extra
    E, F = m["E"] - x["E_out_of_service"], m["F"] - x["F_out_of_service"]
    return {"E": int(E), "F": int(F), "EF": int(E + F), "E_all": int(m["E"]), "F_all": int(m["F"])}


# ======================================================================
# Frames
# ======================================================================

def _step_of(ts, start) -> np.ndarray:
    return ((pd.to_datetime(pd.Series(ts)) - pd.Timestamp(start)) // pd.Timedelta(minutes=STEP)).to_numpy()


def frames(res: K.DayResult, rec: _Grabadora | None, init: pd.DataFrame, start) -> dict:
    """Series por paso k = 0..76 (t_k = 05:30 + 15k; el 76 es el cierre 00:30).

    * `bikes[k]`, `dis[k]`: disponibles y dañadas por estación en t_k (el
      minuto 15k del simulador, después de todos los eventos ≤ t_k; el cierre
      usa el último minuto, 00:29).
    * `cum[k]`: E, F (sin fuera de servicio), movimientos y bicis movidas
      acumulados en [05:30, t_k).
    * `applied[k]`: órdenes que se hicieron efectivas en [t_k, t_k+15), con
      lo aplicado y cada recorte.
    * `decision[k]`: la decisión tomada en t_k (política), con sus insumos.
    * `detours[k]`: desvíos de salida/llegada en [t_k, t_k+15).
    """
    x = res.extra
    names = list(x["stations"])
    pos = {s: i for i, s in enumerate(names)}
    bm, dm = x["bikes_minute"], x["disabled_minute"]
    ini = init.set_index("short_name").loc[names]
    oos = ini["out_of_service"].to_numpy(bool)
    room = (ini["cap"] - ini["docks_disabled"]).to_numpy()[None, :] - dm
    e_min = ((bm == 0) & ~oos).sum(axis=1)
    f_min = ((bm >= room) & ~oos).sum(axis=1)
    mins = [min(STEP * k, C.WINDOW_MIN - 1) for k in range(N_FRAMES)]
    ce, cf = np.r_[0, np.cumsum(e_min)], np.r_[0, np.cumsum(f_min)]

    oa = x["orders_applied"].copy()
    oa["k"] = _step_of(oa["effective_at"], start) if len(oa) else []
    oa_nz = oa[oa["applied"] != 0]
    mv_k = np.bincount(oa_nz["k"].astype(int), minlength=N_FRAMES)[:N_FRAMES] if len(oa_nz) else np.zeros(N_FRAMES, int)
    bk_k = (np.bincount(oa_nz["k"].astype(int), weights=oa_nz["applied"].abs(), minlength=N_FRAMES)[:N_FRAMES]
            if len(oa_nz) else np.zeros(N_FRAMES))
    cum_moves, cum_bikes = np.r_[0, np.cumsum(mv_k)], np.r_[0, np.cumsum(bk_k)]

    applied = [[] for _ in range(N_FRAMES)]
    for r in oa.itertuples():
        applied[int(r.k)].append([pos.get(r.short_name, -1), int(r.delta), int(r.applied), int(r.recorte_por_movimiento),
                                  int(r.recorte_fuera_de_servicio), int(r.recorte_fisico), int(r.recorte_bodega),
                                  int((pd.Timestamp(r.issued_at) - pd.Timestamp(start)) // pd.Timedelta(minutes=STEP))])

    det = x["detours"]
    dk = _step_of(det["t"], start) if len(det) else np.array([], int)
    detours = [{"dep": int(((dk == k) & (det["kind"] == "dep").to_numpy()).sum()),
                "arr": int(((dk == k) & (det["kind"] == "arr").to_numpy()).sum())} for k in range(N_FRAMES)]

    decision = [None] * N_FRAMES
    if rec is not None:
        for t, r in rec.log.items():
            k = int((t - pd.Timestamp(start)) // pd.Timedelta(minutes=STEP))
            pl = r["plan"]
            seen = dict(r["seen"])
            seen["pending"] = [[pos.get(s, -1), d, int((e - pd.Timestamp(start)) // pd.Timedelta(minutes=STEP))]
                               for s, d, e in seen["pending"]]
            dec = {"seen": seen, "orders": [[pos.get(s, -1), d] for s, d in r["orders"]], "plan": None}
            if pl is not None:
                pl = dict(pl)
                pl["issued_at"] = pl["issued_at"].strftime("%H:%M")
                pl["stations"] = [{**q, "s": pos.get(q["s"], -1)} for q in pl["stations"]]
                dec["plan"] = pl
            decision[k] = dec

    return {
        "minute": mins,
        "bikes": [bm[m].astype(int).tolist() for m in mins],
        "dis": [dm[m].astype(int).tolist() for m in mins],
        "cum": {"E": [int(ce[min(STEP * k, C.WINDOW_MIN)]) for k in range(N_FRAMES)],
                "F": [int(cf[min(STEP * k, C.WINDOW_MIN)]) for k in range(N_FRAMES)],
                "moves": [int(v) for v in cum_moves[:N_FRAMES]],
                "bikes_moved": [int(v) for v in cum_bikes[:N_FRAMES]]},
        "applied": applied,
        "decision": decision,
        "detours": detours,
    }


def day_payload(day: str, init: pd.DataFrame, trips: pd.DataFrame, names: list[str]) -> dict:
    """Datos del día comunes a todos los brazos: estaciones, viajes y
    movimientos de Ecobici (GBFS) por paso."""
    d = C.as_date(day)
    start, end = C.day_bounds(day)
    st = data.stations(d).set_index("short_name")
    ini = init.set_index("short_name").loc[names]
    pos = {s: i for i, s in enumerate(names)}
    t0 = pd.Timestamp(start)
    dep = ((trips["t_dep"] - t0) / pd.Timedelta(minutes=1)).to_numpy()
    arr = ((trips["t_arr"] - t0) / pd.Timedelta(minutes=1)).to_numpy()
    keep = ((dep >= 0) & (dep < C.WINDOW_MIN)) | ((arr >= 0) & (arr < C.WINDOW_MIN))
    o = trips["o"].astype(str).map(pos).fillna(-1).astype(int).to_numpy()
    dd = trips["d"].astype(str).map(pos).fillna(-1).astype(int).to_numpy()
    eco = sim.ecobici_orders(day, *REPLAY_ECOBICI)
    eco_k = [[] for _ in range(N_FRAMES)]
    for od in eco:
        k = int((pd.Timestamp(od.effective_at) - t0) // pd.Timedelta(minutes=STEP))
        eco_k[k].append([pos.get(od.short_name, -1), int(od.delta)])
    return {
        "day": day, "start": t0.isoformat(), "step_min": STEP, "n_frames": N_FRAMES,
        "times": [(t0 + timedelta(minutes=STEP * k)).strftime("%H:%M") for k in range(N_FRAMES)],
        "stations": {
            "short_name": names,
            "name": [str(st["name"].get(s, s)) if "name" in st.columns else s for s in names],
            "lat": [round(float(st.loc[s, "lat"]), 6) for s in names],
            "lon": [round(float(st.loc[s, "lon"]), 6) for s in names],
            "cap": ini["cap"].astype(int).tolist(),
            "docks_disabled": ini["docks_disabled"].astype(int).tolist(),
            "out_of_service": ini["out_of_service"].astype(bool).tolist(),
        },
        # viajes reales del día (los que usa el simulador), minutos desde 05:30
        "trips": {"o": o[keep].tolist(), "d": dd[keep].tolist(),
                  "dep": np.round(dep[keep], 1).tolist(), "arr": np.round(arr[keep], 1).tolist()},
        "ecobici_moves": eco_k,
    }


# ======================================================================
# Escritura
# ======================================================================

def _dump(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_js))
    tmp.replace(path)


def _js(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, (pd.Timestamp,)):
        return v.isoformat()
    raise TypeError(type(v).__name__)


def replay_day_arm(day: str, arm: str, fz: dict, rdir=None, out_dir: Path = OUT_DIR, write=True) -> dict:
    """Corre, graba y (si `write`) escribe `{day}/{arm}.json` y `{day}/dia.json`."""
    t = _time.perf_counter()
    res, rec, s, init, trips = run(day, arm, fz)
    start = C.day_bounds(day)[0]
    fr = frames(res, rec, init, start)
    ef = ef_final(res)
    assert fr["cum"]["E"][-1] == ef["E"] and fr["cum"]["F"][-1] == ef["F"], "acumulado ≠ E/F del simulador"
    row = resultados_row(s, rdir)
    check = {"resultados_csv": rel(results_dir(rdir) / "resultados.csv"),
             "EF_resultados": None if row is None else int(row["EF"]),
             "igual": None if row is None else bool(int(row["EF"]) == ef["EF"])}
    m = res.metrics
    out = {
        "day": day, "arm": arm, "label": ARM_LABEL.get(arm, arm),
        "spec": {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in s.items()},
        "frozen_sha": frozen_sha(rdir), "final": {**ef, "moves": int(m["moves"]), "bikes_moved": int(m["A"] + m["R"]),
                                                  "A": int(m["A"]), "R": int(m["R"]),
                                                  "trips_served": int(m["trips_served"])},
        "check": check, "seconds": round(_time.perf_counter() - t, 1),
        **fr,
    }
    if write:
        _dump(out, out_dir / day / f"{arm}.json")
        dp = out_dir / day / "dia.json"
        if not dp.exists():
            _dump(day_payload(day, init, trips, list(res.extra["stations"])), dp)
    return out


def refresh_check(path: Path, fz: dict, rdir=None) -> dict:
    """Vuelve a comparar un archivo ya precalculado contra el `resultados.csv`
    actual (sin re-simular: el E+F final no cambia si `frozen.json` es el
    mismo) y reescribe `check` si cambió (p. ej. la ruta de resultados)."""
    a = json.loads(path.read_text())
    row = resultados_row(spec(fz, a["arm"], a["day"]), rdir)
    check = {"resultados_csv": rel(results_dir(rdir) / "resultados.csv"),
             "EF_resultados": None if row is None else int(row["EF"]),
             "igual": None if row is None else bool(int(row["EF"]) == a["final"]["EF"])}
    if check != a["check"]:
        a["check"] = check
        _dump(a, path)
    return check


def write_index(out_dir: Path = OUT_DIR, rdir=None) -> dict:
    """`index.json` a partir de los archivos por (día, brazo) ya escritos."""
    fz = load_frozen(rdir)
    split = {d: "evaluacion" for d in load_days("evaluacion")}
    split.update({d: "seleccion" for d in load_days("seleccion")})
    days = {}
    for p in sorted(out_dir.glob("*/*.json")):
        if p.name == "dia.json":
            continue
        a = json.loads(p.read_text())
        dd = days.setdefault(a["day"], {"split": split.get(a["day"], "?"), "arms": {}})
        dd["arms"][a["arm"]] = {"label": a["label"], "final": a["final"], "check": a["check"],
                                "frozen_sha": a["frozen_sha"], "spec": a["spec"],
                                "cum": {"EF": [e + f for e, f in zip(a["cum"]["E"], a["cum"]["F"])],
                                        "moves": a["cum"]["moves"], "bikes_moved": a["cum"]["bikes_moved"]}}
    idx = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "results_dir": rel(results_dir(rdir)), "frozen_sha": frozen_sha(rdir),
        "frozen": {k: fz[k] for k in ("lam", "mu", "retiro", "L", "tope_hora", "tope_bodega", "max_move",
                                      "best", "best_real")},
        "arms": arms(fz), "best_real": fz["best_real"],
        "days": dict(sorted(days.items())),
    }
    _dump(idx, out_dir / "index.json")
    return idx


def main(argv=None):
    ap = argparse.ArgumentParser(description="Precalcula el replay de la app (frames de 15 min por día y brazo).")
    ap.add_argument("--days", nargs="*", help="días YYYY-MM-DD (por defecto, los del --split)")
    ap.add_argument("--split", default="evaluacion", choices=["evaluacion", "seleccion", "todos"])
    ap.add_argument("--arms", nargs="*", help="brazos (por defecto: mejor real, oracle, ecobici, baseline)")
    ap.add_argument("--results-dir", default=None, help="carpeta con frozen.json y resultados.csv")
    ap.add_argument("--force", action="store_true", help="recalcula aunque exista y sea del mismo frozen.json")
    a = ap.parse_args(argv)
    fz = load_frozen(a.results_dir)
    sha = frozen_sha(a.results_dir)
    days = a.days or load_days(a.split)
    todo = a.arms or arms(fz)
    bad = []
    for d in days:
        for arm in todo:
            p = OUT_DIR / d / f"{arm}.json"
            if p.exists() and not a.force:
                old = json.loads(p.read_text())
                if old.get("frozen_sha") == sha and old["check"].get("igual") is not False:
                    c = refresh_check(p, fz, a.results_dir)
                    print(f"{d} {arm}: ya está ({sha}); resultados={c['EF_resultados']} igual={c['igual']}", flush=True)
                    if c["igual"] is False:
                        bad.append((d, arm))
                    continue
            o = replay_day_arm(d, arm, fz, a.results_dir)
            c = o["check"]
            print(f"{d} {arm}: EF={o['final']['EF']} resultados={c['EF_resultados']} igual={c['igual']} "
                  f"({o['seconds']} s)", flush=True)
            if c["igual"] is False:
                bad.append((d, arm))
    write_index(OUT_DIR, a.results_dir)
    if bad:
        print(f"E+F distinto de resultados.csv en {bad}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
