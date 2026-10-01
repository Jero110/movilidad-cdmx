"""Simulador por eventos, viaje por viaje (plan 2026-09-28-ecosim, regla 1.4,
con los cambios del plan 2026-09-28-ecosim2, subtask simulador2).

Arranca de `data.initial_state(day)` a las 05:30 y nunca vuelve a leer el
stock real: el estado evoluciona solo con los viajes reales de `data.trips`,
los eventos exógenos de dañadas y las órdenes (tabla fija de replay o las que
emite una política). Ventana del día d: [d 05:30, d+1 00:30).

Reglas (todas de 1.4 y del plan ecosim2, más los supuestos marcados en
`ecosim/results/simulador/REPORT.md`):

* Salida en estación sin bicis → la estación más cercana con bici. Si está
  a ≤ 500 m es el desvío "dentro del radio"; si no hay ninguna en 500 m, la
  más cercana a cualquier distancia (las dos reglas juntas equivalen a "la
  más cercana con bici"; se registra si quedó dentro o fuera del radio).
* Llegada a estación sin anclaje libre → la más cercana con anclaje libre,
  sin radio. El destino del viaje no cambia aunque su salida se haya
  desviado: la bici sigue ligada al viaje y llega a `d`.
* El tiempo caminando se ignora (el viaje conserva sus horas).
* Viajes que salieron antes de 05:30 y llegan en la ventana entregan su bici;
  viajes que salen en la ventana y llegan a las 00:30 o después cuentan la
  salida y su llegada queda fuera. Los que cruzan medianoche dentro de la
  ventana son viajes normales (timestamps completos).
* Estaciones desconocidas (`o_known`/`d_known` de `data.trips`): la salida
  desde una desconocida se ignora; la llegada a una desconocida saca la bici
  del sistema (`arrivals_unknown`).
* Dañadas dinámicas (`damage_events`, contrato `DamageEvents`), iguales en
  todos los brazos y aplicadas en su `t` antes que cualquier orden:
  `daño` (disponible → dañada), `reparacion` (dañada → disponible) y
  `taller_retiro` (sale del sistema una dañada). Lo que no se puede aplicar
  (sin disponibles que dañar, sin dañadas que reparar o retirar) se cuenta.
  Sin eventos, las dañadas quedan fijas en el valor de las 05:30 (run 1).
* Órdenes `(issued_at, effective_at, short_name, delta)` aplicadas en el
  instante exacto `effective_at`. Todas las órdenes de un mismo instante son
  un lote. A cada orden de *política* se le aplica, en este orden:
  1. tope de bicis por movimiento: |delta| > `params.max_bikes_per_move` se
     recorta a ese valor (`recorte_por_movimiento`);
  2. estación fuera de servicio (`out_of_service` de `initial_state`): la
     orden se recorta completa (`extra["recorte_fuera_de_servicio"]`);
  3. recorte físico: se quita lo que hay y se pone lo que cabe (`clipped`);
  4. bodega finita sobre lo aplicado: −tope_bodega ≤ A − R ≤ +tope_bodega al
     cerrar el lote; si el lote se pasa, se recortan puestas (o retiros),
     repartidas entre estaciones en proporción a lo aplicado de ese lado
     (`recorte_bodega`), sin importar la posición de la orden en el lote.
  El replay de Ecobici es un dato: solo lleva el recorte físico.
* Anclajes libres = cap − disponibles − dañadas − anclajes deshabilitados;
  los anclajes deshabilitados quedan fijos en el valor de las 05:30.

Orden de eventos en un mismo instante: dañadas → órdenes → decisión de la
política → llegadas → salidas → muestra del minuto. E y F se cuentan minuto
a minuto: el minuto [m, m+1) de una estación cuenta como vacío (lleno) si al
instante m, después de todos los eventos con tiempo ≤ m, tiene 0 bicis
disponibles (0 anclajes libres). Hay 1140 minutos por estación en
[05:30, 00:30).
"""

from __future__ import annotations

import argparse
import heapq
import json
import sys
import time as _time
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ecosim import config as C
from ecosim import contracts as K

# Archivos de `medicion`. `stock` y `rebal` leen el mismo archivo (columnas
# distintas); `avail` tiene su propio archivo (ver `ecobici_orders`).
MOVES_FILES = {
    "rebal": C.DERIVED / "ecobici_moves.parquet",        # delta_rebal: rebalanceo sin taller
    "stock": C.DERIVED / "ecobici_moves.parquet",        # delta con disponibles + dañadas (run 1)
    "avail": C.DERIVED / "ecobici_moves_avail.parquet",  # delta solo con disponibles (run 1)
}
MOVES_COLUMN = {"rebal": "delta_rebal", "stock": "delta", "avail": "delta"}
# Dañadas con las que va cada variante del replay: `stock` y `avail` ya
# traen en su delta el taller (stock) o los daños y reparaciones (avail), así
# que con dañadas dinámicas los contarían dos veces.
REPLAY_DAMAGE = {"rebal": "auto", "stock": "fixed", "avail": "fixed"}
DAMAGE_EVENTS_FILE = C.DERIVED / "damage_events.parquet"

