"""Medición de lo que hace Ecobici (plan 2026-09-28-ecosim regla 1.3, con los
cambios del plan 2026-09-28-ecosim2: día completo, taller y dañadas, ±1).

En cada estación, entre dos snapshots GBFS consecutivos t0 < t1:

    delta = stock(t1) − stock(t0) − llegadas(t0, t1] + salidas(t0, t1]

con stock = disponibles + dañadas. Sin umbral: todo delta ≠ 0 es un
movimiento de Ecobici. `delta_avail` es lo mismo con stock = disponibles
(sensibilidad).

Descomposición de dañadas (Δdañadas = dañadas(t1) − dañadas(t0)); ver
`decompose`:
  * daño = Δdañadas si es > 0 (una disponible se marca dañada; una bici que
    llega dañada es lo mismo para el simulador: llega y se daña);
  * lo que baja de dañadas (−Δdañadas > 0) se reparte así:
      - delta > 0  → todo es taller_retiro (se llevan dañadas y ponen bicis,
        caso (d) del plan, que manda según el executor);
      - delta < 0  → taller_retiro = min(−Δdañadas, −delta), el resto es
        reparación;
      - delta == 0 → todo es reparación en sitio;
    los intervalos de un par ±1 (ruido de timestamps) se descomponen como
    delta = 0: nunca generan taller;
  * delta_rebal = delta + taller_retiro (el rebalanceo sin el taller).
Con esto el estado se reconstruye exacto: Δdisponibles − flujo = delta_rebal
− daño + reparación y Δdañadas = daño − reparación − taller_retiro.

Hora de cada snapshot: t_estado = t_commit − `config.GBFS_COMMIT_LAG_S`
(30 s), igual que el run 1. t0/t1 del parquet y los escalones de E/F
observados usan t_estado.

Qué intervalos entran:
  * t0 ≥ t_ini, con t_ini = snapshot de la estación más cercano a las 05:30
    por hora de commit (la regla de `data.initial_state`);
  * t1 con commit < 00:30: el día se corta en el último commit antes de las
    00:30. El intervalo que cruza las 00:30 es el cierre del sistema (todas
    las ancladas pasan a "dañadas") y no genera movimientos ni eventos;
  * intervalos que tocan un reporte en blanco se excluyen y se cuentan aparte.

Pares ±1 (`undo`): delta = ±1 seguido, en la misma estación y en el intervalo
inmediato, por el opuesto exacto; emparejados sin traslape (cada par suma 0). Se marcan y se excluyen solo de lo
principal (replay, conteos, tope por hora); todo se reporta también con ellos.

Uso:  uv run python -m ecosim.medicion
"""

from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pandas as pd

from ecosim import config as C
from ecosim import contracts as K
from ecosim import data

MOVES_PARQUET = C.DERIVED / "ecobici_moves.parquet"
MOVES_AVAIL_PARQUET = C.DERIVED / "ecobici_moves_avail.parquet"
MOVES_RAW_PARQUET = C.DERIVED / "ecobici_moves_raw.parquet"   # sensibilidad: hora de commit cruda
DAMAGE_PARQUET = C.DERIVED / "damage_events.parquet"
DAMAGE_RAW_PARQUET = C.DERIVED / "damage_events_raw.parquet"
RESULTS = C.REPO_ROOT / "ecosim" / "results" / "medicion"

WINDOW_MIN = 60          # ventana móvil del tope por hora
PCT_TOPE = 95
MORNING_END = timedelta(hours=7)   # 05:30–12:30: la ventana del run 1
FRANJAS = {"manana": (0, 7 * 60), "tarde": (7 * 60, 13 * 60), "noche": (13 * 60, C.WINDOW_MIN)}


# ======================================================================
# Núcleo (funciones puras, probadas con fixtures a mano)
# ======================================================================

def _ns(s: pd.Series) -> pd.Series:
    return s.astype("datetime64[ns]")


def _t_ini_state(s: pd.DataFrame, start: pd.Timestamp) -> pd.Series:
    """t (hora de estado) del snapshot inicial de cada estación, elegido por
    cercanía de `t_pick` (commit crudo) a las 05:30; empate → el anterior."""
    off = (s["t_pick"] - start).abs()
    r = s.assign(_abs=off).sort_values(["short_name", "_abs", "t_pick"])
    return r.drop_duplicates("short_name").set_index("short_name")["t"]


def decompose(delta, d_dis) -> tuple:
    """(daño, reparación, taller_retiro) de un intervalo a partir de su delta
    (stock = disponibles + dañadas) y Δdañadas. Ver el docstring del módulo."""
    delta, d_dis = np.asarray(delta), np.asarray(d_dis)
    dano = np.clip(d_dis, 0, None)
    out = np.clip(-d_dis, 0, None)                  # dañadas que dejan de estarlo
    taller = np.where(delta > 0, out, np.minimum(out, np.clip(-delta, 0, None)))
    return dano, out - taller, taller


