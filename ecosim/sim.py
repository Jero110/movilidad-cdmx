"""Simulación conservativa de Ecobici, ventana [05:00, 00:30).

Las decisiones equilibradas recogen en pickup_at y entregan en delivery_at.
Cada lote (decisión, paquete) tiene su propia carga y equilibra recogidas y
entregas: las bicis de los donantes de un paquete solo van a su receptor. Al
recoger se lleva solo lo que haya; si faltan bicis, las entregas del lote se
reducen por resto mayor, ordenadas por estación (y orden original en empates).
Lo que no cabe en un destino (o todo, si está fuera de servicio) se deja en la
estación con anclaje libre más cercana a ese destino; nunca vuelve al origen.
Una orden de política a una estación que no existe en el día es un error.
El replay es una medición, no una política: sus movimientos pueden tener neto
no nulo; ese neto se contabiliza explícitamente como flujo externo observado.
"""
from __future__ import annotations

import argparse
import heapq
import json
import time
from datetime import timedelta

import numpy as np
import pandas as pd

from ecosim import config as C
from ecosim import contracts as K

DAMAGE_EVENTS_FILE = C.DERIVED / "damage_events.parquet"
MOVES_FILE = C.DERIVED / "ecobici_moves.parquet"
_NS_MIN = 60_000_000_000
# Registro de órdenes aplicadas (`res.extra["orders_applied"]`): `applied` es lo
# que ocurrió en la estación (negativo al recoger).
APPLIED_COLUMNS = K.ORDER_COLUMNS[:3] + ["short_name", "delta", "applied", "paquete"]
# Registro de reubicaciones (`res.extra["reubicaciones"]`): bicis que no cupieron
# en `destino` y se dejaron en `estacion`, la libre más cercana a ese destino.
RELOCATION_COLUMNS = ["issued_at", "delivery_at", "paquete", "destino", "estacion", "bicis", "dist_m"]
# Pares ejecutados (`pares_ejecutados`).
PAIR_COLUMNS = ["issued_at", "pickup_at", "delivery_at", "paquete", "origen", "destino", "bicis", "tipo", "dist_m"]


