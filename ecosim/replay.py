"""Replay real para la app de ecosim run 3.

Precalcula 4 días por mes de prueba (septiembre-diciembre de 2025) por los
siete brazos del run 3. Cada archivo se obtiene llamando al mismo simulador que
`ecosim.run`: `sim.run_day`, con la política, pronóstico, parámetros y replay de
Ecobici correspondientes. Antes de publicar JSON se verifica que E/F/EF del
motor coincidan exactamente con `ecosim/results/resultados.csv`.

Cada archivo trae, por foto (cada 15 min de 05:00 a 00:15 y el cierre de
00:30), lo ocurrido desde la foto anterior (`snap`, `est`, `desvios`) y el
cuadre: bicis por estación y del sistema contra viajes, órdenes, reubicaciones
junto al destino (columna `devueltas`) y cambios de etiqueta. Los eventos se
reconstruyen desde lo que devuelve el simulador (`res.extra`), sin tocar
`sim.py`. Contrato en `scripts/ecobici_mapa/README.md`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ecosim import config as C, data, run as RUN, sim
from ecosim.asignador import Asignador
from ecosim.days import load_days
from ecosim import contracts as K

OUT_DIR = C.DERIVED / "replay"
STEP = C.STEP_MIN
N_FRAMES = C.WINDOW_MIN // STEP + 1  # 05:00, 05:15, …, 00:15 y la foto de cierre 00:30
ARMS = ("sin_rebalanceo", "ecobici", "oraculo_diario", "ma_diaria", "lgbm_diario", "oraculo_directo", "lgbm_directo")
POLICIES = set(RUN.POLICIES)
# Costo por km (α, min-estación por km de cada tramo donante → receptor) precalculado además de α = 0.
ALFAS = (1, 3, 5, 7, 10)
ALFA_RE = re.compile(r"(?P<arm>[a-z_]+)(?:@a(?P<alfa>\d+))?")


def arm_file(arm: str, alfa: float = 0) -> str:
    """Nombre del archivo de un brazo: `<brazo>.json` con α = 0, `<brazo>@a<α>.json` con α > 0."""
    return f"{arm}.json" if not alfa else f"{arm}@a{int(alfa)}.json"
ARM_LABEL = {
    "sin_rebalanceo": "Sin rebalanceo",
    "ecobici": "Ecobici medido",
    "oraculo_diario": "Oráculo diario",
    "ma_diaria": "Media móvil diaria",
    "lgbm_diario": "LightGBM diario",
    "oraculo_directo": "Oráculo directo",
    "lgbm_directo": "LightGBM directo",
}


def results_dir(path=None) -> Path:
    return Path(path or os.environ.get("ECOSIM_RESULTS_DIR") or C.REPO_ROOT / "ecosim" / "results")


def rel(path) -> str:
    p = Path(path).resolve()
    try:
        return str(p.relative_to(C.REPO_ROOT.resolve()))
    except ValueError:
        return str(p)


def load_frozen(rdir=None) -> dict[str, Any]:
    p = results_dir(rdir) / "frozen.json"
    fz = json.loads(p.read_text())
    missing = [k for k in ("n", "lambda_por_brazo", "mejor_real", "topes_base", "retiro") if k not in fz]
    if missing:
        raise ValueError(f"{p}: faltan llaves run 3 {missing}")
    return fz


def frozen_sha(rdir=None) -> str:
    return hashlib.sha256((results_dir(rdir) / "frozen.json").read_bytes()).hexdigest()[:12]


def brazos(fz: dict | None = None) -> list[str]:
    return list(ARMS)


def _forma(arm: str) -> str | None:
    if arm.endswith("diario") or arm.endswith("diaria"):
        return "diaria"
    if arm.endswith("directo"):
        return "directa"
    return None


def _fold(day: str, fz: dict) -> dict | None:
    return next((c for c in fz.get("cortes", []) if c.get("test_month") == day[:7]), None)


def spec(fz: dict, arm: str, day: str) -> dict:
    if arm in POLICIES:
        return RUN.base_spec(day, arm)
    return RUN.spec(day, arm, "prueba")


def resultados_row(s: dict, rdir=None) -> pd.Series | None:
    p = results_dir(rdir) / "resultados.csv"
    if not p.exists():
        return None
    r = pd.read_csv(p, dtype={"day": str, "arm": str, "tag": str})
    key = RUN.key(s)
    if "run" in r.columns:
        hit = r[r.run == key]
        if not hit.empty:
            return hit.iloc[0]
    hit = r[(r.day == s["day"]) & (r.arm == s["arm"])]
    return None if hit.empty else hit.iloc[0]


def default_days() -> list[str]:
    r = pd.read_csv(results_dir() / "resultados.csv", dtype={"day": str})
    out: list[str] = []
    for month in ("2025-09", "2025-10", "2025-11", "2025-12"):
        days = sorted(r.loc[r.day.str.startswith(month), "day"].unique())
        if len(days) < 4:
            raise ValueError(f"resultados.csv no tiene 4 días para {month}")
        out.extend([days[i] for i in np.linspace(0, len(days) - 1, 4, dtype=int)])
    return out


class RecordingPolicy:
    """Envoltorio mínimo: no altera decisiones, solo conserva `last_plan`."""

    def __init__(self, inner: Asignador):
        self.inner = inner
        self.log: dict[str, dict] = {}

    def decide(self, t, state, pending_orders, forecast, params):
        orders = self.inner.decide(t, state, pending_orders, forecast, params)
        plan = self.inner.last_plan
        self.log[pd.Timestamp(t).isoformat()] = {
            "status": None if plan is None else plan.status,
            "segundos": None if plan is None else round(float(plan.total_s), 4),
            "orders": [(o.short_name, int(o.delta), o.paquete) for o in orders],
        }
        return orders


def _params_from_spec(s: dict) -> K.PolicyParams:
    limit = RUN.cap(s["caps"])
    return K.PolicyParams(
        n_hours=int(s["n"]), lam=float(s["lam"]),
        visits_per_decision=int(limit["visitas_por_decision"]),
        max_bikes_per_visit=int(limit["bicis_por_visita"]),
        delivery_min=int(s["delivery"]), retiro=RUN.RETIRO, alfa=float(s.get("alfa", 0.0)),
    )


def record_times(day) -> list:
    """Fotos cada 15 min desde 05:00 y una de cierre con el estado final.

    El motor solo registra instantes < 00:30; la foto de cierre se toma 1 ns
    antes, cuando ya ocurrieron todos los eventos del día.
    """
    start, end = map(pd.Timestamp, C.day_bounds(day))
    return [start + pd.Timedelta(minutes=STEP * k) for k in range(N_FRAMES - 1)] + [end - pd.Timedelta(1, "ns")]


def run_real(day: str, arm: str, fz: dict, alfa: float = 0.0):
    s = spec(fz, arm, day)
    if alfa:
        s = s | {"alfa": float(alfa)}
    record_at = record_times(day)
    rec_policy = None
    if arm == "sin_rebalanceo":
        res = sim.run_day(day, arm=arm, record_at=record_at, damage_variant=s["damage"])
    elif arm == "ecobici":
        res = sim.run_day(day, arm=arm, record_at=record_at, orders=sim.ecobici_orders(day), damage_variant=s["damage"])
    elif arm in POLICIES:
        params = _params_from_spec(s)
        rec_policy = RecordingPolicy(Asignador())
        res = sim.run_day(day, arm=arm, record_at=record_at, policy=rec_policy,
                          forecast=RUN._forecast(arm, day), params=params, damage_variant=s["damage"])
    else:
        raise ValueError(arm)
    return res, rec_policy, s


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


def day_payload(day: str, init: pd.DataFrame, names: list[str]) -> dict:
    start = pd.Timestamp(C.day_bounds(day)[0])
    st = data.stations(C.as_date(day)).set_index("short_name")
    trips = data.trips(C.as_date(day))
    pos = {s: i for i, s in enumerate(names)}
    dep = ((trips.t_dep - start) / pd.Timedelta(minutes=1)).to_numpy()
    arr = ((trips.t_arr - start) / pd.Timedelta(minutes=1)).to_numpy()
    keep = ((dep >= 0) & (dep < C.WINDOW_MIN)) | ((arr >= 0) & (arr < C.WINDOW_MIN))
    eco_k = [[] for _ in range(N_FRAMES)]
    for od in sim.ecobici_orders(day):
        k = int(_frame_of(_ns([od.pickup_at], start))[0])
        if 0 <= k < N_FRAMES:
            eco_k[k].append([pos.get(od.short_name, -1), int(od.delta)])
    ini = init.set_index("short_name").loc[names]
    return {
        "day": day, "start": start.isoformat(), "step_min": STEP, "n_frames": N_FRAMES,
        "times": [(start + timedelta(minutes=STEP*k)).strftime("%H:%M") for k in range(N_FRAMES)],
        "stations": {
            "short_name": names,
            "name": [str(st["name"].get(s, s)) if "name" in st.columns and s in st.index else s for s in names],
            "lat": [round(float(st.loc[s, "lat"]), 6) if s in st.index else None for s in names],
            "lon": [round(float(st.loc[s, "lon"]), 6) if s in st.index else None for s in names],
            "cap": ini.cap.astype(int).tolist(),
            "docks_disabled": ini.docks_disabled.astype(int).tolist(),
            "out_of_service": ini.out_of_service.astype(bool).tolist(),
        },
        "trips": {"id": np.flatnonzero(keep).tolist(),
                  "o": trips.o.astype(str).map(pos).fillna(-1).astype(int).to_numpy()[keep].tolist(),
                  "d": trips.d.astype(str).map(pos).fillna(-1).astype(int).to_numpy()[keep].tolist(),
                  "dep": np.round(dep[keep], 1).tolist(), "arr": np.round(arr[keep], 1).tolist()},
        "ecobici_moves": eco_k,
    }


def ensure_day_payload(day: str, out_dir: Path = OUT_DIR, names: list[str] | None = None) -> None:
    """Repara el archivo común si quedó de un replay con otra ventana horaria.

    Las corridas por brazo pueden ser válidas aunque `dia.json` sea del run 2:
    el hash de frozen.json solo protege los archivos de brazos, no este archivo.
    """
    path = out_dir / day / "dia.json"
    expected_start = pd.Timestamp(C.day_bounds(day)[0]).isoformat()
    if path.exists():
        try:
            old = json.loads(path.read_text())
            if (old.get("start") == expected_start
                    and old.get("step_min") == STEP
                    and old.get("n_frames") == N_FRAMES
                    and len(old.get("times", [])) == N_FRAMES
                    and old["times"][0] == "05:00"
                    and (names is None or old.get("stations", {}).get("short_name") == names)):
                return
        except (ValueError, KeyError, TypeError):
            pass
    init = data.initial_state(day).sort_values("short_name").reset_index(drop=True)
    _dump(day_payload(day, init, names or init.short_name.astype(str).tolist()), path)


STEP_NS = STEP * 60 * 1_000_000_000
# Columnas de `est[k]` (las primeras siete son el contrato; `devueltas` se agrega).
EST_COLS = ("i", "entregadas", "recogidas", "a_rentable", "a_no_rentable", "salidas", "llegadas", "devueltas")


def _frame_of(ns) -> np.ndarray:
    """Foto que contiene un evento: (t_{k−1}, t_k]; el instante 05:00 va a k=0."""
    ns = np.asarray(ns, dtype=np.int64)
    return (ns + STEP_NS - 1) // STEP_NS


def _ns(times, start) -> np.ndarray:
    return ((pd.to_datetime(pd.Series(times)) - pd.Timestamp(start)) // pd.Timedelta(1, "ns")).to_numpy(np.int64)


def trip_events(trips: pd.DataFrame, names: list[str], start, end, detours: pd.DataFrame) -> dict:
    """Reproduce, viaje por viaje, a qué estación salió y llegó cada bici en el motor.

    Usa las mismas reglas que `sim.simulate`: estación desconocida, viajes
    que ya venían en camino a las 05:00 y desvíos de `res.extra["detours"]`.
    Un desvío se asigna al viaje exacto: entre viajes del mismo instante y
    estación el motor los procesa por (prioridad, índice) y los desviados son
    los últimos (la estación ya se vació o se llenó y no cambia en ese instante).
    """
    ix = {s: i for i, s in enumerate(names)}
    end_ns = int((pd.Timestamp(end) - pd.Timestamp(start)).value)
    dep = _ns(trips.t_dep, start)
    arr = _ns(trips.t_arr, start)
    o = trips.o.astype(str).to_numpy()
    d = trips.d.astype(str).to_numpy()
    o_known = (trips.o_known.to_numpy(bool) if "o_known" in trips else np.isin(o, names)) & np.isin(o, names)
    d_known = (trips.d_known.to_numpy(bool) if "d_known" in trips else np.isin(d, names)) & np.isin(d, names)
    trip_ok = ~(((dep < 0) & (arr >= 0) & ~o_known) | ((dep >= 0) & (dep < end_ns) & ~o_known))
    has_dep = (dep >= 0) & (dep < end_ns) & o_known
    has_arr = (arr >= 0) & (arr < end_ns)
    prio_arr = np.where((dep == arr) & (dep >= 0), 5, 3)
    dep_st = np.array([ix.get(x, -1) for x in o])
    arr_st = np.where(d_known, [ix.get(x, -1) for x in d], -1)
    real_dep, real_arr = dep_st.copy(), arr_st.copy()
    dist = {}
    det = detours.copy()
    if len(det):
        det["ns"] = _ns(det.t, start)
        for (t, kind, orig), g in det.groupby(["ns", "kind", "orig"], sort=False):
            if kind == "dep":
                cand = np.flatnonzero(has_dep & (dep == t) & (o == orig))
                cand = cand[np.argsort(cand, kind="stable")]
            else:
                cand = np.flatnonzero(has_arr & (arr == t) & d_known & (d == orig))
                cand = cand[np.lexsort((cand, prio_arr[cand]))]
            if len(cand) < len(g):
                raise AssertionError(f"desvío sin viaje: {t} {kind} {orig}")
            for k, row in zip(cand[len(cand) - len(g):], g.itertuples(index=False)):
                if kind == "dep":
                    real_dep[k] = ix[row.to]
                else:
                    real_arr[k] = ix[row.to]
                dist[(int(k), kind)] = row.dist_m
    return {"dep": dep, "arr": arr, "has_dep": has_dep, "has_arr": has_arr, "trip_ok": trip_ok,
            "dep_st": dep_st, "arr_st": arr_st, "real_dep": real_dep, "real_arr": real_arr, "dist": dist,
            "transit_start": int(((dep < 0) & (arr >= 0) & o_known).sum())}


def _relocations_by_delivery(oa: pd.DataFrame, moved: pd.DataFrame) -> list[tuple]:
    """(instante de entrega, estación donde se dejó, bicis) de cada reubicación.

    El motor registra cada reubicación con su decisión y paquete; aquí solo se
    comprueba que cada lote cierre: cargadas = entregadas + reubicadas.
    """
    if not len(oa):
        if len(moved):
            raise AssertionError("reubicaciones sin órdenes")
        return []
    for (issued, number), g in oa.groupby(["issued_at", "paquete"], sort=True):
        load = int(-g.loc[g.applied < 0, "applied"].sum())
        given = int(g.loc[g.applied > 0, "applied"].sum())
        left = int(moved.loc[(moved.issued_at == issued) & (moved.paquete == number), "bicis"].sum())
        if load != given + left:
            raise AssertionError(f"lote {issued} paquete {number}: cargadas {load} != entregadas {given} + reubicadas {left}")
    return [(pd.Timestamp(r.delivery_at), str(r.estacion), int(r.bicis)) for r in moved.itertuples(index=False)]


def frames(res: K.DayResult, rec_policy: RecordingPolicy | None, trips: pd.DataFrame | None = None,
           init: pd.DataFrame | None = None, ecobici: bool | None = None) -> dict:
    x = res.extra
    names = [str(s) for s in x["stations"]]
    S = len(names)
    pos = {s: i for i, s in enumerate(names)}
    start = pd.Timestamp(x["record_times"][0])
    end = start + pd.Timedelta(minutes=C.WINDOW_MIN)
    bm = x["bikes_record"].astype(np.int64)
    dm = x["disabled_record"].astype(np.int64)
    if len(bm) != N_FRAMES:
        raise AssertionError(f"record_at incompleto: {len(bm)} != {N_FRAMES}")
    init = init if init is not None else data.initial_state(res.day)
    init = init.sort_values("short_name").set_index("short_name").loc[names]
    trips = trips if trips is not None else data.trips(C.as_date(res.day))
    ecobici = res.arm == "ecobici" if ecobici is None else ecobici
    # Métrica acumulada exacta desde la serie minuto a minuto del motor.
    bmin, dmin = x["bikes_minute"], x["disabled_minute"]
    cap = init.cap.to_numpy()[None, :]
    docks_dis = init.docks_disabled.to_numpy()[None, :]
    empty_min = (bmin == 0).sum(axis=1)
    full_min = (bmin + dmin + docks_dis == cap).sum(axis=1)
    e_prefix = np.r_[0, np.cumsum(empty_min)]
    f_prefix = np.r_[0, np.cumsum(full_min)]
    if int(e_prefix[-1]) != int(res.E) or int(f_prefix[-1]) != int(res.F):
        raise AssertionError(f"serie minuto no cuadra E/F: {(e_prefix[-1], f_prefix[-1])} != {(res.E, res.F)}")
    cuts = [min(STEP * k, C.WINDOW_MIN) for k in range(N_FRAMES)]
    cumE = [int(e_prefix[c]) for c in cuts]
    cumF = [int(f_prefix[c]) for c in cuts]

    # --- eventos por foto y estación (mismo orden de estaciones que bikes) ---
    shape = (N_FRAMES, S)
    sal, lle, ent, rec, dev = (np.zeros(shape, np.int64) for _ in range(5))
    a_r, a_nr = np.zeros(shape, np.int64), np.zeros(shape, np.int64)
    zeros = lambda: np.zeros(N_FRAMES, np.int64)  # noqa: E731
    sys_in, sys_out, ext_eco, no_aplic, no_aplic_et, en_viaje_d, camioneta_d = (zeros() for _ in range(7))
    emit_n, emit_b = zeros(), zeros()

    te = trip_events(trips, names, start, end, x["detours"])
    k_dep = _frame_of(np.maximum(te["dep"], 0))
    k_arr = _frame_of(np.maximum(te["arr"], 0))
    m = te["has_dep"]
    np.add.at(sal, (k_dep[m], te["real_dep"][m]), 1)
    np.add.at(en_viaje_d, k_dep[m], 1)
    m = te["has_arr"] & (te["real_arr"] >= 0)
    np.add.at(lle, (k_arr[m], te["real_arr"][m]), 1)
    m = te["has_arr"] & te["trip_ok"]
    np.add.at(en_viaje_d, k_arr[m], -1)
    np.add.at(sys_in, k_arr[te["has_arr"] & ~te["trip_ok"]], 1)       # bici de estación fuera del feed
    np.add.at(sys_out, k_arr[te["has_arr"] & (te["real_arr"] < 0)], 1)  # llega a estación fuera del feed
    desvios = [[] for _ in range(N_FRAMES)]
    for (tid, kind), dist_m in sorted(te["dist"].items()):
        if kind == "dep":
            k, a, b = k_dep[tid], te["dep_st"][tid], te["real_dep"][tid]
        else:
            k, a, b = k_arr[tid], te["arr_st"][tid], te["real_arr"][tid]
        desvios[int(k)].append([int(tid), "salida" if kind == "dep" else "llegada", int(a), int(b),
                                None if not np.isfinite(dist_m) else round(float(dist_m))])

    dmg = x["damage_applied"]
    if len(dmg):
        kd = _frame_of(_ns(dmg.t, start))
        si = np.array([pos.get(str(s), -1) for s in dmg.short_name])
        app = dmg.applied.to_numpy(np.int64)
        up = (dmg.kind == "sube").to_numpy()
        ok = si >= 0
        np.add.at(a_nr, (kd[ok & up], si[ok & up]), app[ok & up])
        np.add.at(a_r, (kd[ok & ~up], si[ok & ~up]), app[ok & ~up])
        np.add.at(no_aplic_et, kd, dmg.n.to_numpy(np.int64) - app)

    oa = x["orders_applied"].copy()
    applied = [[] for _ in range(N_FRAMES)]
    decision = [None for _ in range(N_FRAMES)]
    if len(oa):
        # Recogida en pickup_at y entrega en delivery_at (en el replay de Ecobici son iguales).
        eff = np.where(oa.delta.to_numpy() < 0, oa.pickup_at.to_numpy(), oa.delivery_at.to_numpy())
        oa["k_eff"] = _frame_of(_ns(eff, start))
        oa["k_iss"] = _frame_of(_ns(oa.issued_at, start))
        for r in oa.itertuples(index=False):
            i = pos.get(str(r.short_name), -1)
            ki, ke = int(r.k_iss), int(r.k_eff)
            # Cada renglón es una visita (recoger o entregar) emitida en ki.
            if 0 <= ki < N_FRAMES:
                dec = decision[ki] or {"orders": [], "plan": None}
                dec["orders"].append([i, int(r.delta), int(r.paquete)])
                decision[ki] = dec
                emit_n[ki] += 1
                emit_b[ki] += abs(int(r.delta))
            if 0 <= ke < N_FRAMES:
                v = int(r.applied)
                if v != 0:
                    applied[ke].append([i, int(r.delta), v, ki])
                if i >= 0 and v > 0:
                    ent[ke, i] += v
                elif i >= 0 and v < 0:
                    rec[ke, i] += -v
                no_aplic[ke] += abs(int(r.delta)) - abs(v)
                if ecobici:
                    ext_eco[ke] += v
                else:
                    camioneta_d[ke] -= v
        for when, to, v in ([] if ecobici else _relocations_by_delivery(oa, x["reubicaciones"])):
            k = int(_frame_of(_ns([when], start))[0])
            dev[k, pos[to]] += v
            camioneta_d[k] -= v
    # Pares ejecutados (de dónde a dónde se movieron bicis), en la foto de su entrega:
    # [paquete, origen, destino, bicis, tipo (0 traslado, 1 reubicación), dist_m, foto emitida, foto de recogida, foto de entrega].
    pares = [[] for _ in range(N_FRAMES)]
    if not ecobici:
        pe = sim.pares_ejecutados(res)
        for r in pe.itertuples(index=False):
            o, d = pos.get(str(r.origen), -1), pos.get(str(r.destino), -1)
            ks = [int(_frame_of(_ns([t], start))[0]) for t in (r.issued_at, r.pickup_at, r.delivery_at)]
            if o < 0 or d < 0 or not 0 <= ks[2] < N_FRAMES:
                continue
            pares[ks[2]].append([int(r.paquete), o, d, int(r.bicis), int(r.tipo == "reubicacion"),
                                 None if not np.isfinite(r.dist_m) else round(float(r.dist_m)), *ks])
    if rec_policy is not None:
        for iso, info in rec_policy.log.items():
            k = int(_frame_of(_ns([iso], start))[0])
            if 0 <= k < N_FRAMES and decision[k] is not None:
                decision[k]["plan"] = {"status": info["status"], "segundos": info["segundos"]}

    # --- cuadre: por estación y del sistema ---
    prev_b = np.vstack([init.bikes.to_numpy(np.int64)[None, :], bm[:-1]])
    prev_d = np.vstack([init.disabled.to_numpy(np.int64)[None, :], dm[:-1]])
    expect_b = prev_b - sal + lle + ent - rec + dev + a_r - a_nr
    expect_d = prev_d + a_nr - a_r
    bad = (expect_b != bm) | (expect_d != dm)
    en_viaje = te["transit_start"] + np.cumsum(en_viaje_d)
    camioneta = np.cumsum(camioneta_d)
    sistema = bm.sum(axis=1) + dm.sum(axis=1) + en_viaje + camioneta
    sistema_ini = int(init.bikes.sum() + init.disabled.sum() + te["transit_start"])
    prev_sys = np.r_[sistema_ini, sistema[:-1]]
    externas = sys_in - sys_out + ext_eco
    sys_ok = sistema == prev_sys + externas
    if int(sistema[-1]) != int(x["initial_total"]) + int(x["unknown_in"]) - int(x["unknown_out"]) + int(x["replay_external"]) + int(x["damage_external"]):
        raise AssertionError("bicis del sistema al cierre no cuadran con el motor")
    if int(camioneta[-1]) != int(x["truck_end"]) or int(en_viaje[-1]) != int(x["transit_end"]):
        raise AssertionError("camioneta o viajes en curso al cierre no cuadran con el motor")

    snap, est = [], []
    for k in range(N_FRAMES):
        ef = cumE[k] + cumF[k]
        e = cumE[k] - (cumE[k-1] if k else 0)
        f = cumF[k] - (cumF[k-1] if k else 0)
        dsal = sum(1 for r in desvios[k] if r[1] == "salida")
        snap.append({
            "min_desde_anterior": cuts[k] - (cuts[k-1] if k else 0),
            "salidas": int(sal[k].sum()), "llegadas": int(lle[k].sum()),
            "desvios_salida": dsal, "desvios_llegada": len(desvios[k]) - dsal,
            "emitidas": int(emit_n[k]), "bicis_emitidas": int(emit_b[k]),
            "recogidas": int(rec[k].sum()), "entregadas": int(ent[k].sum()), "devueltas": int(dev[k].sum()),
            "bicis_no_aplicadas": int(no_aplic[k]),
            "a_rentable": int(a_r[k].sum()), "a_no_rentable": int(a_nr[k].sum()),
            "etiquetas_no_aplicadas": int(no_aplic_et[k]),
            "E": int(e), "F": int(f), "EF": int(e + f), "EF_acum": int(ef),
            "cuadre_ok": bool(not bad[k].any() and sys_ok[k]),
            "descuadre_estaciones": int(bad[k].sum()),
            "cuadre_sistema_ok": bool(sys_ok[k]),
            "bicis_sistema": int(sistema[k]), "disponibles": int(bm[k].sum()), "no_rentables": int(dm[k].sum()),
            "en_camioneta": int(camioneta[k]), "en_viaje": int(en_viaje[k]),
            "entran_externas": int(sys_in[k]), "salen_externas": int(sys_out[k]),
            "externo_ecobici": int(ext_eco[k]),
        })
        rows = np.flatnonzero(sal[k] | lle[k] | ent[k] | rec[k] | dev[k] | a_r[k] | a_nr[k])
        est.append([[int(i), int(ent[k, i]), int(rec[k, i]), int(a_r[k, i]), int(a_nr[k, i]),
                     int(sal[k, i]), int(lle[k, i]), int(dev[k, i])] for i in rows])

    mv_k = np.array([len([a for a in fr if a[2] != 0]) for fr in applied], dtype=int)
    bk_k = np.array([sum(abs(a[2]) for a in fr) for fr in applied], dtype=int)
    return {
        "minute": cuts,
        "bikes": bm.astype(int).tolist(),
        "dis": dm.astype(int).tolist(),
        "cum": {"E": cumE, "F": cumF,
                "moves": np.cumsum(mv_k).astype(int).tolist(),
                "bikes_moved": np.cumsum(bk_k).astype(int).tolist()},
        "applied": applied,
        "decision": decision,
        "pares": pares,
        "snap": snap,
        "est_cols": list(EST_COLS),
        "est": est,
        "desvios": desvios,
        "inicial": {"bikes": init.bikes.astype(int).tolist(), "dis": init.disabled.astype(int).tolist(),
                    "en_viaje": int(te["transit_start"]), "bicis_sistema": sistema_ini},
    }


def replay_day_arm(day: str, arm: str, fz: dict, rdir=None, out_dir: Path = OUT_DIR, write=True, alfa: float = 0.0) -> dict:
    """Un día de un brazo. Con `alfa > 0` (solo brazos con asignador) es una variante con costo por km:
    no se compara con resultados.csv (que es de α = 0); basta el cuadre por foto."""
    started = time.perf_counter()
    if alfa and arm not in POLICIES:
        raise ValueError(f"{arm} no usa asignador: no tiene variantes de α")
    res, rec_policy, s = run_real(day, arm, fz, alfa)
    row = resultados_row(s, rdir) if not alfa else None
    if not alfa and row is None:
        raise ValueError(f"sin renglón en resultados.csv para {RUN.key(s)}")
    for col in ("E", "F", "EF") if not alfa else ():
        if int(getattr(res, col)) != int(row[col]):
            raise AssertionError(f"{day} {arm}: {col} motor={getattr(res, col)} resultados={row[col]}")
    if s["damage"] != "onsite":
        # Con la variante 'feed' parte de las dañadas entra de fuera y el
        # registro del motor no separa esa parte: el cuadre no sería auditable.
        raise NotImplementedError("replay con cuadre solo para damage='onsite' (la del run 3)")
    fr = frames(res, rec_policy)
    if fr["cum"]["E"][-1] != res.E or fr["cum"]["F"][-1] != res.F:
        raise AssertionError("acumulados no cierran contra el motor")
    sn = fr["snap"]
    if (sum(x["desvios_salida"] for x in sn) != res.desvios_salida
            or sum(x["desvios_llegada"] for x in sn) != res.desvios_llegada):
        raise AssertionError("desvíos por foto no suman los del motor")
    fold = _fold(day, fz)
    out = {
        "day": day, "arm": arm, "alfa": float(alfa), "label": ARM_LABEL.get(arm, arm), "frozen_sha": frozen_sha(rdir),
        "spec": s | {"forma": _forma(arm), "train_start": None if fold is None else fold.get("train_start"),
                      "train_end": None if fold is None else fold.get("train_end")},
        "final": {"E": int(res.E), "F": int(res.F), "EF": int(res.EF), "visitas": int(res.visitas),
                  "moves": int(res.visitas), "bikes_moved": int(res.bicis_movidas),
                  "desvios_salida": int(res.desvios_salida), "desvios_llegada": int(res.desvios_llegada),
                  "km_desvio_medio": round(float(res.km_desvio_medio), 3),
                  "recortes": {k: int(v) for k, v in res.recortes.items()},
                  "danadas_no_aplicables": int(res.danadas_no_aplicables),
                  "visitas_sin_regla_pares": int(res.extra["visitas_sin_regla"]),
                  "pares_visitas": int(res.extra["pares_visitas"]),
                  "bicis_sistema_inicio": fr["inicial"]["bicis_sistema"],
                  "bicis_sistema_cierre": sn[-1]["bicis_sistema"]},
        "check": ({"resultados_csv": None, "igual": None} if alfa else
                  {"resultados_csv": rel(results_dir(rdir) / "resultados.csv"), "EF_resultados": int(row["EF"]),
                   "E_resultados": int(row["E"]), "F_resultados": int(row["F"]), "igual": True}) | {
                  "cuadre_todas_las_fotos": all(x["cuadre_ok"] for x in sn),
                  "fotos_sin_cuadre": [k for k, x in enumerate(sn) if not x["cuadre_ok"]]},
        "seconds": round(time.perf_counter() - started, 1),
        **fr,
    }
    if write:
        _dump(out, out_dir / day / arm_file(arm, alfa))
        ensure_day_payload(day, out_dir, list(res.extra["stations"]))
    return out


def write_index(out_dir: Path = OUT_DIR, rdir=None) -> dict:
    fz = load_frozen(rdir)
    days = {}
    sha = frozen_sha(rdir)
    for p in sorted(out_dir.glob("*/*.json")):
        # Solo carpetas AAAA-MM-DD y los brazos (con sus variantes `@aN`): quedan archivos viejos
        # (baseline.json, daily.json…) que no se publican.
        m = ALFA_RE.fullmatch(p.stem)
        if p.name == "dia.json" or not m or m["arm"] not in ARMS or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.parent.name):
            continue
        a = json.loads(p.read_text())
        if a.get("frozen_sha") != sha or a.get("day") != p.parent.name or float(a.get("alfa", 0)) != float(m["alfa"] or 0):
            continue
        dd = days.setdefault(a["day"], {"split": "prueba", "arms": {}})
        item = {"label": a["label"], "final": a["final"], "check": a["check"],
                "frozen_sha": a["frozen_sha"], "spec": a["spec"],
                "cuadre": a["check"].get("cuadre_todas_las_fotos"),
                "cum": {"EF": [e+f for e, f in zip(a["cum"]["E"], a["cum"]["F"])],
                        "moves": a["cum"]["moves"], "bikes_moved": a["cum"]["bikes_moved"]}}
        if m["alfa"]:
            dd.setdefault("alfas", {}).setdefault(a["arm"], {})[m["alfa"]] = item
        else:
            dd["arms"][a["arm"]] = item
    for dd in days.values():  # variantes ordenadas por α
        for arm, v in list(dd.get("alfas", {}).items()):
            dd["alfas"][arm] = dict(sorted(v.items(), key=lambda kv: int(kv[0])))
    idx = {"generated_at": pd.Timestamp.now().isoformat(timespec="seconds"), "results_dir": rel(results_dir(rdir)),
           "frozen_sha": sha, "arms": brazos(fz), "best_real": fz["mejor_real"],
           "frozen": {"n": fz["n"], "lambda_por_brazo": fz["lambda_por_brazo"],
                      "topes_base": fz["topes_base"], "retiro": fz["retiro"]},
           "alfas": sorted({0, *(int(k) for d in days.values() for v in d.get("alfas", {}).values() for k in v)}),
           "days": dict(sorted(days.items()))}
    _dump(idx, out_dir / "index.json")
    return idx


def main(argv=None):
    ap = argparse.ArgumentParser(description="Precalcula replay real run 3 para la app.")
    ap.add_argument("--days", nargs="*")
    ap.add_argument("--split", default="prueba", choices=["prueba", "seleccion", "todos"])
    ap.add_argument("--arms", nargs="*")
    ap.add_argument("--results-dir")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--alfas", nargs="*", type=int, default=[],
                    help="variantes con costo por km (p. ej. 1 3 5 7 10) de los brazos con asignador; además de α = 0 solo si no hay --alfas")
    a = ap.parse_args(argv)
    if a.results_dir:
        os.environ["ECOSIM_RESULTS_DIR"] = str(Path(a.results_dir).resolve())
    fz = load_frozen(a.results_dir)
    malos = [d for d in a.days or [] if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
    if malos:  # p. ej. una lista entre comillas que zsh no separa
        ap.error(f"días inválidos (uno por argumento, AAAA-MM-DD): {malos}")
    days = a.days or (load_days("seleccion")[:4] if a.split == "seleccion" else default_days())
    todo = a.arms or brazos(fz)
    for d in days:
        ensure_day_payload(d)
        for alfa in a.alfas or [0]:
            for arm in todo if not alfa else [x for x in todo if x in POLICIES]:
                p = OUT_DIR / d / arm_file(arm, alfa)
                if p.exists() and not a.force:
                    old = json.loads(p.read_text())
                    if old.get("frozen_sha") == frozen_sha(a.results_dir) and (old.get("check", {}).get("igual") or (alfa and old.get("check", {}).get("cuadre_todas_las_fotos"))):
                        print(f"{d} {arm} α={alfa}: ya está")
                        continue
                out = replay_day_arm(d, arm, fz, a.results_dir, alfa=alfa)
                print(f"{d} {arm} α={alfa}: EF={out['final']['EF']} igual={out['check']['igual']} cuadre={out['check']['cuadre_todas_las_fotos']} ({out['seconds']}s)", flush=True)
    write_index(OUT_DIR, a.results_dir)


if __name__ == "__main__":
    main(sys.argv[1:])