def intervals(snaps: pd.DataFrame, trips: pd.DataFrame, day) -> pd.DataFrame:
    """Todos los intervalos (estación, t0, t1) del día con su delta.

    `snaps`: short_name, t, bikes, disabled, blank (bool, opcional), t_commit
    (opcional; si falta, t). `trips`: o, d, t_dep, t_arr. Un evento con
    tiempo te cuenta en el intervalo con t0 < te ≤ t1.

    Devuelve un renglón por intervalo con t0 ≥ t_ini y commit de t1 < 00:30:
    short_name, t0, t1, stock0/1, avail0/1, dis0/1, arrivals, departures,
    delta, delta_avail, dano, reparacion, taller_retiro, delta_rebal,
    blank_touch, undo_start (inicio de un par ±1), undo (cualquiera de los
    dos intervalos del par), undo_avail (lo mismo sobre delta_avail).
    """
    start, end = (pd.Timestamp(x) for x in C.day_bounds(day))
    s = snaps[["short_name", "t", "bikes", "disabled"]].copy()
    s["blank"] = snaps["blank"].to_numpy() if "blank" in snaps else False
    # t_ini y el corte de las 00:30 van con el commit crudo, igual que data.initial_state
    s["t_pick"] = _ns(snaps["t_commit"] if "t_commit" in snaps else snaps["t"])
    s["t"] = _ns(s["t"])
    s = s.sort_values(["short_name", "t"]).reset_index(drop=True)
    g = s.groupby("short_name", sort=False)
    iv = pd.DataFrame({
        "short_name": s["short_name"],
        "t0": s["t"],
        "t1": g["t"].shift(-1),
        "t1_pick": g["t_pick"].shift(-1),
        "avail0": s["bikes"],
        "avail1": g["bikes"].shift(-1),
        "dis0": s["disabled"],
        "dis1": g["disabled"].shift(-1),
        "blank_touch": s["blank"] | g["blank"].shift(-1, fill_value=False),
    }).dropna(subset=["t1"])
    t_ini = _t_ini_state(s, start)
    iv = iv[(iv["t0"] >= iv["short_name"].map(t_ini)) & (iv["t1_pick"] < end)].drop(columns="t1_pick").copy()
    for c in ["avail1", "dis1"]:
        iv[c] = iv[c].astype("int64")
    iv["stock0"] = iv["avail0"] + iv["dis0"]
    iv["stock1"] = iv["avail1"] + iv["dis1"]

    # eventos de viaje → intervalo (t0 < te ≤ t1) de su estación
    ev = pd.concat([
        pd.DataFrame({"short_name": trips["o"].astype(str), "te": _ns(trips["t_dep"]), "kind": "departures"}),
        pd.DataFrame({"short_name": trips["d"].astype(str), "te": _ns(trips["t_arr"]), "kind": "arrivals"}),
    ], ignore_index=True)
    ev = ev[ev["short_name"].isin(set(s["short_name"]))].sort_values("te")
    grid = s[["short_name", "t"]].rename(columns={"t": "t0"}).sort_values("t0")
    ev = pd.merge_asof(ev, grid, left_on="te", right_on="t0", by="short_name",
                       direction="backward", allow_exact_matches=False)
    cnt = (ev.dropna(subset=["t0"]).groupby(["short_name", "t0", "kind"]).size()
             .unstack("kind", fill_value=0).reindex(columns=["arrivals", "departures"], fill_value=0))
    iv = iv.merge(cnt, left_on=["short_name", "t0"], right_index=True, how="left")
    iv[["arrivals", "departures"]] = iv[["arrivals", "departures"]].fillna(0).astype("int64")

    flow = iv["arrivals"] - iv["departures"]
    iv["delta"] = (iv["stock1"] - iv["stock0"] - flow).astype("int64")
    iv["delta_avail"] = (iv["avail1"] - iv["avail0"] - flow).astype("int64")
    iv = iv.sort_values(["short_name", "t0"]).reset_index(drop=True)
    iv["undo_start"] = _undo_starts(iv)      # un True por par
    iv["undo"] = _undo_both(iv, iv["undo_start"])
    iv["undo_avail"] = _undo_both(iv, _undo_starts(iv, "delta_avail"))
    # Un par ±1 es ruido de timestamps (neto 0), no un camión: se descompone
    # como delta = 0, así que sus cambios de dañadas son daño o reparación,
    # nunca taller (p. ej. una dañada se habilita y se renta, y la salida cae
    # en el intervalo siguiente: −1 con Δdañadas −1 y luego +1).
    dano, rep, taller = decompose(iv["delta"].where(~iv["undo"], 0), iv["dis1"] - iv["dis0"])
    ok = ~iv["blank_touch"].to_numpy()             # un blanco no genera eventos
    iv["dano"] = np.where(ok, dano, 0).astype("int64")
    iv["reparacion"] = np.where(ok, rep, 0).astype("int64")
    iv["taller_retiro"] = np.where(ok, taller, 0).astype("int64")
    iv["delta_rebal"] = (iv["delta"] + iv["taller_retiro"]).astype("int64")
    return iv


def _undo_starts(iv: pd.DataFrame, col: str = "delta") -> pd.Series:
    """Inicio de un ±1 que se deshace: `col` = ±1 seguido, en la misma
    estación y en el intervalo inmediato, por el opuesto exacto (∓1).
    Intervalos que tocan un blanco no participan. Requiere `iv` ordenado por
    (short_name, t0).

    Los pares no se traslapan: en una cadena +1, −1, +1, … se emparejan de
    izquierda a derecha (1-2, 3-4, …), así que cada par suma 0 y el sobrante
    de una cadena impar queda como movimiento normal."""
    d = iv[col].where(~iv["blank_touch"], 0)
    nxt = d.groupby(iv["short_name"], sort=False).shift(-1)
    cand = d.abs().eq(1) & nxt.eq(-d)
    # posición dentro de cada racha de candidatos consecutivos de la estación
    new_station = iv["short_name"].ne(iv["short_name"].shift())
    run = (~cand | new_station).cumsum()
    pos = cand.astype("int64").groupby(run).cumsum() - 1
    return cand & pos.mod(2).eq(0)


def _undo_both(iv: pd.DataFrame, starts: pd.Series) -> pd.Series:
    return starts | starts.groupby(iv["short_name"], sort=False).shift(1, fill_value=False)


MOVES_EXTRA = ["delta_avail", "stock0", "stock1", "dis0", "dis1", "dano", "reparacion"]


def moves_from_intervals(iv: pd.DataFrame, col: str = "delta") -> pd.DataFrame:
    """Movimientos = intervalos sin blanco con `col` ≠ 0 (contrato EcobiciMoves
    + delta_avail y columnas de diagnóstico). Con col = "delta_avail" es la
    sensibilidad de solo disponibles (replay `avail`): delta = delta_rebal =
    delta_avail, taller_retiro = 0, undo = undo_avail."""
    m = iv[~iv["blank_touch"] & iv[col].ne(0)]
    cols = K.ECOBICI_MOVES_COLUMNS + MOVES_EXTRA
    m = m[(["day"] if "day" in m else []) + cols].reset_index(drop=True)
    if col == "delta_avail":
        m["delta"] = m["delta_rebal"] = m["delta_avail"]
        m["taller_retiro"] = 0
        m["undo"] = iv.loc[~iv["blank_touch"] & iv[col].ne(0), "undo_avail"].to_numpy()
    return K.validate_ecobici_moves(m)


def damage_events(iv: pd.DataFrame) -> pd.DataFrame:
    """Eventos exógenos (contrato DamageEvents) en t = t1 del intervalo; un
    renglón por (estación, t1, tipo) con n > 0. Lleva `day` si `iv` lo trae."""
    base = ["day"] if "day" in iv else []
    parts = []
    for col, kind in [("dano", "daño"), ("reparacion", "reparacion"), ("taller_retiro", "taller_retiro")]:
        x = iv[iv[col] > 0]
        parts.append(pd.DataFrame({**{b: x[b] for b in base}, "short_name": x["short_name"],
                                   "t": x["t1"], "kind": kind, "n": x[col].astype("int64")}))
    ev = pd.concat(parts, ignore_index=True).sort_values(["t", "short_name", "kind"]).reset_index(drop=True)
    return K.validate_damage_events(ev)