def _spread(total: int, caps: list[int]) -> list[int]:
    """Reparto entero proporcional por resto mayor; empate por índice."""
    size = sum(caps)
    if total < 0 or total > size:
        raise AssertionError("reparto fuera de capacidad")
    if not size:
        return [0] * len(caps)
    out = [total * c // size for c in caps]
    rem = [total * c % size for c in caps]
    for i in sorted(range(len(caps)), key=lambda i: -rem[i])[:total - sum(out)]:
        out[i] += 1
    return out


def ecobici_orders(day, when="t1", undo=False, path=None):
    """Movimientos observados (pares excluidos por defecto) al t1 del feed.

    `when='t0'` permite auditar el desfase de aplicación. Las órdenes del
    replay llevan ambos instantes iguales: no simulan camionetas.
    """
    if when not in ("t0", "t1"):
        raise ValueError("when debe ser t0 o t1")
    df = pd.read_parquet(path or MOVES_FILE)
    K.validate_ecobici_moves(df)
    start, end = C.day_bounds(day)
    df = df[(df[when] >= start) & (df[when] < end) & (df.delta != 0)]
    if not undo:
        df = df[~df.par]
    df = df.sort_values([when, "short_name"], kind="stable")
    return [K.Order(t.to_pydatetime(), t.to_pydatetime(), t.to_pydatetime(), str(s), int(d))
            for t, s, d in zip(df[when], df.short_name, df.delta)]


def load_damage_events(day, damage_events="auto", path=None):
    """Carga los cambios observados; nunca cae silenciosamente a dañadas fijas."""
    if damage_events is None or (isinstance(damage_events, str) and damage_events == "fixed"):
        return None, "fixed"
    if isinstance(damage_events, pd.DataFrame):
        df, source = damage_events, "DataFrame"
    elif damage_events == "auto":
        p = path or DAMAGE_EVENTS_FILE
        df, source = pd.read_parquet(p), str(p.name)
    else:
        raise ValueError("damage_events debe ser auto, fixed o DataFrame")
    K.validate_damage_events(df)
    start, end = C.day_bounds(day)
    return df[(df.t >= start) & (df.t < end)], source


def simulate(init, trips, nbrs, start, end, orders=None, policy=None, forecast=None,
             params=None, record_at=None, day=None, arm=None, damage_events=None,
             damage_variant="onsite", feed_moves=None, neutralize_replay=False) -> K.DayResult:
    """Motor único: eventos exactos y E/F muestreados tras eventos de cada minuto.

    `damage_variant='feed'` aplica entradas/salidas dañadas observadas como
    flujo exógeno idéntico para política, baseline y replay. `feed_moves`
    aporta los deltas de los intervalos, no depende de las órdenes emitidas.
    """
    if orders is not None and policy is not None:
        raise ValueError("orders y policy son excluyentes")
    if damage_variant not in ("onsite", "feed"):
        raise ValueError("damage_variant debe ser onsite o feed")
    if damage_variant == "feed" and feed_moves is None:
        raise ValueError("variante feed requiere feed_moves (EcobiciMoves), incluso con política")
    if neutralize_replay and orders is None:
        raise ValueError("neutralize_replay es solo diagnóstico de replay")
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    end_ns = int((end - start).value)
    n_min = end_ns // _NS_MIN
    def ns(t):
        return int((pd.Timestamp(t) - start).value)

    init = init.sort_values("short_name").reset_index(drop=True)
    names = init.short_name.astype(str).to_numpy()
    ix = {s: i for i, s in enumerate(names)}
    n = len(names)
    bikes = init.bikes.to_numpy(dtype=np.int64).copy()
    dis = init.disabled.to_numpy(dtype=np.int64).copy()
    cap = init.cap.to_numpy(dtype=np.int64)
    docks_dis = init.docks_disabled.to_numpy(dtype=np.int64)
    room = cap - docks_dis - dis
    oos = init.out_of_service.to_numpy(dtype=bool) if "out_of_service" in init else np.zeros(n, bool)
    if (bikes < 0).any() or (dis < 0).any() or (bikes > room).any():
        raise ValueError("estado inicial inválido")
    initial_total = int(bikes.sum() + dis.sum())
    nb = nbrs[nbrs.short_name.isin(ix) & nbrs.nbr.isin(ix)].sort_values(
        ["short_name", "dist_m", "nbr"], kind="stable")
    neighbors = [[] for _ in names]
    for s, g in nb.groupby("short_name"):
        neighbors[ix[s]] = [(ix[r.nbr], float(r.dist_m)) for r in g.itertuples()]
    def nearest(i, predicate, allow_self=False):
        if allow_self and predicate(i):
            return i, 0.0
        for j, d in neighbors[i]:
            if predicate(j):
                return j, d
        # Estación sin coordenadas: elegir determinísticamente y declarar NaN.
        for j in range(n):
            if j != i and predicate(j):
                return j, float("nan")
        raise ValueError("No hay estación apta para desvío o reubicación; imposible atender sin crear bicis")

    heap = []
    seq = 0
    def push(t, priority, kind, payload):
        nonlocal seq
        heapq.heappush(heap, (int(t), priority, seq, kind, payload))
        seq += 1

    if (trips.t_arr < trips.t_dep).any():
        raise ValueError("viaje con llegada antes de salida")
    transit = 0
    unknown_in = unknown_out = 0
    trip_served = 0
    dep_unknown = 0
    after_end = 0
    trip_ok = np.ones(len(trips), bool)
    for k, r in enumerate(trips.itertuples(index=False)):
        dep, arr = ns(r.t_dep), ns(r.t_arr)
        known_o = getattr(r, "o_known", r.o in ix) and r.o in ix
        known_d = getattr(r, "d_known", r.d in ix) and r.d in ix
        if dep < 0 and arr >= 0:
            if known_o:
                transit += 1
            else:
                trip_ok[k] = False  # la bici entra desde una estación no observada
        if 0 <= dep < end_ns:
            if known_o:
                push(dep, 4, "dep", (k, str(r.o)))
            else:
                trip_ok[k] = False
                dep_unknown += 1
        if 0 <= arr < end_ns:
            push(arr, 5 if dep == arr and dep >= 0 else 3, "arr", (k, str(r.d), known_d))
        elif 0 <= dep < end_ns and arr >= end_ns:
            after_end += 1
    transit_initial = transit

    events = damage_events
    if events is not None:
        K.validate_damage_events(events)
        for r in events.sort_values(["t", "short_name", "kind"]).itertuples(index=False):
            t = ns(r.t)
            if 0 <= t < end_ns:
                push(t, 0, "damage", (str(r.short_name), r.kind, int(r.n)))

    is_policy = policy is not None
    params = params or K.PolicyParams()
    batches = {}  # lote (decisión, paquete) → recogidas, entregas, carga
    observed_moves = {}  # (t1, estación) → delta TOTAL del feed (sin depender del brazo)
    if feed_moves is not None:
        K.validate_ecobici_moves(feed_moves)
        for row in feed_moves.itertuples(index=False):
            observed_moves[(ns(row.t1), str(row.short_name))] = int(row.delta)
    feed_replay_offset = {}  # misma entrada/salida exógena ya aplicada antes de replay
    replay_groups = {}  # t → órdenes del mismo instante para neutralización diagnóstica
    diagnostic_returns = []
    replay_nets = {}  # neto aplicado por instante, antes de neutralizar
    pending = []
    applied = []
    visited = set()
    cut = {"fisico": 0, "tope": 0, "fuera_servicio": 0, "entrega_sin_recogida": 0,
           "reubicacion_destino": 0, "visitas": 0}
    truck = 0
    truck_max = 0
    replay_external = 0
    damage_external = 0
    relocation_log = []  # (issued_at, delivery_at, paquete, destino, estación, bicis, dist_m)
    order_log = []
    def register(group):
        nonlocal seq
        if not group:
            return
        if is_policy:
            if len(group) > params.visits_per_decision:
                raise ValueError("visitas por decisión exceden tope")
            if len({(o.pickup_at, o.delivery_at) for o in group}) != 1:
                raise ValueError("tiempos distintos dentro de una decisión")
            missing = sorted({o.short_name for o in group if o.short_name not in ix})
            if missing:
                raise ValueError(f"orden de política a estación que no existe en el día: {missing}")
            # Un lote por paquete; paquete 0 = todas las órdenes sin paquete juntas.
            for number in sorted({o.paquete for o in group}):
                picks = [o for o in group if o.delta < 0 and o.paquete == number]
                gives = [o for o in group if o.delta > 0 and o.paquete == number]
                if not picks or sum(-o.delta for o in picks) != sum(o.delta for o in gives):
                    raise ValueError("cada decisión debe equilibrar recogidas y entregas en cada paquete")
                key = seq
                batches[key] = {"picks": picks, "gives": gives, "load": 0}
                push(ns(picks[0].pickup_at), 1, "pickup", key)
                push(ns(gives[0].delivery_at), 1, "delivery", key)
        else:
            for o in group:
                if 0 <= ns(o.pickup_at if o.delta < 0 else o.delivery_at) < end_ns:
                    tt = ns(o.pickup_at if o.delta < 0 else o.delivery_at)
                    replay_groups.setdefault(tt, []).append(o)
                    push(tt, 1, "replay", o)

    if orders is not None:
        oo = K.frame_to_orders(orders) if isinstance(orders, pd.DataFrame) else list(orders)
        register(oo)
        if neutralize_replay:
            for tt in replay_groups:
                push(tt, 2, "neutralize", tt)
    if is_policy:
        for t in C.decision_times(day or C.window_day(start)):
            if start <= pd.Timestamp(t) and pd.Timestamp(t) + timedelta(minutes=params.delivery_min) < end:
                push(ns(t), 2, "decide", t)

    minutes = pd.date_range(start, periods=n_min, freq="min")
    bikes_min = np.empty((n_min, n), np.int16)
    dis_min = np.empty((n_min, n), np.int16)
    rec_times = [pd.Timestamp(t) for t in (record_at or []) if 0 <= ns(t) < end_ns]
    record = np.zeros((len(rec_times), n), np.int16)
    record_dis = np.zeros_like(record)
    for i, t in enumerate(rec_times):
        push(ns(t), 6, "record", i)
    for m in range(n_min):
        push(m * _NS_MIN, 6, "sample", m)

    detours = []
    damage_log = []
    times_decision = []
    no_damage = 0
    moves_pick = moves_give = bikes_moved = 0
    def invariant(t):
        # Un cambio de flota del replay queda explícito como flujo observado:
        # nunca se confunde con carga disponible en una camioneta.
        actual = int(bikes.sum() + dis.sum() + transit + truck + unknown_out - unknown_in - replay_external - damage_external)
        if actual != initial_total + transit_initial:
            raise AssertionError(f"conservación rota en {start + pd.Timedelta(t, unit='ns')}: {actual} != {initial_total + transit_initial}")
        if truck < 0 or (bikes < 0).any() or (dis < 0).any() or (bikes > room).any():
            raise AssertionError("stock/camionetas físicamente inválidos")

    while heap:
        t, _, _, kind, p = heapq.heappop(heap)
        if kind == "damage":
            s, kd, count = p
            i = ix.get(s)
            delta = observed_moves.get((t, s), 0) if damage_variant == "feed" else 0
            requested_external = min(count, max(0, delta if kd == "sube" else -delta))
            if i is None:
                carried = 0
            elif kd == "sube":
                carried = min(requested_external, int(room[i] - bikes[i]))
                dis[i] += carried
                room[i] -= carried
                damage_external += carried
            else:
                carried = min(requested_external, int(dis[i]))
                dis[i] -= carried
                room[i] += carried
                damage_external -= carried
            if carried:
                feed_replay_offset[(t, s)] = feed_replay_offset.get((t, s), 0) + (carried if kd == "sube" else -carried)
            v = min(count - carried, int(bikes[i] if kd == "sube" else dis[i])) if i is not None else 0
            if kd == "sube":
                bikes[i] -= v
                dis[i] += v
                room[i] -= v
            else:
                dis[i] -= v
                bikes[i] += v
                room[i] += v
            no_damage += count - carried - v
            damage_log.append((start + pd.Timedelta(t, unit="ns"), s, kd, count, v + carried))
        elif kind == "replay":
            o = p
            i = ix.get(o.short_name)
            offset = feed_replay_offset.pop((t, o.short_name), 0)
            req = o.delta - offset  # no contar dos veces la dañada exógena
            if i is None:
                v = 0
            elif req > 0:
                v = min(req, int(room[i] - bikes[i]))
            else:
                v = -min(-req, int(bikes[i]))
            if i is not None:
                bikes[i] += v
            replay_external += v
            replay_nets[t] = replay_nets.get(t, 0) + v
            cut["fisico"] += abs(req) - abs(v)
            if v:
                visited.add(o.short_name)
                moves_give += int(v > 0)
                moves_pick += int(v < 0)
                bikes_moved += abs(v)
            order_log.append((o.issued_at, o.pickup_at, o.delivery_at, o.short_name, req, v, o.paquete))
        elif kind == "neutralize":
            # Contrafactual únicamente: redistribuye el exceso neto de cada
            # foto entre estaciones, sin flujo externo. Primero revierte en
            # estaciones del mismo signo; después recorre IDs alfabéticos.
            net = replay_nets.pop(t, 0)
            if net:
                primary = sorted({o.short_name for o in replay_groups[t] if o.delta * net > 0})
                candidates = primary + [s for s in names if s not in primary]
                remaining = abs(net)
                for s in candidates:
                    i = ix.get(s)
                    if i is None:
                        continue
                    quantity = min(remaining, int(bikes[i] if net > 0 else room[i] - bikes[i]))
                    if quantity:
                        bikes[i] -= quantity if net > 0 else -quantity
                        correction = -quantity if net > 0 else quantity
                        replay_external += correction
                        diagnostic_returns.append((start + pd.Timedelta(t, unit="ns"), s, correction))
                        remaining -= quantity
                    if not remaining:
                        break
                if remaining:
                    raise AssertionError("imposible neutralizar neto observado sin violar capacidad")
        elif kind == "pickup":
            b = batches[p]
            for o in b["picks"]:
                i = ix.get(o.short_name)
                limit = min(-o.delta, params.max_bikes_per_visit)
                cut["tope"] += -o.delta - limit
                if i is None or oos[i]:
                    cut["fuera_servicio"] += limit
                    v = 0
                else:
                    v = min(limit, int(bikes[i]))
                    bikes[i] -= v
                cut["fisico"] += limit - v if i is not None and not oos[i] else 0
                if v:
                    truck += v
                    b["load"] += v
                    moves_pick += 1
                    bikes_moved += v
                    visited.add(o.short_name)
                order_log.append((o.issued_at, o.pickup_at, o.delivery_at, o.short_name, o.delta, -v, o.paquete))
            truck_max = max(truck_max, truck)
            pending = [o for o in pending if o not in b["picks"]]
        elif kind == "delivery":
            b = batches[p]
            gives = b["gives"]
            limits = [min(o.delta, params.max_bikes_per_visit) for o in gives]
            for o, limit in zip(gives, limits):
                cut["tope"] += o.delta - limit
            # Distribución determinista independiente del orden de emisión.
            order_idx = sorted(range(len(gives)), key=lambda j: (gives[j].short_name, j))
            caps = [limits[j] for j in order_idx]
            allocation = _spread(min(b["load"], sum(caps)), caps)
            quotas = dict(zip(order_idx, allocation))
            cut["entrega_sin_recogida"] += sum(limits) - sum(allocation)
            leftover = []  # (destino, bicis que no se pudieron dejar ahí)
            delivered = 0
            for j, o in enumerate(gives):
                limit = quotas[j]
                i = ix[o.short_name]
                if oos[i]:
                    cut["fuera_servicio"] += limit
                    v = 0
                else:
                    v = min(limit, int(room[i] - bikes[i]))
                    bikes[i] += v
                    cut["fisico"] += limit - v
                if limit - v:
                    leftover.append((i, limit - v))
                truck -= v
                delivered += v
                if v:
                    moves_give += 1
                    bikes_moved += v
                    visited.add(o.short_name)
                order_log.append((o.issued_at, o.pickup_at, o.delivery_at, o.short_name, o.delta, v, o.paquete))
            # Carga por encima de los topes de entrega (órdenes que exceden el
            # tope por visita): también se queda junto al primer destino.
            excess = b["load"] - sum(allocation)
            if excess:
                leftover.append((ix[gives[order_idx[0]].short_name], excess))
            remainder = b["load"] - delivered
            # La camioneta está en el destino: lo que no cupo se deja en la estación
            # con anclaje libre más cercana a ese destino.
            for dest, quantity in leftover:
                while quantity and remainder:
                    j, dist = nearest(dest, lambda z: bikes[z] < room[z])
                    v = min(quantity, remainder, int(room[j] - bikes[j]))
                    bikes[j] += v
                    truck -= v
                    quantity -= v
                    remainder -= v
                    cut["reubicacion_destino"] += v
                    relocation_log.append((gives[0].issued_at, gives[0].delivery_at, gives[0].paquete,
                                           names[dest], names[j], v, dist))
            if remainder:
                raise AssertionError("quedaron bicis en camioneta sin anclaje cerca del destino")
            pending = [o for o in pending if o not in gives]
            del batches[p]
        elif kind == "decide":
            st = K.SimState(t=p, stations=pd.DataFrame({"short_name": names, "bikes": bikes.copy(),
                            "disabled": dis.copy(), "docks": room - bikes, "cap": cap,
                            "out_of_service": oos.copy()}), warehouse=truck)
            tic = time.perf_counter()
            new = policy.decide(p, st, list(pending), forecast, params) or []
            times_decision.append(time.perf_counter() - tic)
            for o in new:
                if o.issued_at != p or o.pickup_at != p + timedelta(minutes=params.pickup_min) or o.delivery_at != p + timedelta(minutes=params.delivery_min):
                    raise ValueError("orden fuera de los tiempos de la decisión")
            register(new)
            pending.extend(new)
        elif kind == "dep":
            k, origin = p
            i = ix[origin]
            if not bikes[i]:
                j, d = nearest(i, lambda z: bikes[z] > 0)
                detours.append((start + pd.Timedelta(t, unit="ns"), "dep", origin, names[j], d))
                i = j
            bikes[i] -= 1
            transit += 1
            trip_served += 1
        elif kind == "arr":
            k, dest, known = p
            i = ix[dest] if known else -1
            if i >= 0 and bikes[i] >= room[i]:
                j, d = nearest(i, lambda z: bikes[z] < room[z])
                detours.append((start + pd.Timedelta(t, unit="ns"), "arr", dest, names[j], d))
                i = j
            if trip_ok[k]:
                transit -= 1
            else:
                unknown_in += 1
            if i < 0:
                unknown_out += 1
            else:
                bikes[i] += 1
        elif kind == "sample":
            bikes_min[p] = bikes
            dis_min[p] = dis
        elif kind == "record":
            record[p] = bikes
            record_dis[p] = dis
        invariant(t)

    if truck or batches:
        raise AssertionError("camionetas no vacías al cierre")
    empty = bikes_min == 0
    full = bikes_min + dis_min + docks_dis == cap
    hours = np.where(minutes.hour < 5, minutes.hour + 24, minutes.hour)
    station_hour = pd.concat([pd.DataFrame({"short_name": names, "hour": int(h),
                        "E": empty[hours == h].sum(axis=0).astype(int),
                        "F": full[hours == h].sum(axis=0).astype(int)}) for h in sorted(set(hours))], ignore_index=True)
    det = pd.DataFrame(detours, columns=["t", "kind", "orig", "to", "dist_m"])
    distance = det.dist_m.dropna()
    # Pares exactos de visitas sucesivas en la misma estación y a un paso
    # de decisión: se quitan solo del esfuerzo, nunca del estado físico.
    paired = set()
    if is_policy:
        by_station = {}
        for j, (issued, pick, deliver, s, requested, applied_n, _) in enumerate(order_log):
            if applied_n:
                at = pick if applied_n < 0 else deliver
                by_station.setdefault(s, []).append((pd.Timestamp(at), j, applied_n))
        for visits in by_station.values():
            visits.sort(key=lambda x: (x[0], x[1]))
            for a, b in zip(visits, visits[1:]):
                if a[1] not in paired and b[1] not in paired and b[0] - a[0] == pd.Timedelta(minutes=C.STEP_MIN) and a[2] == -b[2]:
                    paired.update((a[1], b[1]))
    paired_picks = sum(order_log[j][5] < 0 for j in paired)
    paired_gives = len(paired) - paired_picks
    metrics = dict(E=int(empty.sum()), F=int(full.sum()), visitas=moves_pick + moves_give - len(paired),
                   visitas_recoger=moves_pick - paired_picks, visitas_entregar=moves_give - paired_gives,
                   bicis_movidas=int(bikes_moved), en_transito_max=int(truck_max),
                   recortes=cut, desvios_salida=int((det.kind == "dep").sum()),
                   desvios_llegada=int((det.kind == "arr").sum()),
                   km_desvio_medio=float(distance.mean() / 1000) if len(distance) else 0.,
                   danadas_no_aplicables=int(no_damage))
    res = K.DayResult(day=str(day or C.window_day(start)), arm=arm or ("policy" if is_policy else "ecobici" if orders is not None else "baseline"),
                      EF=metrics["E"] + metrics["F"], station_hour=station_hour,
                      tiempos_decision=times_decision, **metrics)
    res.extra = {"stations": names, "minutes": minutes, "bikes_minute": bikes_min,
                 "disabled_minute": dis_min, "record_times": rec_times,
                 "bikes_record": record, "disabled_record": record_dis,
                 "detours": det, "desvios_gt500": int((det.dist_m > 500).sum()),
                 "desvios_gt500_fraccion": float((det.dist_m > 500).sum() / len(det)) if len(det) else 0.,
                 "orders_applied": pd.DataFrame(order_log, columns=APPLIED_COLUMNS),
                 "damage_applied": pd.DataFrame(damage_log, columns=["t", "short_name", "kind", "n", "applied"]),
                 "reubicaciones": pd.DataFrame(relocation_log, columns=RELOCATION_COLUMNS),
                 "truck_end": truck, "transit_start": transit_initial,
                 "transit_end": transit, "unknown_in": unknown_in, "unknown_out": unknown_out,
                 "replay_external": replay_external, "damage_external": damage_external,
                 "replay_neto": replay_external, "neto_externo": replay_external + damage_external,
                 "neutralization": diagnostic_returns, "trips_served": trip_served,
                 "departures_unknown": dep_unknown, "arrivals_after_end": after_end,
                 "initial_total": initial_total + transit_initial,
                 "visitas_sin_regla": moves_pick + moves_give, "pares_visitas": len(paired)}
    return K.validate_day_result(res)


def pares_ejecutados(res: K.DayResult) -> pd.DataFrame:
    """Lo que el simulador movió de dónde a dónde, por (decisión, paquete).

    Una fila por recogida aplicada: `origen` = donante, `destino` = receptor de
    su lote, `bicis` = las que se recogieron ahí (`tipo = "traslado"`), y una
    fila por reubicación: `origen` = receptor que no tenía lugar, `destino` =
    estación libre más cercana a él, con su distancia (`tipo = "reubicacion"`).
    Las bicis entregadas en el receptor son traslados − reubicaciones desde él.

    Con paquetes (un receptor por lote) los pares son exactos. En un lote de
    paquete 0 con varios receptores las bicis son intercambiables: los
    traslados se asignan llenando receptores en orden de registro con
    donantes en orden de registro. El replay de Ecobici (órdenes sin
    camioneta) no tiene pares: devuelve una tabla vacía.
    """
    oa = res.extra["orders_applied"]
    rows = []
    if len(oa) and (oa.pickup_at != oa.delivery_at).any():
        for (issued, number), g in oa.groupby(["issued_at", "paquete"], sort=True):
            picks = g[g.applied < 0]
            gives = g[g.delta > 0]
            moved = res.extra["reubicaciones"]
            moved = moved[(moved.issued_at == issued) & (moved.paquete == number)]
            # Lo que llegó a cada receptor: entregado ahí + dejado junto a él.
            want = [[r.short_name, int(max(r.applied, 0)) + int(moved.loc[moved.destino == r.short_name, "bicis"].sum())]
                    for r in gives.itertuples(index=False)]
            for r in picks.itertuples(index=False):
                left = -int(r.applied)
                for w in want:
                    if not left:
                        break
                    k = min(left, w[1]) if w is not want[-1] else left
                    if k:
                        rows.append((issued, r.pickup_at, r.delivery_at, int(number), r.short_name, w[0], k, "traslado", np.nan))
                        w[1] -= k
                        left -= k
            for r in moved.itertuples(index=False):
                rows.append((issued, picks.pickup_at.iloc[0] if len(picks) else r.delivery_at, r.delivery_at,
                             int(number), r.destino, r.estacion, int(r.bicis), "reubicacion", r.dist_m))
    out = pd.DataFrame(rows, columns=PAIR_COLUMNS)
    return out.astype({"paquete": "int64", "bicis": "int64", "dist_m": "float64"})


def run_day(day, orders=None, policy=None, forecast=None, params=None, record_at=None,
            arm=None, damage_events="auto", damage_variant="onsite", neutralize_replay=False):
    from ecosim import data
    start, end = C.day_bounds(day)
    ev, source = load_damage_events(day, damage_events)
    mv = None
    if damage_variant == "feed":
        mv = pd.read_parquet(MOVES_FILE)
        mv = mv[(mv.t1 >= start) & (mv.t1 < end)]
    res = simulate(data.initial_state(day), data.trips(day), data.neighbors(day), start, end,
                   orders=orders, policy=policy, forecast=forecast, params=params,
                   record_at=record_at, day=str(C.as_date(day)), arm=arm,
                   damage_events=ev, damage_variant=damage_variant, feed_moves=mv,
                   neutralize_replay=neutralize_replay)
    res.extra["damage_source"] = source
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="Simula 05:00–00:30 con viajes observados")
    ap.add_argument("--day", required=True)
    ap.add_argument("--arm", choices=["baseline", "ecobici"], default="baseline")
    ap.add_argument("--delivery-min", type=int, choices=C.DELIVERY_SENS_MIN, default=C.DELIVERY_MIN)
    ap.add_argument("--damage-variant", choices=["onsite", "feed"], default="onsite")
    a = ap.parse_args(argv)
    tic = time.perf_counter()
    orders = ecobici_orders(a.day) if a.arm == "ecobici" else None
    r = run_day(a.day, orders=orders, arm=a.arm, damage_variant=a.damage_variant,
                params=K.PolicyParams(delivery_min=a.delivery_min))
    out = {"day": r.day, "arm": r.arm, **r.metrics,
           "bicis": int(r.extra["initial_total"]),
           "desvios_gt500_fraccion": r.extra["desvios_gt500_fraccion"],
           "camionetas_cierre": r.extra["truck_end"], "flujo_externo_replay": r.extra["replay_external"],
           "flujo_externo_danadas": r.extra["damage_external"], "neto_externo": r.extra["neto_externo"],
           "seconds": round(time.perf_counter() - tic, 2)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


if __name__ == "__main__":
    main()