# prioridades de eventos en un mismo instante
_P_DAMAGE, _P_ORDER, _P_DECIDE, _P_ARR, _P_DEP, _P_SAMPLE = 0, 1, 2, 3, 4, 5
_NS_MIN = 60_000_000_000
# orden de aplicación de los eventos de dañadas de un mismo instante
_DAMAGE_RANK = {"daño": 0, "taller_retiro": 1, "reparacion": 2}


# ======================================================================
# Órdenes de Ecobici (replay) y eventos de dañadas
# ======================================================================

def ecobici_orders(day, when: str = "t0", variant: str = "rebal", undo: bool | None = None,
                   path=None) -> list[K.Order]:
    """Movimientos medidos de Ecobici → órdenes para el replay.

    `when="t0"` (principal): la orden se hace efectiva al inicio del
    intervalo; `when="t1"` (sensibilidad): al final. `issued_at =
    effective_at` (el replay no tiene lead time).

    `variant`:
    * `"rebal"` (principal, run 2): `ecobici_moves.parquet`, columna
      `delta_rebal` (rebalanceo sin el taller; el taller entra como evento
      `taller_retiro` de `damage_events`).
    * `"stock"` (run 1): mismo archivo, columna `delta` (disponibles +
      dañadas, con el taller dentro).
    * `"avail"` (run 1): `ecobici_moves_avail.parquet`, columna `delta`. No se
      usa `delta_avail` del archivo principal: ese archivo solo trae los
      intervalos con delta de stock ≠ 0 y perdería los de delta = 0 y
      delta_avail ≠ 0.

    **`stock` y `avail` van siempre con dañadas fijas** (`REPLAY_DAMAGE`,
    `damage_events="fixed"`): con dañadas dinámicas contarían dos veces lo
    mismo. `stock` ya trae el taller dentro de su delta, y `avail` (delta de
    disponibles) ya trae los daños y las reparaciones (una disponible que se
    daña baja las disponibles). La CLI lo hace cumplir (`resolve_damage`);
    quien arme el brazo a mano (p. ej. `run.py`) debe usar `REPLAY_DAMAGE`.

    `undo`: si se incluyen los pares ±1 que se deshacen (columna `undo`).
    `None` (por defecto) = `False` para `rebal` (sin ±1, principal) y `True`
    para `stock`/`avail` (como el run 1). `path` reemplaza el archivo (tests).

    Solo entran órdenes con `effective_at` en [05:30, 00:30) del día y delta ≠ 0.
    """
    if when not in ("t0", "t1"):
        raise ValueError(f"when debe ser 't0' o 't1', llegó {when!r}")
    if variant not in MOVES_FILES:
        raise ValueError(f"variant debe ser uno de {sorted(MOVES_FILES)}, llegó {variant!r}")
    if undo is None:
        undo = variant != "rebal"
    col = MOVES_COLUMN[variant]
    mv = pd.read_parquet(path or MOVES_FILES[variant])
    K.validate_ecobici_moves(mv)
    start, end = C.day_bounds(day)
    eff = mv[when]
    keep = (eff >= start) & (eff < end) & (mv[col] != 0)
    if not undo:
        keep &= ~mv["undo"].astype(bool)
    mv = mv[keep].sort_values([when, "short_name"], kind="stable")
    return [
        K.Order(t.to_pydatetime(), t.to_pydatetime(), str(s), int(d))
        for t, s, d in zip(mv[when], mv["short_name"], mv[col])
    ]


def load_damage_events(day, damage_events="auto", path=None):
    """Eventos de dañadas del día → (DataFrame o None, fuente).

    `"auto"`: lee `damage_events.parquet` (o `path`) si existe; si no, dañadas
    fijas con un aviso. `"fixed"` o `None`: dañadas fijas (run 1). DataFrame:
    se usa tal cual. Se valida con `contracts.validate_damage_events` y se
    filtra a los eventos con `t` en [05:30, 00:30) del día.
    """
    if damage_events is None or (isinstance(damage_events, str) and damage_events == "fixed"):
        return None, "fixed"
    if isinstance(damage_events, str):
        if damage_events != "auto":
            raise ValueError(f"damage_events: 'auto', 'fixed', None o DataFrame; llegó {damage_events!r}")
        p = path or DAMAGE_EVENTS_FILE
        if not p.exists():
            warnings.warn(f"{p} no existe: se simula con dañadas fijas (run 1)", stacklevel=2)
            return None, "fixed (sin archivo)"
        ev, source = pd.read_parquet(p), str(p.name)
    elif isinstance(damage_events, pd.DataFrame):
        ev, source = damage_events, "DataFrame"
    else:
        raise TypeError(f"damage_events: tipo no válido {type(damage_events).__name__}")
    K.validate_damage_events(ev)
    start, end = C.day_bounds(day)
    ev = ev[(ev["t"] >= start) & (ev["t"] < end)]
    return ev, source