def main_moves(moves: pd.DataFrame, with_undo: bool = False) -> pd.DataFrame:
    """Rebalanceo de Ecobici: delta_rebal ≠ 0 (sin taller), sin pares ±1
    salvo `with_undo`. `delta` pasa a ser delta_rebal."""
    m = moves[moves["delta_rebal"].ne(0) & (with_undo | ~moves["undo"])]
    return m.assign(delta=m["delta_rebal"])


def window_counts(moves: pd.DataFrame, day) -> pd.Series:
    """Movimientos con t0 en cada ventana de 60 min [s, s+60), s = 05:30,
    05:45, …, 23:30 (ventanas completas dentro de 05:30–00:30)."""
    start, end = (pd.Timestamp(x) for x in C.day_bounds(day))
    w = pd.Timedelta(minutes=WINDOW_MIN)
    starts = pd.date_range(start, end - w, freq=f"{C.STEP_MIN}min")
    t0 = np.sort(moves["t0"].to_numpy())
    lo = np.searchsorted(t0, starts.to_numpy(), side="left")
    hi = np.searchsorted(t0, (starts + w).to_numpy(), side="left")
    return pd.Series(hi - lo, index=starts, name="moves")


def flow_stats(moves: pd.DataFrame) -> dict:
    """movimientos, estaciones, A, R, min(A, R), bodega A − R y el máximo de
    |bodega acumulada| en el día (por t0) de un conjunto de movimientos."""
    d = moves.sort_values("t0")["delta"]
    A, R = int(d[d > 0].sum()), int(-d[d < 0].sum())
    cum = d.cumsum()
    return {"moves": int(len(d)), "stations_touched": int(moves["short_name"].nunique()),
            "A": A, "R": R, "rebalanced": min(A, R), "warehouse": A - R,
            "warehouse_cum_max_abs": int(cum.abs().max()) if len(d) else 0}


def day_summary(iv: pd.DataFrame, moves: pd.DataFrame, day) -> dict:
    """Resumen del día. Sin prefijo = principal (rebalanceo sin taller, sin
    ±1); `undo_*` = con pares ±1; `stock_*` = la definición del run 1 (delta
    con dañadas, taller incluido, con ±1); `manana_*` = solo t0 < 12:30."""
    start = pd.Timestamp(C.day_bounds(day)[0])
    morning = lambda m: m[m["t0"] < start + MORNING_END]   # noqa: E731
    main, with_undo = main_moves(moves), main_moves(moves, with_undo=True)
    # rama ambigua del taller: delta > 0 con dañadas que bajan → todo taller.
    # Sensibilidad: si se leyera como reparación en sitio + poner, delta_rebal
    # de esos renglones sería delta y la bodega bajaría en ese taller.
    pos_main = int(main.loc[main["delta"].gt(0), "taller_retiro"].sum())
    ok = iv[~iv["blank_touch"]]
    av = ok[ok["delta_avail"].ne(0)]
    n = len(moves)
    blank = iv[iv["blank_touch"]]
    out = {**flow_stats(main),
           **{f"undo_{k}": v for k, v in flow_stats(with_undo).items()},
           **{f"stock_{k}": v for k, v in flow_stats(moves).items()},
           **{f"manana_{k}": v for k, v in flow_stats(morning(main)).items()},
           **{f"manana_stock_{k}": v for k, v in flow_stats(morning(moves)).items()},
           "taller_retiro": int(ok["taller_retiro"].sum()),
           "taller_rama_delta_pos": int(ok.loc[ok["delta"].gt(0), "taller_retiro"].sum()),
           "warehouse_rama_pos_reparacion": flow_stats(main)["warehouse"] - pos_main,
           "danos": int(ok["dano"].sum()),
           "reparaciones": int(ok["reparacion"].sum()),
           "manana_taller_retiro": int(morning(ok)["taller_retiro"].sum()),
           "undo_pairs": int(iv["undo_start"].sum()),
           "undo_rows": int(moves["undo"].sum()),
           "undo_pct": round(100 * int(moves["undo"].sum()) / n, 2) if n else 0.0,
           "undo_rows_dis_drop_neg": int((moves["undo"] & moves["delta"].lt(0) & moves["dis1"].lt(moves["dis0"])).sum()),
           "moves_avail": len(av),
           "A_avail": int(av["delta_avail"].clip(lower=0).sum()),
           "R_avail": int((-av["delta_avail"]).clip(lower=0).sum()),
           "intervals": len(iv),
           "excluded_blank": len(blank),
           "excluded_blank_nonzero": int(blank["delta"].ne(0).sum())}
    return out


def observed_ef(snaps: pd.DataFrame, day, lo_min: float = 0, hi_min: float = C.WINDOW_MIN) -> pd.DataFrame:
    """E y F observados en GBFS en [05:30 + lo_min, 05:30 + hi_min) (por
    defecto 05:30–00:30), en minutos-estación.

    Función escalón: el valor del snapshot k vale en [t_k, t_{k+1}) (el
    último, hasta el fin). Vacía (E) si bikes == 0, llena (F) si docks == 0.
    Los reportes en blanco no cuentan como E ni F: van a `blank_min`. Minutos
    antes del primer snapshot de la estación van a `unobs_min`. Snapshots con
    commit ≥ 00:30 (el cierre del sistema) no se usan.
    """
    day_start, day_end = (pd.Timestamp(x) for x in C.day_bounds(day))
    start, end = day_start + pd.Timedelta(minutes=lo_min), day_start + pd.Timedelta(minutes=hi_min)
    s = snaps[["short_name", "t", "bikes", "docks"]].copy()
    s["blank"] = snaps["blank"].to_numpy() if "blank" in snaps else False
    commit = _ns(snaps["t_commit"] if "t_commit" in snaps else snaps["t"])
    s = s[(commit < day_end).to_numpy()]
    s["t"] = _ns(s["t"])
    s = s.sort_values(["short_name", "t"])
    t1 = s.groupby("short_name")["t"].shift(-1).fillna(end)
    a, b = s["t"].clip(lower=start, upper=end), t1.clip(lower=start, upper=end)
    mins = (b - a) / pd.Timedelta(minutes=1)
    ok = ~s["blank"]
    first = s.groupby("short_name")["t"].min().clip(lower=start, upper=end)
    return pd.DataFrame({
        "E": float(mins[ok & s["bikes"].eq(0)].sum()),
        "F": float(mins[ok & s["docks"].eq(0)].sum()),
        "blank_min": float(mins[s["blank"]].sum()),
        "unobs_min": float(((first - start) / pd.Timedelta(minutes=1)).sum()),
        "stations": int(s["short_name"].nunique()),
    }, index=[0])


