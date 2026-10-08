"""Asignador del run 3: bodega estricta, recogida a t+15 y entrega a t+60.

Cada decisión es independiente. El pronóstico sólo se consulta mediante
ForecastTable.table(t, n_hours); las órdenes pendientes son su única memoria.
El costo de cada alternativa (recoger k o entregar x bicis en una estación)
se calcula con flujo fluido recortado minuto a minuto, como en el run 2, y
`greedy.greedy` elige con esas tablas qué estaciones recogen y entregan.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from ecosim import config as C
from ecosim.contracts import Order, PolicyParams, SimState, validate_forecast_table
from ecosim.greedy import greedy


def fluid_project(b0, K, net, m0, m1, adds=None):
    """Stock justo antes del minuto m1, con órdenes al inicio de cada minuto."""
    s = np.asarray(b0, float).copy()
    K = np.asarray(K, float)
    adds = adds or {}
    for m in range(m0, m1):
        if m in adds:
            s = np.clip(s + adds[m], 0, K)
        s = np.clip(s + net[:, m], 0, K)
    return s


def station_costs(K, net, m0, m1):
    """Costo sin pendientes para cada stock entero inicial, útil en diagnósticos."""
    K = np.asarray(K, int)
    y = np.arange(max(K, default=0) + 1)[None, :]
    s = np.minimum(y.astype(float), K[:, None])
    cost = np.zeros_like(s)
    for m in range(m0, m1):
        s = np.clip(s + net[:, m, None], 0, K[:, None])
        cost += (s < 0.5) + (s > K[:, None] - 0.5)
    cost[K == 0] = 0
    cost = np.where(y <= K[:, None], cost, np.inf)
    return cost


def _option_costs(stock, K, net, start, end, pending, cap, sign):
    """Costo para aplicar 0..cap bicis en start; pendientes posteriores incluidos.

    Los pendientes de start ya están en stock. Se cuenta E/F tras el flujo de
    cada minuto, igual que el sustituto del run 2.
    """
    amounts = np.arange(cap + 1, dtype=float)
    s = np.clip(stock[:, None] + sign * amounts, 0, K[:, None])
    cost = np.zeros_like(s)
    live = K > 0
    for m in range(start, end):
        if m in pending:
            s = np.clip(s + pending[m][:, None], 0, K[:, None])
        s = np.clip(s + net[:, m, None], 0, K[:, None])
        cost += ((s < 0.5) + (s > K[:, None] - 0.5)) * live[:, None]
    return cost


@dataclass
class Plan:
    names: np.ndarray
    K: np.ndarray
    pickup_stock: np.ndarray
    delivery_stock: np.ndarray
    r: np.ndarray
    u: np.ndarray
    pickup_cost: np.ndarray
    delivery_cost: np.ndarray
    pickup: np.ndarray
    delivery: np.ndarray
    # (receptor, x, [(donante, k), ...]) con índices de `names`, en el orden del greedy.
    paquetes: list
    status: str
    total_s: float

    @property
    def x(self):
        return self.delivery - self.pickup

    def surrogate(self, pickup=None, delivery=None):
        """Costo incremental del sustituto (sin precio por visita)."""
        pickup = self.pickup if pickup is None else pickup
        delivery = self.delivery if delivery is None else delivery
        result = 0.0
        for i in range(len(self.names)):
            if pickup[i]:
                result += self.pickup_cost[i, pickup[i]]
            if delivery[i]:
                result += self.delivery_cost[i, delivery[i]]
        return float(result)


class Asignador:
    """Política greedy; ninguna agenda histórica ni acceso a viajes futuros."""

    def __init__(self, coords: dict | None = None):
        """`coords`: {short_name: (lat, lon)}, solo para alfa > 0. Sin ella se usan las
        coordenadas de `data.stations(día)`; en vivo, las del feed."""
        self.last_plan: Plan | None = None
        self.coords = coords
        self._D = None

    def _distancias(self, names, t) -> np.ndarray:
        """Matriz de km en línea recta (haversine) entre estaciones; NaN si falta una coordenada."""
        key = (C.window_day(t), tuple(names))
        if self._D is not None and self._D[0] == key:
            return self._D[1]
        if self.coords is not None:
            xy = [self.coords.get(str(nm), (np.nan, np.nan)) for nm in names]
        else:
            from ecosim import data
            st = data.stations(key[0]).drop_duplicates("short_name")
            st = st.assign(short_name=st.short_name.astype(str)).set_index("short_name")
            xy = [(st.lat.get(str(nm), np.nan), st.lon.get(str(nm), np.nan)) for nm in names]
        c = np.radians(np.array(xy, float))
        lat, lon = c[:, 0][:, None], c[:, 1][:, None]
        h = np.sin((lat - lat.T) / 2) ** 2 + np.cos(lat) * np.cos(lat.T) * np.sin((lon - lon.T) / 2) ** 2
        D = 2 * 6371.0 * np.arcsin(np.sqrt(h))
        self._D = (key, D)
        return D

    def decide(self, t, state: SimState, pending_orders, forecast, params: PolicyParams) -> list[Order]:
        """Una orden por estación visitada; `paquete` = 1..P dentro de la decisión."""
        t = pd.Timestamp(t).to_pydatetime()
        plan = self.plan(t, state, pending_orders, forecast, params)
        self.last_plan = plan
        if plan is None:
            return []
        number = {}
        for p, (receptor, _, donors) in enumerate(plan.paquetes, start=1):
            number[receptor] = p
            for j, _ in donors:
                number[j] = p
        pickup_at = t + timedelta(minutes=params.pickup_min)
        delivery_at = t + timedelta(minutes=params.delivery_min)
        return [Order(t, pickup_at, delivery_at, str(name), int(delta), number[i])
                for i, (name, delta) in enumerate(zip(plan.names, plan.x)) if delta]

    def plan(self, t, state: SimState, pending_orders, forecast, params: PolicyParams) -> Plan | None:
        started = time.perf_counter()
        t = pd.Timestamp(t)
        day0, day_end = map(pd.Timestamp, C.day_bounds(C.window_day(t)))
        if t + pd.Timedelta(minutes=params.delivery_min) >= day_end or params.n_hours == 1:
            return None
        if forecast is None:
            raise ValueError("asignador: decisión sin pronóstico")
        st = state.stations
        names = st.short_name.astype(str).to_numpy()
        b = st.bikes.to_numpy(float)
        K = (st.bikes + st.docks).to_numpy(int)
        table = validate_forecast_table(forecast, t.to_pydatetime(), params.n_hours, len(names))
        net = np.repeat((table[:, :, 1] - table[:, :, 0]) / 15.0, 15, axis=1)
        horizon = min(params.n_hours * 60, int((day_end - t).total_seconds() // 60))
        pickup_min, delivery_min = params.pickup_min, params.delivery_min
        # Órdenes ya emitidas: ambas piernas se aplican en su hora efectiva.
        pending = {}
        pos = {nm: i for i, nm in enumerate(names)}
        for order in pending_orders or []:
            i = pos.get(str(order.short_name))
            if i is None:
                continue
            effective = order.pickup_at if order.delta < 0 else order.delivery_at
            m = int((pd.Timestamp(effective) - t).total_seconds() // 60)
            if 0 <= m < horizon:
                pending.setdefault(m, np.zeros(len(names)))[i] += order.delta
        p = fluid_project(b, K, net, 0, pickup_min, pending)
        if pickup_min in pending:
            p = np.clip(p + pending[pickup_min], 0, K)
        d = fluid_project(b, K, net, 0, delivery_min, pending)
        if delivery_min in pending:
            d = np.clip(d + pending[delivery_min], 0, K)
        cap = params.max_bikes_per_visit
        # sigma Poisson sobre el flujo esperado antes de cada operación.
        sigma_p = np.sqrt(table[:, : (pickup_min + 14) // 15, :].sum(axis=(1, 2))) if params.retiro else 0
        sigma_d = np.sqrt(table[:, : (delivery_min + 14) // 15, :].sum(axis=(1, 2))) if params.retiro else 0
        r = np.maximum(0, np.minimum(cap, np.floor(p - params.retiro * sigma_p + 1e-9))).astype(int)
        u = np.maximum(0, np.minimum(cap, np.floor(K - d - params.retiro * sigma_d + 1e-9))).astype(int)
        if "out_of_service" in st:
            bad = st.out_of_service.fillna(False).to_numpy(bool)
            r[bad], u[bad] = 0, 0
        # Pendientes en el minuto de actuación ya se incorporaron a p/d.
        rp = {m: v for m, v in pending.items() if m > pickup_min}
        rd = {m: v for m, v in pending.items() if m > delivery_min}
        pc = _option_costs(p, K, net, pickup_min, horizon, rp, cap, -1)
        dc = _option_costs(d, K, net, delivery_min, horizon, rd, cap, 1)
        pc -= pc[:, :1]
        dc -= dc[:, :1]
        pickup = np.zeros(len(names), int)
        delivery = np.zeros(len(names), int)
        paquetes, status = [], "noop"
        if params.visits_per_decision >= 2 and r.any() and u.any():
            D = self._distancias(names, t) if params.alfa > 0 else None
            pickup, delivery, paquetes = greedy(r, u, pc, dc, params.lam, params.visits_per_decision, params.alfa, D)
            status = "greedy"
        return Plan(names, K, p, d, r, u, pc, dc, pickup, delivery, paquetes,
                    status, time.perf_counter() - started)


def flow_sim(b0, K, dep, arr, m0, m1, orders=None, count_from=None):
    """Simulador de flujo propio con demanda entera, sin acceso al simulador del run."""
    s = np.asarray(b0, int).copy()
    K = np.asarray(K, int)
    E = F = 0
    for m in range(m0, m1):
        if orders and m in orders:
            s = np.clip(s + orders[m], 0, K)
        s = np.clip(s + arr[:, m] - dep[:, m], 0, K)
        if m >= (m0 if count_from is None else count_from):
            E += int(((s == 0) & (K > 0)).sum())
            F += int(((s == K) & (K > 0)).sum())
    return s, E, F


def validate(out_dir=None):
    """Correlación del sustituto y tiempos por decisión en fixtures reproducibles.

    No importa pronóstico, datos ni simulador de otros workers. La demanda real
    se genera independientemente de su tabla esperada.
    """
    from ecosim.contracts import ForecastTable

    out = Path(out_dir) if out_dir else C.REPO_ROOT / "ecosim/results/asignador"
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20261002)
    nstations = 700
    names = [f"S{i:03d}" for i in range(nstations)]
    K = np.full(nstations, 24, int)
    b = rng.integers(3, 22, nstations)
    # Direcciones persistentes y picos moderados: un caso cercano a un
    # problema operativo, no un oráculo que conoce cada viaje.
    tendencies = rng.uniform(-5, 5, nstations)
    departures = np.clip(6 - tendencies, 0.3, None)
    arrivals = np.clip(6 + tendencies, 0.3, None)
    day = "2025-08-13"
    start = pd.Timestamp(C.day_bounds(day)[0])
    table6 = np.stack([np.repeat(departures[:, None] * 15 / 60, 24, axis=1),
                       np.repeat(arrivals[:, None] * 15 / 60, 24, axis=1)], axis=-1)

    class FixtureForecast:
        forma = "directa"
        modelo = "fixture"
        def table(self, t, n_hours):
            return table6[:, :4*n_hours, :].copy()

    fc: ForecastTable = FixtureForecast()
    # Cuartos reales de Poisson independientes; minuto dentro del cuarto al azar.
    dep = np.zeros((nstations, 360), int)
    arr = np.zeros_like(dep)
    for m in range(360):
        dep[:, m] = rng.poisson(departures / 60)
        arr[:, m] = rng.poisson(arrivals / 60)
    rows = []
    for n in (2, 4, 6):
        for offset in (0, 45, 90, 135):
            t = (start + pd.Timedelta(minutes=offset)).to_pydatetime()
            stock, _, _ = flow_sim(b, K, dep, arr, 0, offset)
            st = pd.DataFrame({"short_name": names, "bikes": stock, "docks": K-stock,
                               "disabled": 0, "cap": K})
            state = SimState(t, st)
            for lam in (10, 30, 60):
                p = PolicyParams(n_hours=n, lam=lam)
                a = Asignador()
                plan = a.plan(t, state, [], fc, p)
                rows.append({"n": n, "lam": lam, "t": t.strftime("%H:%M"),
                             "seconds": plan.total_s, "status": plan.status,
                             "visits": int(np.count_nonzero(plan.x))})
                if lam == 10:
                    # Cada propuesta factible se evalúa en el flujo real desde t;
                    # costo sustituto y E+F se comparan como mejoras frente a noop.
                    candidates = [(plan.pickup.copy(), plan.delivery.copy())]
                    candidates += [(np.zeros(nstations, int), np.zeros(nstations, int))]
                    for fraction in (0.25, 0.5, 0.75):
                        sel = rng.random(nstations) < fraction
                        candidates.append((plan.pickup * sel, plan.delivery * sel))
                    for count in (10, 30, 80):
                        for _ in range(2):
                            pick = np.zeros(nstations, int)
                            deliver = np.zeros(nstations, int)
                            for i in rng.choice(nstations, size=count, replace=False):
                                if rng.random() < 0.5 and plan.r[i]:
                                    pick[i] = rng.integers(1, plan.r[i] + 1)
                                elif plan.u[i]:
                                    deliver[i] = rng.integers(1, plan.u[i] + 1)
                            candidates.append((pick, deliver))
                    # Propuestas parciales y aleatorias pueden desbalancearse:
                    # sólo validan el costo, nunca se emiten como órdenes.
                    for pick, deliver in candidates:
                        orders = {offset + p.pickup_min: -pick,
                                  offset + p.delivery_min: deliver}
                        _, E, F = flow_sim(stock, K, dep, arr, offset,
                                            min(offset + n*60, 360), orders=orders,
                                            count_from=offset + p.pickup_min)
                        rows[-1].setdefault("candidates", []).append((plan.surrogate(pick, deliver), E+F))
    from collections import defaultdict
    groups = defaultdict(list)
    for row in rows:
        if "candidates" in row:
            sub, actual = np.array(row["candidates"]).T
            rho = spearmanr(sub, actual).statistic if len(np.unique(sub)) > 1 and len(np.unique(actual)) > 1 else np.nan
            groups[row["n"]].append(rho)
    valid = [v for vals in groups.values() for v in vals if np.isfinite(v)]
    lines = ["# Validación del asignador — run 3", "",
             "Fixture reproducible: 700 estaciones, flujo Poisson independiente del pronóstico; "
             "el simulador de flujo recorta a [0, K] y aplica las dos piernas en t+15 y t+60. "
             "Se comparan planes greedy, sin movimiento, fracciones y alternativas aleatorias "
             "(estas últimas sólo para validar el costo, no son decisiones factibles).", "",
             f"Spearman entre costo incremental pronosticado y E+F real: mediana "
             f"{np.median(valid):.3f} en {len(valid)} grupos definidos.",
             "", "| n | mediana Spearman |", "|---:|---:|"]
    lines += [f"| {n} | {np.nanmedian(groups[n]):.3f} |" for n in (2, 4, 6)]
    lines += ["", "| n | λ | decisiones | visitas mediana | mediana s | p95 s | máximo s |",
              "|---:|---:|---:|---:|---:|---:|---:|"]
    for n in (2, 4, 6):
        for lam in (10, 30, 60):
            subset = [r for r in rows if r["n"] == n and r["lam"] == lam]
            secs = np.array([r["seconds"] for r in subset])
            lines.append(f"| {n} | {lam} | {len(subset)} | {np.median([r['visits'] for r in subset]):.1f} | {np.median(secs):.3f} | "
                         f"{np.quantile(secs, .95):.3f} | {secs.max():.3f} |")
    lines += ["", "Supuesto: las dañadas presentes en t ocupan anclajes durante la proyección; "
              "eventos futuros no son visibles. Las órdenes pendientes se aplican en su hora "
              "y se recortan en la proyección; no se presupone capacidad de bodega.", ""]
    path = out / "REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(path)
    print("\n".join(lines[-3:-1]))
    return path


if __name__ == "__main__":
    if sys.argv[1:] == ["validate"]:
        validate()
    else:
        print("uso: uv run python -m ecosim.asignador validate")