def _spread(total: int, caps: list[int]) -> list[int]:
    """Reparte `total` en enteros proporcionales a `caps` (resto mayor), sin
    pasarse de cada cap. Empates del resto: gana el índice menor (el caller
    ordena, p. ej. por short_name). Requiere 0 ≤ total ≤ sum(caps)."""
    S = sum(caps)
    if total <= 0:
        return [0] * len(caps)
    if total > S:
        raise AssertionError(f"_spread: {total} > {S}")
    out = [total * c // S for c in caps]
    rem = [total * c % S for c in caps]
    for k in sorted(range(len(caps)), key=lambda k: -rem[k])[: total - sum(out)]:
        out[k] += 1
    return out


def _as_order_list(orders) -> list[K.Order]:
    if orders is None:
        return []
    if isinstance(orders, pd.DataFrame):
        return K.frame_to_orders(orders)
    return list(orders)


# ======================================================================
# Motor
# ======================================================================

def _ns(ts, start) -> int:
    """Tiempo relativo a las 05:30 en nanosegundos enteros."""
    return int((pd.Timestamp(ts) - pd.Timestamp(start)) / pd.Timedelta(1, "ns"))


def simulate(
    init: pd.DataFrame,
    trips: pd.DataFrame,
    nbrs: pd.DataFrame,
    start: datetime,
    end: datetime,
    orders=None,
    policy=None,
    lead_min: int = C.LEAD_MIN,
    forecast=None,
    params: K.PolicyParams | None = None,
    record_at=None,
    day: str | None = None,
    arm: str | None = None,
    damage_events: pd.DataFrame | None = None,
) -> K.DayResult:
    """Motor único para todos los brazos.

    init: estado inicial (columnas de `contracts.INITIAL_STATE_COLUMNS`, al
    menos short_name, bikes, disabled, docks_disabled, cap; `out_of_service`
    opcional).
    trips: viajes (`contracts.TRIP_COLUMNS`, opcional o_known/d_known; si
    faltan, se consideran conocidas las estaciones de `init`).
    nbrs: vecinos (short_name, nbr, dist_m), como `data.neighbors`.
    orders: tabla fija de órdenes (list[Order] o DataFrame) para el replay.
    policy: objeto con `decide(t, state, pending, forecast, params)`; se
    llama en 05:30, 05:45, … mientras t + lead_min < end, y sus órdenes se
    hacen efectivas en t + lead_min. Sus órdenes llevan tope por movimiento,
    fuera de servicio y bodega finita (`params`).
    damage_events: eventos `DamageEvents` (None = dañadas fijas). Los que
    caen fuera de [start, end) se ignoran.
    record_at: instantes extra en los que guardar bicis y dañadas por
    estación (p. ej. los timestamps GBFS, para validar contra lo observado).
    """
    if orders is not None and policy is not None:
        raise ValueError("run_day: usar `orders` (replay) o `policy`, no los dos")
    if policy is not None:
        if params is None:
            params = K.PolicyParams(lead_min=lead_min)
        elif params.lead_min != lead_min:
            raise ValueError(f"params.lead_min={params.lead_min} ≠ lead_min={lead_min}")
    if arm is None:
        arm = "policy" if policy is not None else ("replay" if orders is not None else "baseline")
    is_policy = policy is not None
    max_move = params.max_bikes_per_move if is_policy else None
    tope_bodega = int(params.tope_bodega) if is_policy else None

    start, end = pd.Timestamp(start).to_pydatetime(), pd.Timestamp(end).to_pydatetime()
    end_ns = _ns(end, start)
    n_min = end_ns // _NS_MIN

    # --- estaciones -------------------------------------------------------
    init = init.sort_values("short_name").reset_index(drop=True)
    names = init["short_name"].astype(str).to_numpy()
    idx = {s: i for i, s in enumerate(names)}
    n = len(names)
    bikes = init["bikes"].to_numpy(dtype=np.int64).copy()
    dis = init["disabled"].to_numpy(dtype=np.int64).copy()          # dañadas: cambian con los eventos
    docks_dis = init["docks_disabled"].to_numpy(dtype=np.int64)     # fijos
    cap = init["cap"].to_numpy(dtype=np.int64)
    room = cap - dis - docks_dis            # máximo de disponibles que caben (se actualiza in situ)
    oos = (init["out_of_service"].to_numpy(dtype=bool) if "out_of_service" in init
           else np.zeros(n, dtype=bool))
    if (bikes < 0).any() or (bikes > room).any():
        raise ValueError("estado inicial fuera de [0, cap − dañadas − anclajes deshabilitados]")
    bikes0_total, dis0_total = int(bikes.sum()), int(dis.sum())

    # vecinos por estación, ordenados por distancia
    nb = nbrs[nbrs["short_name"].isin(idx) & nbrs["nbr"].isin(idx)]
    nb = nb.sort_values(["short_name", "dist_m", "nbr"], kind="stable")
    nb_idx = [np.empty(0, dtype=np.int64)] * n
    nb_dist = [np.empty(0)] * n
    for s, g in nb.groupby("short_name", sort=False):
        nb_idx[idx[s]] = g["nbr"].map(idx).to_numpy(dtype=np.int64)
        nb_dist[idx[s]] = g["dist_m"].to_numpy(dtype=float)
    # Guarda: estación sin vecinos (sin coordenadas válidas en `data.neighbors`).
    # Si hay que desviar desde ella, no hay distancias: se usa la primera
    # estación con coordenadas válidas (orden de short_name) que cumpla la
    # condición, con dist_m = NaN, y se cuenta en `detours_no_coords`.
    has_nb = np.array([len(a) > 0 for a in nb_idx], dtype=bool)
    fallback_idx = np.flatnonzero(has_nb)

    # --- eventos ------------------------------------------------------------
    heap: list = []
    seq = 0

    def push(t_ns, prio, kind, payload):
        nonlocal seq
        heapq.heappush(heap, (t_ns, prio, seq, kind, payload))
        seq += 1

    if (trips["t_arr"] < trips["t_dep"]).any():
        raise ValueError("trips: hay viajes con t_arr < t_dep")
    o_known = trips["o_known"].to_numpy() if "o_known" in trips else trips["o"].isin(idx).to_numpy()
    d_known = trips["d_known"].to_numpy() if "d_known" in trips else trips["d"].isin(idx).to_numpy()
    t_dep = ((trips["t_dep"] - pd.Timestamp(start)) / pd.Timedelta(1, "ns")).to_numpy().astype(np.int64)
    t_arr = ((trips["t_arr"] - pd.Timestamp(start)) / pd.Timedelta(1, "ns")).to_numpy().astype(np.int64)
    o_arr, d_arr = trips["o"].astype(str).to_numpy(), trips["d"].astype(str).to_numpy()

    in_transit = 0
    inflow_unknown = 0          # llegadas a estación conocida de viajes cuya salida se ignoró
    n_dep_unknown = 0
    n_arr_after_end = 0         # salen en la ventana y llegan a las 00:30 o después
    for k in range(len(trips)):
        dep_in = 0 <= t_dep[k] < end_ns
        if t_dep[k] < 0:
            in_transit += 1     # salió antes de 05:30: está en tránsito al arrancar
        elif dep_in:
            if o_known[k] and o_arr[k] in idx:
                push(int(t_dep[k]), _P_DEP, "dep", k)
            else:
                n_dep_unknown += 1
        if 0 <= t_arr[k] < end_ns:
            # Guarda: un viaje de duración 0 que sale en la ventana no puede
            # llegar antes de salir. Su llegada va justo después de su salida
            # (misma prioridad, empujada después), no con las llegadas.
            zero = dep_in and t_arr[k] == t_dep[k]
            push(int(t_arr[k]), _P_DEP if zero else _P_ARR, "arr", k)
        elif dep_in and t_arr[k] >= end_ns:
            n_arr_after_end += 1
    transit0 = in_transit

    for m in range(n_min):
        push(m * _NS_MIN, _P_SAMPLE, "sample", m)

    # decisiones: cada STEP_MIN desde start mientras t + L < end
    decide_times = []
    if is_policy:
        t = start
        while t + timedelta(minutes=lead_min) < end:
            decide_times.append(t)
            push(_ns(t, start), _P_DECIDE, "decide", t)
            t += timedelta(minutes=C.STEP_MIN)

    rec_times = []
    for t in (record_at if record_at is not None else []):
        tn = _ns(t, start)
        if 0 <= tn < end_ns:
            push(tn, _P_SAMPLE, "record", len(rec_times))
            rec_times.append(pd.Timestamp(t))

    # dañadas: ordenadas por (t, tipo, estación) para que el orden en un mismo
    # instante sea determinista (daño → taller_retiro → reparacion)
    n_damage_outside = 0
    if damage_events is not None and len(damage_events):
        K.validate_damage_events(damage_events)
        ev = damage_events.assign(_rank=damage_events["kind"].map(_DAMAGE_RANK))
        ev = ev.sort_values(["t", "_rank", "short_name"], kind="stable")
        for t, s, kd, nn in zip(ev["t"], ev["short_name"].astype(str), ev["kind"], ev["n"]):
            tn = _ns(t, start)
            if 0 <= tn < end_ns:
                push(tn, _P_DAMAGE, "damage", (str(s), str(kd), int(nn)))
            else:
                n_damage_outside += 1

    # órdenes: un lote por instante efectivo
    batches: dict[int, list] = {}
    n_orders_outside = 0

    def schedule(o: K.Order):
        nonlocal n_orders_outside
        tn = _ns(o.effective_at, start)
        if not (0 <= tn < end_ns):
            n_orders_outside += 1
            return
        if tn not in batches:
            batches[tn] = []
            push(tn, _P_ORDER, "orders", tn)
        batches[tn].append(o)

    for o in _as_order_list(orders):
        schedule(o)

    # --- estado y bitácoras -------------------------------------------------
    trip_ok = np.ones(len(trips), dtype=bool)   # False si la salida no encontró bici
    pending: list[K.Order] = []
    empty_min = np.zeros((n_min, n), dtype=bool)
    full_min = np.zeros((n_min, n), dtype=bool)
    bikes_min = np.zeros((n_min, n), dtype=np.int32)
    dis_min = np.zeros((n_min, n), dtype=np.int16)
    rec = np.zeros((len(rec_times), n), dtype=np.int32)
    rec_dis = np.zeros((len(rec_times), n), dtype=np.int32)
    detours = []                                  # (t, kind, orig, to, dist_m)
    applied_log = []                              # ver columnas de `orders_applied`
    damage_log = []                               # (t, short_name, kind, n, applied)
    A = R = clipped = moves = 0
    cut_move_total = cut_wh_total = cut_oos_total = 0
    wh_min = wh_max = 0                           # bodega neta mínima y máxima al cerrar cada lote
    touched = set()
    trips_served = arrivals_unknown = dep_failed = arr_failed = 0
    dmg = {"daño": [0, 0], "reparacion": [0, 0], "taller_retiro": [0, 0]}  # [aplicado, no aplicable]
    n_arr = 0

    n_no_coords = 0

    def nearest(i, cond):
        nonlocal n_no_coords
        if not has_nb[i]:
            cand = fallback_idx[fallback_idx != i]
            ok = cond(cand)
            if not ok.any():
                return -1, np.nan
            n_no_coords += 1
            return int(cand[int(np.argmax(ok))]), np.nan
        cand = nb_idx[i]
        ok = cond(cand)
        if not ok.any():
            return -1, np.nan
        j = int(np.argmax(ok))
        return int(cand[j]), float(nb_dist[i][j])

    def apply_batch(t_ns, batch):
        """Aplica un lote de órdenes del mismo instante (ver docstring del módulo)."""
        nonlocal A, R, clipped, moves, cut_move_total, cut_wh_total, cut_oos_total, wh_min, wh_max
        recs = []   # [orden, i, pedido tras tope y fuera de servicio, aplicado, rec_mov, rec_oos, rec_fis, rec_bod]
        for o in batch:
            if o in pending:
                pending.remove(o)
            i = idx.get(o.short_name)
            req, cut_mov, cut_oos = o.delta, 0, 0
            if is_policy:
                if abs(req) > max_move:
                    cut_mov = abs(req) - max_move
                    req = max_move if req > 0 else -max_move
                if i is not None and oos[i]:
                    cut_oos, req = abs(req), 0
            if i is None or req == 0:
                ap = 0
            elif req > 0:
                ap = int(min(req, room[i] - bikes[i]))
            else:
                ap = -int(min(-req, bikes[i]))
            if i is not None:
                bikes[i] += ap
            recs.append([o, i, req, ap, cut_mov, cut_oos, abs(req) - abs(ap), 0])
        if is_policy:
            # Bodega finita sobre lo aplicado: el lote no puede dejar A − R fuera
            # de [−tope, +tope]. Si se pasa arriba se recortan puestas (sign = +1);
            # si se pasa abajo, retiros (sign = −1). El recorte se reparte entre
            # estaciones en proporción a lo aplicado de ese lado (`_spread`), así
            # que no depende de la posición de la orden en el lote. Por estación
            # se recorta a lo más lo que puede deshacer: una puesta no puede dejar
            # bicis negativas ni un retiro pasarse de la capacidad; con eso
            # siempre alcanza (ver REPORT).
            w = (A - R) + sum(r[3] for r in recs)
            need, sign = (w - tope_bodega, 1) if w > tope_bodega else (-tope_bodega - w, -1)
            if need > 0:
                by_st: dict[int, list] = {}
                for r in recs:
                    if sign * r[3] > 0:
                        by_st.setdefault(r[1], []).append(r)
                sts = sorted(by_st, key=lambda i: names[i])
                caps = [min(sum(sign * r[3] for r in by_st[i]),
                            int(bikes[i]) if sign > 0 else int(room[i] - bikes[i])) for i in sts]
                for i, c in zip(sts, _spread(need, caps)):
                    if c == 0:
                        continue
                    bikes[i] -= sign * c
                    rs = by_st[i]
                    for r, cc in zip(rs, _spread(c, [sign * r[3] for r in rs])):
                        r[3] -= sign * cc
                        r[7] += cc
        for o, i, req, ap, cut_mov, cut_oos, cut_fis, cut_bod in recs:
            clipped += cut_fis
            cut_move_total += cut_mov
            cut_oos_total += cut_oos
            cut_wh_total += cut_bod
            if ap != 0:
                moves += 1
                touched.add(o.short_name)
                if ap > 0:
                    A += ap
                else:
                    R -= ap
            applied_log.append((o.issued_at, o.effective_at, o.short_name, o.delta, ap,
                                cut_mov, cut_oos, cut_fis, cut_bod))
        w = A - R
        if is_policy and not (-tope_bodega <= w <= tope_bodega):
            raise AssertionError(f"bodega fuera del tope: {w} ∉ [±{tope_bodega}]")
        wh_min, wh_max = min(wh_min, w), max(wh_max, w)

    while heap:
        t_ns, prio, _, kind, p = heapq.heappop(heap)
        if kind == "dep":
            i = idx[o_arr[p]]
            if bikes[i] == 0:
                j, dist = nearest(i, lambda c: bikes[c] > 0)
                if j < 0:
                    dep_failed += 1
                    trip_ok[p] = False
                    continue
                detours.append((t_ns, "dep", names[i], names[j], dist))
                i = j
            bikes[i] -= 1
            in_transit += 1
            trips_served += 1
        elif kind == "arr":
            if t_dep[p] >= 0 and not trip_ok[p]:
                continue                          # la salida no ocurrió: no hay bici
            i = idx[d_arr[p]] if (d_known[p] and d_arr[p] in idx) else -1
            if i >= 0 and bikes[i] >= room[i]:
                j, dist = nearest(i, lambda c: bikes[c] < room[c])
                if j < 0:
                    arr_failed += 1               # nadie tiene anclaje: la bici queda en tránsito
                    continue
                detours.append((t_ns, "arr", names[i], names[j], dist))
                i = j
            n_arr += 1
            if t_dep[p] >= 0 and not (o_known[p] and o_arr[p] in idx):
                inflow_unknown += 1               # bici que entra desde una desconocida
            else:
                in_transit -= 1
            if i < 0:
                arrivals_unknown += 1             # llega a una desconocida: sale del sistema
            else:
                bikes[i] += 1
        elif kind == "damage":
            s, kd, nn = p
            i = idx.get(s)
            if i is None:
                a = 0
            elif kd == "daño":                    # disponible → dañada (ocupa el mismo anclaje)
                a = int(min(nn, bikes[i]))
                bikes[i] -= a
                dis[i] += a
                room[i] -= a
            elif kd == "reparacion":              # dañada → disponible
                a = int(min(nn, dis[i]))
                dis[i] -= a
                bikes[i] += a
                room[i] += a
            else:                                 # taller_retiro: la dañada sale del sistema
                a = int(min(nn, dis[i]))
                dis[i] -= a
                room[i] += a
            dmg[kd][0] += a
            dmg[kd][1] += nn - a
            damage_log.append((t_ns, s, kd, nn, a))
        elif kind == "orders":
            apply_batch(t_ns, batches.pop(p))
        elif kind == "decide":
            t = p
            st = pd.DataFrame({
                "short_name": names, "bikes": bikes.copy(), "disabled": dis.copy(),
                "docks": room - bikes, "cap": cap.copy(), "out_of_service": oos.copy(),
            })
            state = K.SimState(t=t, stations=st, warehouse=A - R)
            new = policy.decide(t, state, list(pending), forecast, params) or []
            eff = t + timedelta(minutes=lead_min)
            for o in new:
                if not isinstance(o, K.Order):
                    raise TypeError(f"policy.decide devolvió {type(o).__name__}, se esperaba Order")
                if o.issued_at != t or o.effective_at != eff:
                    raise ValueError(f"orden con issued_at/effective_at ≠ ({t}, {eff}): {o}")
                pending.append(o)
                schedule(o)
        elif kind == "sample":
            m = p
            bikes_min[m] = bikes
            dis_min[m] = dis
            empty_min[m] = bikes == 0
            full_min[m] = bikes >= room
        elif kind == "record":
            rec[p] = bikes
            rec_dis[p] = dis

    # --- conservación -------------------------------------------------------
    # disponibles: estaciones + tránsito − bodega + fugas a desconocidas
    #              + dañadas netas (daño − reparación) = constante
    total0 = bikes0_total + transit0
    total1 = (int(bikes.sum()) + in_transit - (A - R) + arrivals_unknown - inflow_unknown
              + dmg["daño"][0] - dmg["reparacion"][0])
    if total0 != total1:
        raise AssertionError(f"conservación rota: {total0} ≠ {total1}")
    if dis0_total + dmg["daño"][0] - dmg["reparacion"][0] - dmg["taller_retiro"][0] != int(dis.sum()):
        raise AssertionError("conservación de dañadas rota")

    # --- métricas -------------------------------------------------------------
    minutes = pd.date_range(start, periods=n_min, freq="min")
    hours = minutes.hour.to_numpy()
    hours = np.where(hours < 5, hours + 24, hours)   # 00:00–00:30 → 24 (STATION_HOURS)
    rows = []
    for h in np.unique(hours):
        sel = hours == h
        rows.append(pd.DataFrame({
            "short_name": names, "hour": int(h),
            "E": empty_min[sel].sum(axis=0).astype("int64"),
            "F": full_min[sel].sum(axis=0).astype("int64"),
        }))
    station_hour = pd.concat(rows, ignore_index=True)[K.STATION_HOUR_COLUMNS]

    det = pd.DataFrame(detours, columns=["t_ns", "kind", "orig", "to", "dist_m"])
    det["t"] = pd.Timestamp(start) + pd.to_timedelta(det.pop("t_ns"), unit="ns")
    det = det[["t", "kind", "orig", "to", "dist_m"]]
    dep_d, arr_d = det[det["kind"] == "dep"], det[det["kind"] == "arr"]

    dl = pd.DataFrame(damage_log, columns=["t_ns", "short_name", "kind", "n", "applied"])
    dl.insert(0, "t", pd.Timestamp(start) + pd.to_timedelta(dl.pop("t_ns"), unit="ns"))

    metrics = {
        "E": int(empty_min.sum()),
        "F": int(full_min.sum()),
        "trips_served": int(trips_served),
        "detours_dep": int(len(dep_d)),
        "detours_arr": int(len(arr_d)),
        "walk_m_dep": float(dep_d["dist_m"].sum()),
        "walk_m_arr": float(arr_d["dist_m"].sum()),
        "moves": int(moves),
        "stations_touched": int(len(touched)),
        "A": int(A),
        "R": int(R),
        "rebalanced": int(min(A, R)),
        "warehouse": int(A - R),
        "clipped": int(clipped),
        "arrivals_unknown": int(arrivals_unknown),
        "recorte_bodega": int(cut_wh_total),
        "recorte_por_movimiento": int(cut_move_total),
        "danos_aplicados": int(dmg["daño"][0]),
        "danos_no_aplicables": int(dmg["daño"][1]),
        "taller_aplicado": int(dmg["taller_retiro"][0]),
        "taller_no_aplicable": int(dmg["taller_retiro"][1]),
    }
    extra = {
        "detours": det,
        "orders_applied": pd.DataFrame(applied_log, columns=[
            "issued_at", "effective_at", "short_name", "delta", "applied",
            "recorte_por_movimiento", "recorte_fuera_de_servicio", "recorte_fisico", "recorte_bodega"]),
        "damage_applied": dl,                   # (t, short_name, kind, n, applied)
        "stations": names,
        "minutes": minutes,
        "bikes_minute": bikes_min,              # (minuto, estación) bicis disponibles
        "disabled_minute": dis_min,             # (minuto, estación) dañadas
        "record_times": rec_times,
        "bikes_record": rec,                    # (record_at, estación) disponibles
        "disabled_record": rec_dis,             # (record_at, estación) dañadas
        "decision_times": decide_times,
        "n_departures_unknown": int(n_dep_unknown),
        "inflow_unknown": int(inflow_unknown),
        "n_arrivals": int(n_arr),
        "n_arrivals_after_end": int(n_arr_after_end),
        "dep_failed": int(dep_failed),
        "arr_failed": int(arr_failed),
        "detours_dep_far": int((dep_d["dist_m"] > C.DETOUR_RADIUS_M).sum()),
        "detours_no_coords": int(n_no_coords),   # desvíos desde estación sin coordenadas (dist NaN)
        "orders_outside_window": int(n_orders_outside),
        "damage_outside_window": int(n_damage_outside),
        "reparaciones_aplicadas": int(dmg["reparacion"][0]),
        "reparaciones_no_aplicables": int(dmg["reparacion"][1]),
        "recorte_fuera_de_servicio": int(cut_oos_total),
        "tope_bodega": tope_bodega,
        "max_bikes_per_move": max_move,
        "bodega_min": int(wh_min),              # A − R mínimo al cerrar un lote
        "bodega_max": int(wh_max),              # A − R máximo al cerrar un lote
        "transit_start": int(transit0),
        "transit_end": int(in_transit),
        "E_out_of_service": int(empty_min[:, oos].sum()),
        "F_out_of_service": int(full_min[:, oos].sum()),
    }
    res = K.DayResult(day=str(day) if day is not None else str(start.date()), arm=arm,
                      metrics=metrics, station_hour=station_hour, extra=extra)
    return K.validate_day_result(res)


def run_day(day, orders=None, policy=None, lead_min: int = C.LEAD_MIN, forecast=None,
            params: K.PolicyParams | None = None, record_at=None, arm: str | None = None,
            damage_events="auto") -> K.DayResult:
    """Simula un día real 05:30–00:30 con los viajes de `data.trips(day)`.

    Sin `orders` ni `policy` es el baseline sin rebalanceo. `damage_events`:
    `"auto"` (por defecto) lee `data/derived/ecosim/damage_events.parquet` si
    existe; `"fixed"` reproduce las dañadas fijas del run 1; o un DataFrame
    `DamageEvents`. La fuente queda en `extra["damage_source"]`.
    """
    from ecosim import data
    d = C.as_date(day)
    start, end = C.day_bounds(d)
    ev, source = load_damage_events(d, damage_events)
    res = simulate(
        data.initial_state(d), data.trips(d), data.neighbors(d), start, end,
        orders=orders, policy=policy, lead_min=lead_min, forecast=forecast,
        params=params, record_at=record_at, day=d.isoformat(), arm=arm, damage_events=ev,
    )
    res.extra["damage_source"] = source
    return res


# ======================================================================
# CLI
# ======================================================================

ARMS = {   # brazo → (when, variant, undo) del replay
    "ecobici": ("t0", "rebal", False),
    "ecobici_t1": ("t1", "rebal", False),
    "ecobici_undo": ("t0", "rebal", True),
    "ecobici_stock": ("t0", "stock", None),
    "ecobici_avail": ("t0", "avail", None),
}


def resolve_damage(arm: str, damage: str | None) -> str:
    """Modo de dañadas de un brazo de la CLI. `None` = el de su variante
    (`REPLAY_DAMAGE`; `"auto"` para el baseline). Pedir dañadas dinámicas
    con `ecobici_stock` o `ecobici_avail` es un error (doble conteo)."""
    variant = ARMS[arm][1] if arm in ARMS else None
    default = REPLAY_DAMAGE[variant] if variant else "auto"
    if damage is None:
        return default
    if damage == "auto" and default == "fixed":
        raise ValueError(f"--arm {arm} va con dañadas fijas: con dañadas dinámicas contaría dos veces "
                         f"{'el taller' if variant == 'stock' else 'daños y reparaciones'}")
    return damage


def main(argv=None):
    ap = argparse.ArgumentParser(description="Simula un día (05:30–00:30) e imprime sus métricas.")
    ap.add_argument("--day", required=True)
    ap.add_argument("--arm", default="baseline", choices=["baseline", *ARMS])
    ap.add_argument("--damage", default=None, choices=["auto", "fixed"],
                    help="dañadas dinámicas (damage_events.parquet si existe) o fijas (run 1); "
                         "por defecto auto, salvo ecobici_stock y ecobici_avail, que van siempre fijas")
    a = ap.parse_args(argv)
    try:
        damage = resolve_damage(a.arm, a.damage)
    except ValueError as e:
        ap.error(str(e))
    t = _time.perf_counter()
    if a.arm == "baseline":
        r = run_day(a.day, arm="baseline", damage_events=damage)
    else:
        when, variant, undo = ARMS[a.arm]
        r = run_day(a.day, orders=ecobici_orders(a.day, when=when, variant=variant, undo=undo),
                    arm=a.arm, damage_events=damage)
    secs = _time.perf_counter() - t
    x = r.extra
    out = {"day": r.day, "arm": r.arm, "damage_source": x["damage_source"], **r.metrics,
           "n_orders": int(len(x["orders_applied"])),
           "reparaciones_aplicadas": x["reparaciones_aplicadas"],
           "reparaciones_no_aplicables": x["reparaciones_no_aplicables"],
           "detours_dep_far": x["detours_dep_far"],
           "detours_no_coords": x["detours_no_coords"],
           "dep_failed": x["dep_failed"], "arr_failed": x["arr_failed"],
           "n_arrivals_after_end": x["n_arrivals_after_end"],
           "E_out_of_service": x["E_out_of_service"],
           "F_out_of_service": x["F_out_of_service"],
           "seconds": round(secs, 2)}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    main(sys.argv[1:])