# ======================================================================
# Carga real
# ======================================================================

def state_time(snaps: pd.DataFrame, lag_s: float = C.GBFS_COMMIT_LAG_S) -> pd.DataFrame:
    """Snapshots con `t` = t_estado = t_commit − lag_s (por defecto
    GBFS_COMMIT_LAG_S = 30 s; lag_s = 0 es la versión cruda); el commit
    crudo queda en `t_commit`."""
    return snaps.assign(t_commit=snaps["t"], t=snaps["t"] - pd.Timedelta(seconds=lag_s))


def _day_trips(d) -> pd.DataFrame:
    # Salidas del día anterior, del día y del siguiente (la ventana llega a
    # d+1 00:30): cubre intervalos que empiezan antes de 05:30, los viajes que
    # cruzan medianoche y los que salen en 00:00–00:30. Se pierden solo viajes
    # de > 1 día que llegan en la ventana (548 en todo ene–nov).
    return data.trips_range(d - timedelta(days=1), d + timedelta(days=1))


def measure_day(day, lag_s: float = C.GBFS_COMMIT_LAG_S) -> pd.DataFrame:
    d = C.as_date(day)
    iv = intervals(state_time(data.snapshots(d), lag_s), _day_trips(d), d)
    iv.insert(0, "day", d.isoformat())
    return iv


def measured_days() -> list:
    """Días de 2025-08-01..2025-11-29 con cobertura ≥ 90% (bloques de 60 min)."""
    out, d = [], C.SNAPSHOT_START
    while d <= C.SNAPSHOT_END:
        if data.coverage(d) >= C.COVERAGE_MIN:
            out.append(d)
        d += timedelta(days=1)
    return out


def day_sets() -> dict:
    """{día ISO: "evaluacion" | "seleccion"} de ecosim/days.json."""
    js = json.loads(C.DAYS_JSON.read_text())
    return {x["day"]: k for k in ("evaluacion", "seleccion") for x in js[k]}


# ======================================================================
# Reporte
# ======================================================================

def hour_blocks(moves: pd.DataFrame, day) -> pd.DataFrame:
    """Movimientos, A y R por bloque de 60 min anclado a las 05:30 (como los
    bloques del pronóstico), según t0. `block` = 0 para 05:30–06:30, …, 18
    para 23:30–00:30; −1 = t0 antes de 05:30 (fuera de la ventana)."""
    start = pd.Timestamp(C.day_bounds(day)[0])
    k = np.floor((moves["t0"] - start) / pd.Timedelta(minutes=WINDOW_MIN)).astype("int64").clip(lower=-1)
    h = moves.assign(block=k, A=moves["delta"].clip(lower=0), R=(-moves["delta"]).clip(lower=0))
    out = h.groupby("block").agg(moves=("delta", "size"), A=("A", "sum"), R=("R", "sum")).reset_index()
    out["block_start"] = [("antes 05:30" if b < 0 else
                           (start + pd.Timedelta(minutes=WINDOW_MIN * b)).strftime("%H:%M")) for b in out["block"]]
    return out


def lag_sweep(days, shifts_s) -> pd.DataFrame:
    """Movimientos (todo delta ≠ 0) por día según el corrimiento (s) restado a
    la hora del commit. El mínimo marca el desfase real del feed."""
    rows = []
    for d in days:
        d = C.as_date(d)
        sn, tr = data.snapshots(d), _day_trips(d)
        for lag in shifts_s:
            iv = intervals(state_time(sn, lag), tr, d)
            rows.append({"day": d.isoformat(), "lag_s": lag,
                         "moves": int((~iv["blank_touch"] & iv["delta"].ne(0)).sum()),
                         "undo_pairs": int(iv["undo_start"].sum())})
    return pd.DataFrame(rows)


def _measure(days, sets, lag_s):
    """Mide todos los días con un desfase dado. Devuelve (movimientos,
    movimientos avail, eventos de dañadas, por día, por bloque, ventanas)."""
    ivs, rows, blocks, wins = [], [], [], []
    for d in days:
        iv = measure_day(d, lag_s)
        mv = moves_from_intervals(iv)
        main = main_moves(mv)
        start = pd.Timestamp(C.day_bounds(d)[0])
        rows.append({"day": d.isoformat(), "set": sets.get(d.isoformat(), ""),
                     "coverage": round(data.coverage(d), 4),
                     "t_ini_min": round((iv["t0"].min() - start) / pd.Timedelta(minutes=1), 2),
                     "t1_max": str(iv["t1"].max()),
                     **day_summary(iv, mv, d)})
        blocks.append(hour_blocks(main, d).assign(day=d.isoformat()))
        wins.append(pd.DataFrame({
            "moves": window_counts(main, d),
            "moves_con_undo": window_counts(main_moves(mv, with_undo=True), d),
            "moves_stock": window_counts(mv, d),          # definición del run 1
        }).rename_axis("start").reset_index().assign(day=d.isoformat()))
        ivs.append(iv)
        print(f"lag={lag_s}s", d, rows[-1]["moves"], "movimientos", flush=True)
    iv_all = pd.concat(ivs, ignore_index=True)
    return (moves_from_intervals(iv_all), moves_from_intervals(iv_all, "delta_avail"), damage_events(iv_all),
            pd.DataFrame(rows), pd.concat(blocks, ignore_index=True), pd.concat(wins, ignore_index=True))


def _pct(x, q) -> int:
    return int(np.ceil(np.percentile(x, q)))


def _max_at(m: pd.DataFrame) -> str:
    r = m.loc[m["delta"].abs().idxmax()]
    return f"{r['short_name']} {r['t0']} → {r['t1']} (delta {r['delta']:+d}, {r['arrivals']} llegadas)"


def _bikes_dist(deltas: pd.Series) -> dict:
    a = deltas.abs()
    return {**{f"p{q}": float(np.percentile(a, q)) for q in (50, 90, 95, 99)},
            "max": int(a.max()), "mean": float(a.mean()), "n": int(len(a))}


MEAN_KEYS = ["moves", "stations_touched", "A", "R", "rebalanced", "warehouse", "warehouse_cum_max_abs",
             "undo_moves", "undo_A", "undo_R", "undo_warehouse",
             "stock_moves", "stock_A", "stock_R", "stock_warehouse",
             "manana_moves", "manana_A", "manana_R", "manana_warehouse",
             "manana_stock_moves", "manana_stock_A", "manana_stock_R", "manana_stock_warehouse",
             "manana_taller_retiro", "taller_retiro", "taller_rama_delta_pos", "warehouse_rama_pos_reparacion",
             "danos", "reparaciones",
             "undo_pairs", "moves_avail", "A_avail", "R_avail"]


def _agg(df: pd.DataFrame) -> dict:
    n = df["stock_moves"].sum()
    return {
        "n_days": len(df),
        **{f"mean_{k}": float(df[k].mean()) for k in MEAN_KEYS},
        "mean_abs_warehouse": float(df["warehouse"].abs().mean()),
        "mean_abs_undo_warehouse": float(df["undo_warehouse"].abs().mean()),
        "mean_abs_stock_warehouse": float(df["stock_warehouse"].abs().mean()),
        "max_warehouse_cum_max_abs": int(df["warehouse_cum_max_abs"].max()),
        "undo_pct": float(100 * df["undo_rows"].sum() / n) if n else 0.0,
    }


def _topes(wins: pd.DataFrame, per_day: pd.DataFrame, moves: pd.DataFrame) -> dict:
    w = wins["moves"]
    return {
        "tope_hora": _pct(w, PCT_TOPE),
        "tope_hora_median": float(np.median(w)),
        "tope_hora_max": int(w.max()),
        "tope_hora_p99": _pct(w, 99),
        "tope_hora_max_at": str(wins.loc[w.idxmax(), "start"]),
        "tope_hora_con_undo": _pct(wins["moves_con_undo"], PCT_TOPE),
        "tope_hora_stock": _pct(wins["moves_stock"], PCT_TOPE),
        "n_windows": int(len(w)),
        "tope_bodega": int(np.ceil(per_day["warehouse"].abs().mean())),
        "tope_bodega_mean_abs_A_minus_R": float(per_day["warehouse"].abs().mean()),
        "tope_bodega_con_undo": int(np.ceil(per_day["undo_warehouse"].abs().mean())),
        # sensibilidad: la rama delta > 0 del taller leída como reparación en sitio + poner
        "tope_bodega_rama_delta_pos_reparacion": int(np.ceil(per_day["warehouse_rama_pos_reparacion"].abs().mean())),
        "bodega_acumulada_max_abs_mean": float(per_day["warehouse_cum_max_abs"].mean()),
        "bodega_acumulada_max_abs_p95": float(np.percentile(per_day["warehouse_cum_max_abs"], 95)),
        "bodega_acumulada_max_abs_max": int(per_day["warehouse_cum_max_abs"].max()),
        "days_warehouse_positive": int((per_day["warehouse"] > 0).sum()),
        "days_cum_over_tope_bodega": int((per_day["warehouse_cum_max_abs"]
                                          > np.ceil(per_day["warehouse"].abs().mean())).sum()),
        "bicis_por_movimiento": _bikes_dist(main_moves(moves)["delta"]),
        "bicis_por_movimiento_max_at": _max_at(main_moves(moves)),
        "bicis_por_movimiento_con_undo": _bikes_dist(main_moves(moves, with_undo=True)["delta"]),
    }


def _run1_compare(per_day: pd.DataFrame) -> dict:
    """Mañana (t0 < 12:30) medida ahora con la definición del run 1
    (`manana_stock_*`) contra el `ecobici_por_dia.csv` del run 1 (copia del
    tag `ecosim-run1` en `run1_ecobici_por_dia.csv`), en los días comunes."""
    p = RESULTS / "run1_ecobici_por_dia.csv"
    if not p.exists():
        return {}
    r1 = pd.read_csv(p, dtype={"day": str}).set_index("day")
    x = per_day.set_index("day").join(r1[["moves", "A", "R", "warehouse"]].add_prefix("run1_"), how="inner")
    ev = x[x["set"].eq("evaluacion")]
    return {
        "n_common_days": int(len(x)),
        "max_abs_diff_moves": int((x["manana_stock_moves"] - x["run1_moves"]).abs().max()),
        "max_abs_diff_A": int((x["manana_stock_A"] - x["run1_A"]).abs().max()),
        "max_abs_diff_R": int((x["manana_stock_R"] - x["run1_R"]).abs().max()),
        "eval_run1_mean_moves": float(ev["run1_moves"].mean()),
        "eval_run1_mean_A": float(ev["run1_A"].mean()),
        "eval_run1_mean_R": float(ev["run1_R"].mean()),
        "eval_run1_mean_warehouse": float(ev["run1_warehouse"].mean()),
    }


def _undo_cmp_md(c: dict) -> str:
    keys = ["undo_sum_delta", "undo_sum_delta_rebal", "moves", "moves_1_bici", "A", "R", "wh", "wh_con_undo",
            "taller", "p95_ventana"]
    names = {"usada": "usada: ±1 sobre delta, par = delta 0", "undo_sobre_delta_rebal": "alternativa: ±1 sobre delta_rebal"}
    head = "| 15 días de evaluación, media por día | " + " | ".join(keys) + " |\n|---|" + "---|" * len(keys)
    return head + "\n" + "\n".join(f"| {names.get(k, k)} | " + " | ".join(f"{v[c2]:,.1f}" for c2 in keys) + " |"
                                   for k, v in c.items())


def _report(s: dict, obs: pd.DataFrame, blocks: pd.DataFrame, sweep: pd.DataFrame) -> str:
    raw, ev, sel, r1c = s["raw"], s["evaluacion"], s["seleccion"], s["run1"]
    r1 = json.loads((RESULTS / "run1_ecobici_stats.json").read_text()) if (RESULTS / "run1_ecobici_stats.json").exists() else {}
    b = (blocks[blocks["block"] >= 0].groupby(["block", "block_start"])[["moves", "A", "R"]]
         .sum().div(s["n_days"]).reset_index())
    block_rows = "\n".join(f"| {r.block_start} | {r.moves:.1f} | {r.A:.1f} | {r.R:.1f} | {r.A - r.R:+.1f} |"
                           for r in b.itertuples())
    sw = sweep.pivot(index="lag_s", columns="day", values="moves")
    sw_min = ", ".join(f"{d}: −{sw[d].idxmin():g} s" for d in sw.columns)
    bd, bdu = s["bicis_por_movimiento"], s["bicis_por_movimiento_con_undo"]
    tall_share_m = (100 * s["mean_manana_taller_retiro"] / -s["mean_manana_stock_warehouse"]
                    if s["mean_manana_stock_warehouse"] < 0 else float("nan"))
    ob = obs[obs["set"] == "evaluacion"]

    def row(label, k, fmt="{:,.1f}"):
        return f"| {label} | " + " | ".join(fmt.format(x[k]) for x in (s, ev, sel, raw)) + " |"

    return f"""# Medición de Ecobici, run 2 (día completo 05:30–00:30)

Código: `ecosim/medicion.py`. Datos: `data/derived/ecosim/ecobici_moves.parquet` ({s['moves_total']:,}
intervalos con delta ≠ 0, hora corregida −{s['lag_s']} s), `damage_events.parquet` ({s['damage_events_total']:,}
eventos) y las sensibilidades `ecobici_moves_raw.parquet` / `damage_events_raw.parquet` (hora cruda) y
`ecobici_moves_avail.parquet` (solo disponibles). Tablas: `ecobici_stats.json`, `ecobici_por_dia.csv`,
`ecobici_por_hora.csv`, `ecobici_ventanas.csv`, `ecobici_observado.csv` (E/F GBFS de los 30 días).

**Regla.** delta = stock(t1) − stock(t0) − llegadas + salidas, stock = disponibles + dañadas, sin umbral
(plan run 1, 1.3). Entran intervalos desde el snapshot inicial (el más cercano a 05:30) hasta el último
commit antes de las 00:30: el intervalo que cruza las 00:30 es el **cierre del sistema** (las ancladas pasan
a "dañadas") y no genera nada. **Principal = rebalanceo sin taller (`delta_rebal`) y sin pares ±1.**

**Regla de taller (decisión del executor: manda el caso (d) del plan).** Con Δdañadas = dañadas(t1) − dañadas(t0):
daño = Δdañadas si > 0. Lo que baja de dañadas se reparte: si delta > 0, todo es `taller_retiro` (se llevan
dañadas y ponen bicis); si delta < 0, taller = min(−Δdañadas, −delta) y el resto es reparación; si delta = 0,
todo es reparación en sitio. delta_rebal = delta + taller_retiro. Así el simulador reconstruye exacto
disponibles y dañadas de cada snapshot (test `test_decomposition_reconstructs_state`).
**Ambigüedad declarada:** con delta > 0 y dañadas que bajan, "reparación en sitio + poner" y "taller + poner"
dan el mismo snapshot; la regla elige taller. Y "daño" incluye bicis que llegan ya dañadas (para el
simulador es lo mismo: llega y se daña).

## Números por día (media)

| | todos ({s['n_days']} días) | evaluación (15) | selección (15) | todos, hora cruda |
|---|---|---|---|---|
{row('movimientos (principal)', 'mean_moves')}
{row('estaciones tocadas', 'mean_stations_touched')}
{row('A (bicis metidas)', 'mean_A')}
{row('R (bicis sacadas)', 'mean_R')}
{row('min(A, R)', 'mean_rebalanced')}
{row('bodega A − R', 'mean_warehouse', '{:+,.1f}')}
{row('\\|A − R\\|', 'mean_abs_warehouse')}
{row('máx \\|bodega acumulada\\| en el día', 'mean_warehouse_cum_max_abs')}
{row('bicis de taller retiradas', 'mean_taller_retiro')}
{row('daños', 'mean_danos')}
{row('reparaciones', 'mean_reparaciones')}
{row('movimientos con ±1 (sensibilidad)', 'mean_undo_moves')}
{row('A − R con ±1', 'mean_undo_warehouse', '{:+,.1f}')}
{row('pares ±1', 'mean_undo_pairs')}
{row('intervalos delta ≠ 0 (def. run 1)', 'mean_stock_moves')}
{row('A − R con taller (def. run 1)', 'mean_stock_warehouse', '{:+,.1f}')}

**Tope por hora** (movimientos principales con t0 en ventanas de 60 min que empiezan cada 15 min, 05:30 …
23:30; {s['n_windows']:,} pares día × ventana): **p95 = {s['tope_hora']}**, mediana {s['tope_hora_median']:g}, máximo
{s['tope_hora_max']}. Con ±1: p95 = {s['tope_hora_con_undo']}. Definición del run 1 (todo delta ≠ 0): {s['tope_hora_stock']}.
Hora cruda: {raw['tope_hora']}.

**Tope de bodega** = promedio diario de |A − R| del rebalanceo principal, redondeado hacia arriba:
**{s['tope_bodega']}** ({s['tope_bodega_mean_abs_A_minus_R']:.1f}). Con ±1: {s['tope_bodega_con_undo']}. Leyendo la rama ambigua del
taller como reparación: {s['tope_bodega_rama_delta_pos_reparacion']} (ver Limitaciones). Máximo de la bodega
acumulada dentro del día: media {s['bodega_acumulada_max_abs_mean']:.1f}, p95 {s['bodega_acumulada_max_abs_p95']:.0f},
máximo {s['bodega_acumulada_max_abs_max']}; el acumulado pasa el tope en {s['days_cum_over_tope_bodega']} de {s['n_days']} días.
La bodega del rebalanceo es positiva en {s['days_warehouse_positive']} de {s['n_days']} días: **el tope de bodega del run 2 es
{s['tope_bodega'] / r1.get('tope_bodega_mean_abs_A_minus_R', float('nan')):.1f} veces el del run 1 porque mide sobre todo el regreso de reparadas** (ver Descomposición). Ambos topes usan todos los días medidos, como el run 1 (son capacidades de Ecobici, no
parámetros de política); las columnas de evaluación y selección están arriba.

**Bicis por movimiento** (|delta_rebal|, principal): p50 {bd['p50']:g}, p90 {bd['p90']:g}, p95 {bd['p95']:g},
p99 {bd['p99']:.1f}, máx {bd['max']} (n = {bd['n']:,}). Con ±1: p50 {bdu['p50']:g}, p95 {bdu['p95']:g}, p99 {bdu['p99']:.1f}.
El máximo es el hub {s['bicis_por_movimiento_max_at']}: un intervalo nocturno de ~40 min en el que Ecobici vacía
el hub varias veces; se ve como un solo movimiento.

## Descomposición taller / daño / rebalanceo

- **Mañana (05:30–12:30, la ventana del run 1):** con la definición del run 1 (taller dentro) Ecobici saca más de lo
  que mete, A − R = {s['mean_manana_stock_warehouse']:+.1f}. El taller retira {s['mean_manana_taller_retiro']:.1f} dañadas en la mañana,
  {tall_share_m:.0f}% de ese A − R negativo: **el taller explica todo el A − R negativo del run 1**. Sin taller y sin ±1
  el rebalanceo de la mañana queda en A − R = {s['mean_manana_warehouse']:+.1f}.
- **Día completo:** con taller dentro, A − R = {s['mean_stock_warehouse']:+.1f}: en el día Ecobici mete casi lo mismo que saca. El
  taller retira {s['mean_taller_retiro']:.1f} dañadas por día y regresan ~{s['mean_warehouse']:.0f} bicis netas que no se distinguen de
  un "poner" de rebalanceo (reparadas que vuelven del taller, o bicis de bodega). Por eso el rebalanceo sin taller
  queda en A − R = {s['mean_warehouse']:+.1f} y el tope de bodega sale en {s['tope_bodega']}. La alternativa sin separar el taller
  (|A − R| con taller dentro, como el run 1) da {s['mean_abs_stock_warehouse']:.1f} en el día completo.
- Daños ({s['mean_danos']:.1f}/día) y reparaciones ({s['mean_reparaciones']:.1f}/día) no son movimientos: son eventos exógenos
  (`damage_events.parquet`) que el simulador aplica en todos los brazos.

## Pares ±1 y taller (por qué los ±1 se detectan sobre delta y se descomponen como delta = 0)

{_undo_cmp_md(s['undo_regla_comparacion_evaluacion'])}

Las dos reglas cuadran la bodega del replay (A − R principal ≈ A − R con ±1). La alternativa ("un −1 que
es taller no forma par") convierte cada par "−1 con una dañada menos, luego +1" en *taller retira 1 dañada y
~15 min después Ecobici pone 1 bici*. Son cientos por día de visitas de camión de 1 bici, lo que no es
plausible. La explicación que cuadra con los viajes es otra: una dañada se habilita y se renta, y la salida
cae en el intervalo siguiente al snapshot que ya no la ve. Por eso se usa la primera regla.

## Comparación contra la mañana del run 1

La mañana medida ahora con la regla del run 1 reproduce el `ecobici_por_dia.csv` del run 1 en los
{r1c.get('n_common_days', 0)} días comunes: diferencia máxima {r1c.get('max_abs_diff_moves', 'n/a')} movimientos, {r1c.get('max_abs_diff_A', 'n/a')} en A,
{r1c.get('max_abs_diff_R', 'n/a')} en R (los snapshots de la mañana son idénticos; ver FUNDAMENTOS).

| 15 días de evaluación, media por día | run 1 (05:30–12:30) | run 2, misma mañana, def. run 1 | run 2 mañana, principal | run 2 día completo, principal |
|---|---|---|---|---|
| movimientos | {r1c.get('eval_run1_mean_moves', float('nan')):,.1f} | {ev['mean_manana_stock_moves']:,.1f} | {ev['mean_manana_moves']:,.1f} | {ev['mean_moves']:,.1f} |
| A | {r1c.get('eval_run1_mean_A', float('nan')):,.1f} | {ev['mean_manana_stock_A']:,.1f} | {ev['mean_manana_A']:,.1f} | {ev['mean_A']:,.1f} |
| R | {r1c.get('eval_run1_mean_R', float('nan')):,.1f} | {ev['mean_manana_stock_R']:,.1f} | {ev['mean_manana_R']:,.1f} | {ev['mean_R']:,.1f} |
| A − R | {r1c.get('eval_run1_mean_warehouse', float('nan')):+,.1f} | {ev['mean_manana_stock_warehouse']:+,.1f} | {ev['mean_manana_warehouse']:+,.1f} | {ev['mean_warehouse']:+,.1f} |

Topes del run 1 (mañana, con ±1 y taller): tope por hora {r1.get('tope_p95', 'n/a')}, bodega {r1.get('tope_bodega_mean_abs_A_minus_R', float('nan')):.1f}.
Run 2 (día completo, principal): {s['tope_hora']} y {s['tope_bodega']}.

## Por bloque de 60 min (principal, media por día)

| bloque | movimientos | A | R | A − R |
|---|---|---|---|---|
{block_rows}

## E y F observados en GBFS (15 días de evaluación, minutos-estación, 05:30–00:30)

E medio = {ob['E'].mean():,.0f} (mañana {ob['E_manana'].mean():,.0f}, tarde {ob['E_tarde'].mean():,.0f}, noche {ob['E_noche'].mean():,.0f});
F medio = {ob['F'].mean():,.0f} (mañana {ob['F_manana'].mean():,.0f}, tarde {ob['F_tarde'].mean():,.0f}, noche {ob['F_noche'].mean():,.0f}).
Escalón entre snapshots con hora corregida; E = 0 disponibles; minutos en blanco aparte (`blank_min`).

## Limitaciones

- **Reparadas que regresan del taller** no se distinguen de un "poner" de rebalanceo: quedan en delta_rebal.
  Por eso A − R del rebalanceo principal queda sesgado hacia arriba y el tope de bodega es grande.
- **Desfase de timestamps.** El feed no trae `last_reported`; se corrige con −{s['lag_s']} s (mínimo del barrido:
  {sw_min}). Aun así {s['undo_pct']:.1f}% de los intervalos con delta ≠ 0 son pares ±1 (ruido); se excluyen de lo
  principal y se descomponen como delta = 0: {s['undo_rows_dis_drop_neg']:,} de esos renglones son −1 con una dañada
  menos (una dañada que se habilita y se renta, con la salida registrada en el intervalo siguiente). Con la regla
  de taller normal serían taller y el simulador perdería esas bicis; así quedan como reparación.
- **Dos toques en un intervalo son invisibles:** solo se ve el neto por estación e intervalo. De 18:00 a
  21:30 los intervalos son de ~40 min (recolector irregular), así que ahí se juntan más toques y más viajes.
- **Ambigüedad taller/reparación** con delta > 0 (arriba). Esa rama aporta {s['mean_taller_rama_delta_pos']:.1f} de las
  {s['mean_taller_retiro']:.1f} bicis de taller por día ({100 * s['mean_taller_rama_delta_pos'] / s['mean_taller_retiro']:.0f}%). Si se leyera como "reparación en sitio + poner", la bodega
  principal bajaría de {s['mean_warehouse']:+.1f} a {s['mean_warehouse_rama_pos_reparacion']:+.1f} por día y el **tope de bodega sería
  {s['tope_bodega_rama_delta_pos_reparacion']}** en vez de {s['tope_bodega']} (`tope_bodega_rama_delta_pos_reparacion`). Una bici que llega dañada cuenta como daño.
- **Reportes en blanco:** {s['excluded_blank']:,} intervalos excluidos, {s['excluded_blank_nonzero']:,} con delta ≠ 0.
- **Atípico del 2025-08-14 por la tarde:** desde las 16:49 ~280 estaciones por intervalo tienen delta ≠ 0 (el feed y
  los viajes dejan de cuadrar). Ahí está el máximo del tope por hora ({s['tope_hora_max']}, ventana {s['tope_hora_max_at']}). No es
  día de evaluación ni de selección y no mueve el p95 (p99 de las ventanas = {s['tope_hora_p99']}).
- **Viajes** de más de 1 día que llegan en la ventana se pierden (548 en todo ene–nov).
"""


def undo_rule_comparison(days) -> dict:
    """Regla de ±1 usada (pares sobre delta, descompuestos como delta = 0)
    contra la alternativa "pares sobre delta_rebal con la descomposición
    normal". Media por día; `wh` = A − R principal, `wh_con_undo` = con ±1."""
    rows = []
    for d in days:
        iv = measure_day(d)
        alt = iv.copy()
        _, _, tall = decompose(alt["delta"], alt["dis1"] - alt["dis0"])
        alt["taller_retiro"] = np.where(alt["blank_touch"], 0, tall)
        alt["delta_rebal"] = alt["delta"] + alt["taller_retiro"]
        alt["undo"] = _undo_both(alt, _undo_starts(alt, "delta_rebal"))
        for name, x in [("usada", iv), ("undo_sobre_delta_rebal", alt)]:
            ok = x[~x["blank_touch"]]
            u, allr = ok[ok["undo"]], ok[ok["delta_rebal"].ne(0)]
            main = allr[~allr["undo"]]
            rows.append({"regla": name, "undo_sum_delta": int(u["delta"].sum()),
                         "undo_sum_delta_rebal": int(u["delta_rebal"].sum()), "moves": len(main),
                         "moves_1_bici": int(main["delta_rebal"].abs().eq(1).sum()),
                         "A": int(main["delta_rebal"].clip(lower=0).sum()),
                         "R": int((-main["delta_rebal"]).clip(lower=0).sum()),
                         "wh": int(main["delta_rebal"].sum()), "wh_con_undo": int(allr["delta_rebal"].sum()),
                         "taller": int(ok["taller_retiro"].sum()),
                         "p95_ventana": float(np.percentile(window_counts(main, d), PCT_TOPE))})
    return pd.DataFrame(rows).groupby("regla").mean().round(1).to_dict("index")


SWEEP_S = [0, 10, 20, 25, 28, 30, 32, 34, 36, 40, 50, 60]


def run() -> dict:
    RESULTS.mkdir(parents=True, exist_ok=True)
    days = measured_days()
    sets = day_sets()
    missing = sorted(set(sets) - {d.isoformat() for d in days})
    if missing:
        raise RuntimeError(f"días de days.json sin cobertura suficiente: {missing}")
    lag = C.GBFS_COMMIT_LAG_S

    moves, moves_av, dmg, per_day, blocks, wins = _measure(days, sets, lag)
    moves_raw, _, dmg_raw, per_day_raw, _, wins_raw = _measure(days, sets, 0)

    C.DERIVED.mkdir(parents=True, exist_ok=True)
    moves.to_parquet(MOVES_PARQUET, index=False)
    moves_av.to_parquet(MOVES_AVAIL_PARQUET, index=False)
    moves_raw.to_parquet(MOVES_RAW_PARQUET, index=False)
    dmg.to_parquet(DAMAGE_PARQUET, index=False)
    dmg_raw.to_parquet(DAMAGE_RAW_PARQUET, index=False)
    per_day.to_csv(RESULTS / "ecobici_por_dia.csv", index=False)
    blocks[["day", "block", "block_start", "moves", "A", "R"]].to_csv(RESULTS / "ecobici_por_hora.csv", index=False)
    wins.to_csv(RESULTS / "ecobici_ventanas.csv", index=False)

    obs_rows = []
    for d in sorted(sets):
        sn = state_time(data.snapshots(d), lag)
        o = observed_ef(sn, d).iloc[0].to_dict()
        for f, (lo, hi) in FRANJAS.items():
            of = observed_ef(sn, d, lo, hi).iloc[0]
            o.update({f"E_{f}": of["E"], f"F_{f}": of["F"], f"blank_min_{f}": of["blank_min"]})
        obs_rows.append({"day": d, "set": sets[d], **o})
    obs = pd.DataFrame(obs_rows).round(2)
    obs.to_csv(RESULTS / "ecobici_observado.csv", index=False)

    ev_list = sorted(d for d, k in sets.items() if k == "evaluacion")
    sweep = lag_sweep([ev_list[0], ev_list[len(ev_list) // 2], ev_list[-1]], SWEEP_S)
    sweep.to_csv(RESULTS / "desfase_barrido.csv", index=False)

    cmp_undo = undo_rule_comparison(ev_list)
    stats = {
        "rule": "plan 2026-09-28-ecosim §1.3 + plan 2026-09-28-ecosim2 (descomposición de dañadas); "
                "stock = disponibles + dañadas; sin umbral; hora del snapshot = commit − GBFS_COMMIT_LAG_S; "
                "ventana [t_ini, último commit < 00:30]; principal = delta_rebal sin pares ±1",
        "regla_taller": "daño = Δdañadas si > 0; si baja: delta > 0 → taller = −Δdañadas; "
                        "delta < 0 → taller = min(−Δdañadas, −delta), resto reparación; "
                        "delta = 0 → reparación; delta_rebal = delta + taller_retiro",
        "lag_s": lag,
        **_agg(per_day),
        **_topes(wins, per_day, moves),
        "moves_total": int(len(moves)),
        "damage_events_total": int(len(dmg)),
        "undo_rows_dis_drop_neg": int(per_day["undo_rows_dis_drop_neg"].sum()),
        "excluded_blank": int(per_day["excluded_blank"].sum()),
        "excluded_blank_nonzero": int(per_day["excluded_blank_nonzero"].sum()),
        "evaluacion": _agg(per_day[per_day["set"].eq("evaluacion")]),
        "seleccion": _agg(per_day[per_day["set"].eq("seleccion")]),
        "raw": {
            "descripcion": "sensibilidad: hora de commit cruda (sin restar GBFS_COMMIT_LAG_S); "
                           "ecobici_moves_raw.parquet y damage_events_raw.parquet",
            **_agg(per_day_raw),
            **_topes(wins_raw, per_day_raw, moves_raw),
            "moves_total": int(len(moves_raw)),
        },
        "run1": _run1_compare(per_day),
        "undo_regla_comparacion_evaluacion": cmp_undo,
        "lag_sweep_min_s": {d: int(g.loc[g["moves"].idxmin(), "lag_s"]) for d, g in sweep.groupby("day")},
        "days": per_day["day"].tolist(),
    }
    (RESULTS / "ecobici_stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    (RESULTS / "REPORT.md").write_text(_report(stats, obs, blocks, sweep))
    return stats


if __name__ == "__main__":
    st = run()
    print(json.dumps({k: v for k, v in st.items() if k not in ("days", "evaluacion", "seleccion", "raw")},
                     indent=2, ensure_ascii=False))
