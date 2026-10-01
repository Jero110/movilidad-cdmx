"""Asignador de rebalanceo (plan 2026-09-28-ecosim, sección 1.6; run 2:
plan 2026-09-28-ecosim2, subtask asignador2).

MILP (HiGHS vía `highspy`) de horizonte móvil. En cada decisión t:

1. **Pronóstico.** Se usa la emisión más reciente con `issued_at ≤ t` de la
   variante (`params.extra["variant"]`) y la serie (`params.refresh_min`)
   pedidas. Si no hay ninguna, o no cubre [t, t+L+H), `ValueError`.
2. **Proyección a t+L.** Desde el estado en t, un flujo fluido minuto a minuto
   con el pronóstico (tasa uniforme dentro de cada bloque) y las órdenes
   pendientes, recortado a [0, K_i], da las bicis esperadas p_i en t+L.
   p̂_i = round(p_i). K_i = bikes + docks libres del estado en t (lugar útil
   para bicis disponibles; las dañadas de t no cuentan y pueden cambiar).
3. **Costo por estación.** Para cada objetivo entero y ∈ {0..K_i} en t+L se
   corre el mismo flujo fluido recortado sobre [t+L, t+L+H) y se cuentan los
   minutos con stock < 0.5 (vacía) y > K_i − 0.5 (llena): c_i(y) = e_i(y) + f_i(y),
   en minutos-estación. E_sub + F_sub = Σ_i ĉ_i(y_i), con ĉ_i la envolvente
   convexa inferior de c_i sobre el rango permitido [p̂_i − r_i, p̂_i + u_i]
   (lineal por tramos → restricciones lineales).
4. **MILP** (una sola etapa: solo se decide lo que entra en t+L):

       min  Σ_i z_i + λ Σ_i m_i + μ w
       s.a. z_i ≥ c_i(a) + β_ik (y_i − a)              (tramos de la envolvente)
            y_i − p̂_i ≤ u_i m_i,  p̂_i − y_i ≤ r_i m_i   (cotas por orden)
            w ≥ ±Σ_i (y_i − p̂_i)                      (bicis de bodega de esta orden)
            Σ_i m_i ≤ tope_hora − máx_{ventanas de 60 min ∋ t+L} (órdenes ya emitidas)
            lo ≤ W + P + Σ_i (y_i − p̂_i) ≤ hi           (|bodega neta acumulada| ≤ tope)
            y_i ∈ {p̂_i − r_i .. p̂_i + u_i} ⊆ {0..K_i} entero, m_i ∈ {0,1}

   con W = state.warehouse (A − R aplicado hasta t), P = Σ deltas pendientes,
   r_i = min(⌊p_i⌋, max_bikes_per_move) (retiro máximo: nunca más bicis que
   las proyectadas en t+L) y u_i = min(K_i − ⌈p_i⌉, max_bikes_per_move)
   (puesta máxima: nunca más que los anclajes proyectados libres).
   Variante `Asignador(retiro="sigma")`: ⌊p_i − σ_i⌋ y K_i − ⌈p_i + σ_i⌉, con
   σ_i = √(salidas + llegadas esperadas en [t, t+L)) (margen fijo, k = 1, no
   tuneado); es una decisión de política como λ, la congela `integracion2`.
   Las órdenes son x_i = y_i − p̂_i ≠ 0, con effective_at = t + L.

Ventana del día: [05:30, 00:30 del día siguiente); el día de una hora de
decisión es `config.window_day(t)`. No se emiten órdenes con t + L ≥ 00:30.

`uv run python -m ecosim.asignador validate` corre la validación del sustituto
(Spearman contra un simulador de flujo propio) y `... day-timing` corre días
completos en lazo cerrado con ese simulador; ver
`ecosim/results/asignador/REPORT.md`.
"""

from __future__ import annotations

import sys
import time as _time
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta

import highspy
import numpy as np
import pandas as pd
from scipy import sparse

from ecosim import config as C
from ecosim.contracts import Order, PolicyParams, SimState

WINDOW_MIN = C.WINDOW_MIN          # 1140 minutos en [05:30, 00:30)
EMPTY_TH = 0.5                     # stock fluido < 0.5 cuenta como vacía
FULL_TH = 0.5                      # stock fluido > K − 0.5 cuenta como llena


# ======================================================================
# Pronóstico → tasas por minuto
# ======================================================================

def _ts(x) -> pd.Timestamp:
    return pd.Timestamp(x)


def _day0(t) -> pd.Timestamp:
    """05:30 de la ventana a la que pertenece t (también después de medianoche)."""
    return pd.Timestamp(C.day_bounds(C.window_day(t))[0])


def _minute(t, day0) -> int:
    return int(round((_ts(t) - day0) / pd.Timedelta(minutes=1)))


def select_issue(forecast: pd.DataFrame, t, block_min: int, variant=None, refresh_min=None) -> pd.DataFrame:
    """Renglones de la emisión que se usa en la decisión t: bloques de
    `block_min`, la variante y la serie (`refresh_min`) pedidas, y el
    `issued_at` más reciente ≤ t. Nunca una emisión futura.

    `refresh_min=None` no filtra por serie (solo si la columna no existe o
    trae una sola serie). Lanza `ValueError` si no queda nada: nunca tasas
    cero en silencio."""
    f = forecast[forecast["block_min"] == block_min]
    if variant is not None:
        f = f[f["variant"] == variant]
    if len(f) == 0:
        raise ValueError(f"forecast sin bloques de {block_min} min"
                         + (f" de la variante {variant!r}" if variant is not None else ""))
    if "refresh_min" in f.columns:
        if refresh_min is not None:
            avail = sorted(f["refresh_min"].unique().tolist())
            f = f[f["refresh_min"] == refresh_min]
            if len(f) == 0:
                raise ValueError(f"forecast sin emisiones con refresh_min={refresh_min} "
                                 f"(hay {avail}); pasa params.refresh_min")
        elif f["refresh_min"].nunique() > 1:
            raise ValueError("forecast trae varias series (refresh_min); pasa params.refresh_min")
    f = f[f["issued_at"] <= _ts(t)]
    if len(f) == 0:
        raise ValueError(f"forecast sin emisiones con issued_at ≤ {t}"
                         + (f" (variante {variant!r})" if variant is not None else ""))
    f = f[f["issued_at"] == f["issued_at"].max()]
    if f["variant"].nunique() > 1:
        raise ValueError("forecast trae varias variantes; pasa params.extra['variant']")
    return f


def forecast_rates(forecast: pd.DataFrame, names, t, block_min: int, variant=None,
                   refresh_min=None, need=None):
    """Tasas por minuto (salidas, llegadas) de forma (n, WINDOW_MIN), uniformes
    dentro de cada bloque, de la emisión de `select_issue`. Estaciones sin
    pronóstico → 0. `need = (m0, m1)`: minutos de la ventana que la emisión
    debe cubrir (con algún bloque); si no, `ValueError`."""
    n = len(names)
    dep = np.zeros((n, WINDOW_MIN))
    arr = np.zeros((n, WINDOW_MIN))
    f = select_issue(forecast, t, block_min, variant, refresh_min)
    day0 = _day0(t)
    start = ((f["block_start"] - day0) / pd.Timedelta(minutes=1)).round().astype(int).to_numpy()
    if need is not None and need[1] > need[0]:
        covered = np.zeros(WINDOW_MIN, bool)
        for s0 in np.unique(start):
            covered[max(s0, 0):max(min(s0 + block_min, WINDOW_MIN), 0)] = True
        miss = np.flatnonzero(~covered[need[0]:need[1]])
        if len(miss):
            first = day0 + pd.Timedelta(minutes=int(need[0] + miss[0]))
            raise ValueError(f"la emisión de {f['issued_at'].iloc[0]} no cubre {first} "
                             f"(la decisión de {t} necesita hasta "
                             f"{day0 + pd.Timedelta(minutes=int(need[1]))})")
    pos = pd.Series(np.arange(n), index=pd.Index(names, dtype=object))
    idx = pos.reindex(f["short_name"].astype(str).to_numpy()).to_numpy()
    ok = ~np.isnan(idx)
    idx, start = idx[ok].astype(int), start[ok]
    d = f["departures"].to_numpy(float)[ok] / block_min
    a = f["arrivals"].to_numpy(float)[ok] / block_min
    for k in range(block_min):
        m = start + k
        good = (m >= 0) & (m < WINDOW_MIN)
        np.add.at(dep, (idx[good], m[good]), d[good])
        np.add.at(arr, (idx[good], m[good]), a[good])
    return dep, arr


# ======================================================================
# Flujo fluido recortado
# ======================================================================

def fluid_project(b0, K, net, m0, m1, adds=None):
    """Stock fluido al inicio del minuto m1 partiendo de b0 al inicio de m0.
    `adds` = {minuto: vector de deltas} (órdenes pendientes), aplicados al
    inicio de ese minuto y recortados a [0, K]."""
    s = np.asarray(b0, float).copy()
    adds = adds or {}
    for m in range(m0, m1):
        if m in adds:
            s = np.clip(s + adds[m], 0, K)
        s = np.clip(s + net[:, m], 0, K)
    return s


def station_costs(K, net, m0, m1):
    """c[i, y] = minutos vacíos + llenos en [m0, m1) arrancando con y bicis en
    m0 (fluido recortado). Matriz (n, Kmax+1); y > K_i queda en +inf."""
    K = np.asarray(K, int)
    kmax = int(K.max()) if len(K) else 0
    Y = np.tile(np.arange(kmax + 1, dtype=float), (len(K), 1))
    valid = Y <= K[:, None]
    Kf = K[:, None].astype(float)
    s = np.minimum(Y, Kf)
    cost = np.zeros_like(Y)
    for m in range(m0, m1):
        s = np.clip(s + net[:, m][:, None], 0, Kf)
        cost += (s < EMPTY_TH) + (s > Kf - FULL_TH)
    cost[K == 0] = 0.0           # estación sin lugar: no hay nada que decidir
    cost[~valid] = np.inf
    return cost


def lower_hull(c):
    """Envolvente convexa inferior de los puntos (y, c[y]). Devuelve la lista
    de índices y de sus vértices (monotone chain)."""
    pts = [y for y in range(len(c)) if np.isfinite(c[y])]
    hull = []
    for y in pts:
        while len(hull) >= 2:
            y1, y2 = hull[-2], hull[-1]
            # quitar y2 si queda por encima de la recta y1→y
            if (c[y2] - c[y1]) * (y - y1) >= (c[y] - c[y1]) * (y2 - y1):
                hull.pop()
            else:
                break
        hull.append(y)
    return hull


def hull_value(c, hull, y):
    """ĉ(y) evaluada en la envolvente (interpolación lineal entre vértices)."""
    return float(np.interp(y, hull, [c[h] for h in hull]))


# ======================================================================
# Asignador
# ======================================================================

@dataclass
class Plan:
    names: np.ndarray
    K: np.ndarray
    p: np.ndarray          # proyección fluida en t+L
    phat: np.ndarray       # round(p)
    cost: np.ndarray       # c[i, y]
    hulls: list            # envolvente de c_i sobre [p̂_i − r_i, p̂_i + u_i]
    r: np.ndarray          # retiro máximo por estación (bicis)
    u: np.ndarray          # puesta máxima por estación (bicis)
    y: np.ndarray          # objetivo entero en t+L
    moves_avail: int
    bodega_bounds: tuple
    status: str
    objective: float
    solve_s: float
    total_s: float
    issued_at: object = None   # emisión de pronóstico usada

    @property
    def x(self):
        return (self.y - self.phat).astype(int)

    def surrogate(self, y=None, hull=True) -> float:
        """E_sub + F_sub del plan (por defecto el elegido). Con `hull=True`, y
        debe caer en [p̂ − r, p̂ + u] (dominio de la envolvente)."""
        y = self.y if y is None else y
        if hull:
            return float(sum(hull_value(self.cost[i], self.hulls[i], y[i]) for i in range(len(y))))
        return float(self.cost[np.arange(len(y)), y].sum())


class Asignador:
    """Política MILP de 1.6. `decide` cumple `contracts.Policy`."""

    def __init__(self, time_limit: float = 4.0, threads: int = 4, mip_rel_gap: float = 1e-4,
                 retiro: str = "floor"):
        # retiro: cota de cada retiro (y, simétrica, de cada puesta).
        #   "floor" (default, run 2) = ⌊p⌋: las bicis proyectadas en t+L;
        #   "sigma" = ⌊p − σ⌋: menos una desviación Poisson del flujo esperado
        #     en [t, t+L) (variante de política; la elige `integracion2` en
        #     días de selección, igual para todos los brazos);
        #   "round" = p̂ = round(p), la del run 1 (solo para comparar).
        if retiro not in ("floor", "round", "sigma"):
            raise ValueError(f"retiro: 'floor', 'round' o 'sigma', llegó {retiro!r}")
        self.retiro = retiro
        self.time_limit = time_limit
        self.threads = threads
        self.mip_rel_gap = mip_rel_gap
        self._history: list[Order] = []     # órdenes emitidas por esta instancia (mismo día)
        self._hist_day = None
        self._last_t = None
        self.last_plan: Plan | None = None
        # decisiones en que HiGHS no dio una solución factible y se cayó a
        # "no mover nada": (t, estado de HiGHS). `integracion` las reporta.
        self.fallbacks: list[tuple] = []

    # ---------------------------------------------------------------- API
    def decide(self, t, state: SimState, pending_orders, forecast, params: PolicyParams) -> list[Order]:
        t = _ts(t).to_pydatetime()
        # historia propia (tope por hora): se reinicia con cada día (ventana)
        # nuevo o si el tiempo no avanza (otra corrida del mismo día con esta
        # instancia). El día es `window_day`: 00:15 sigue siendo el día d.
        wd = C.window_day(t)
        if self._hist_day != wd or (self._last_t is not None and t <= self._last_t):
            self._history, self._hist_day = [], wd
        self._last_t = t
        plan = self.plan(t, state, pending_orders, forecast, params)
        self.last_plan = plan
        if plan is None:
            return []
        if plan.status.startswith("fallback"):
            self.fallbacks.append((t, plan.status))
        eff = t + timedelta(minutes=int(params.lead_min))
        orders = [Order(t, eff, str(nm), int(dx)) for nm, dx in zip(plan.names, plan.x) if dx != 0]
        self._history.extend(orders)
        return orders

    # ------------------------------------------------------------ helpers
    def _known_orders(self, pending_orders, eff_lo, eff_hi):
        """Órdenes (pendientes ∪ historia propia) con effective_at en [eff_lo, eff_hi)."""
        seen = {}
        for o in list(pending_orders or []) + self._history:
            e = _ts(o.effective_at)
            if eff_lo <= e < eff_hi:
                seen[(e, str(o.short_name))] = o
        return list(seen.values())

    def moves_available(self, t, pending_orders, params) -> int:
        """tope_hora − máx de órdenes ya emitidas en las ventanas de 60 min
        (inicio cada 15 min, desde 05:30) que contienen t+L."""
        eff = _ts(t) + pd.Timedelta(minutes=int(params.lead_min))
        day0 = _day0(t)
        known = self._known_orders(pending_orders, eff - pd.Timedelta(minutes=60), eff + pd.Timedelta(minutes=60))
        worst = 0
        for back in range(0, 60, C.STEP_MIN):
            s = eff - pd.Timedelta(minutes=back)
            if s < day0:
                continue
            e = s + pd.Timedelta(minutes=60)
            worst = max(worst, sum(1 for o in known if s <= _ts(o.effective_at) < e))
        return max(0, int(params.tope_hora) - worst)

    # --------------------------------------------------------------- plan
    def plan(self, t, state: SimState, pending_orders, forecast, params: PolicyParams,
             lam=None, tope_hora=None, tope_bodega=None) -> Plan | None:
        t0 = _time.perf_counter()
        t = _ts(t)
        L = int(params.lead_min)
        eff = t + pd.Timedelta(minutes=L)
        _, day_end = C.day_bounds(C.window_day(t))
        if eff >= pd.Timestamp(day_end):
            return None             # nada efectivo en o después de 00:30
        if forecast is None:
            raise ValueError(f"asignador: decisión de {t} sin pronóstico (forecast=None)")
        lam = float(params.lam if lam is None else lam)
        mu = float(params.mu)
        if tope_hora is not None or tope_bodega is not None:
            params = PolicyParams(**{**params.__dict__,
                                     "tope_hora": params.tope_hora if tope_hora is None else tope_hora,
                                     "tope_bodega": params.tope_bodega if tope_bodega is None else tope_bodega})

        st = state.stations
        names = st["short_name"].astype(str).to_numpy()
        # lugar útil para disponibles en t: las dañadas de t (que cambian en
        # el día) y los anclajes deshabilitados no cuentan
        K = (st["bikes"] + st["docks"]).to_numpy(int)
        b = st["bikes"].to_numpy(int)
        day0 = _day0(t)
        m_t, m_eff = _minute(t, day0), _minute(eff, day0)
        m_end = min(m_eff + int(params.H_horas) * 60, WINDOW_MIN)
        variant = params.extra.get("variant")
        dep, arr = forecast_rates(forecast, names, t, int(params.block_min), variant,
                                  params.refresh_min, need=(m_t, m_end))
        issued = select_issue(forecast, t, int(params.block_min), variant, params.refresh_min)["issued_at"].iloc[0]
        net = arr - dep

        # proyección t → t+L con pendientes
        pos = {nm: i for i, nm in enumerate(names)}
        adds, pend_net = {}, 0
        for o in pending_orders or []:
            pend_net += int(o.delta)
            i = pos.get(str(o.short_name))
            if i is None:
                continue
            m = max(_minute(o.effective_at, day0), m_t)
            if m > m_eff:
                continue           # efectiva después de t+L: no cambia la proyección
            adds.setdefault(m, np.zeros(len(names)))[i] += o.delta
        p = fluid_project(b, K, net, m_t, m_eff, {m: v for m, v in adds.items() if m < m_eff})
        if m_eff in adds:
            p = np.clip(p + adds[m_eff], 0, K)
        phat = np.clip(np.round(p), 0, K).astype(int)

        # Cotas por orden. Retiro ≤ ⌊p⌋: nunca pedir más bicis de las que se
        # proyectan en t+L (en el run 1 los retiros imposibles se recortaban al
        # aplicarse y la bodega aplicada rompía el tope). Simétrico para poner:
        # ≤ K − ⌈p⌉. Con "sigma" se resta/suma σ (menos recortes, ver
        # REPORT.md). Ambos ≤ max_bikes_per_move.
        cap = int(params.max_bikes_per_move)
        if self.retiro in ("floor", "sigma"):
            # sigma: margen de una desviación Poisson del flujo esperado en [t, t+L)
            sd = np.sqrt((dep[:, m_t:m_eff] + arr[:, m_t:m_eff]).sum(axis=1)) if self.retiro == "sigma" else 0.0
            r = np.minimum(np.floor(p - sd + 1e-9), cap).astype(int)
            u = np.minimum(K - np.ceil(p + sd - 1e-9), cap).astype(int)
        else:                                          # cotas del run 1 (+ tope por orden)
            r = np.minimum(phat, cap).astype(int)
            u = np.minimum(K - phat, cap).astype(int)
        r, u = np.clip(r, 0, phat), np.clip(u, 0, K - phat)
        if "out_of_service" in st.columns:           # columna extra del simulador
            oos = st["out_of_service"].fillna(False).to_numpy(bool)
            r[oos], u[oos] = 0, 0

        cost = station_costs(K, net, m_eff, m_end)
        hulls = [lower_hull(np.where((np.arange(cost.shape[1]) >= phat[i] - r[i])
                                     & (np.arange(cost.shape[1]) <= phat[i] + u[i]), cost[i], np.inf))
                 for i in range(len(names))]

        M = self.moves_available(t, pending_orders, params)
        T = int(params.tope_bodega)
        base = int(state.warehouse) + pend_net
        lo, hi = min(-T - base, 0), max(T - base, 0)

        y = phat.copy()
        status, obj, solve_s = "noop", float("nan"), 0.0
        if M > 0 and (r + u).any():
            y, status, obj, solve_s = self._solve(phat, r, u, cost, hulls, lam, mu, M, lo, hi)
        return Plan(names, K, p, phat, cost, hulls, r, u, y, M, (lo, hi), status, obj, solve_s,
                    _time.perf_counter() - t0, pd.Timestamp(issued))

    # -------------------------------------------------------------- MILP
    def _solve(self, phat, r, u, cost, hulls, lam, mu, M, lo, hi):
        n = len(phat)
        # columnas: y[0:n], m[n:2n], z[2n:3n], w[3n];  y ∈ [p̂ − r, p̂ + u]
        iy, im, iz, iw = 0, n, 2 * n, 3 * n
        ncol = 3 * n + 1
        col_cost = np.r_[np.zeros(n), np.full(n, lam), np.ones(n), mu]
        col_lo = np.r_[(phat - r).astype(float), np.zeros(2 * n + 1)]
        col_hi = np.r_[(phat + u).astype(float), np.ones(n), np.full(n, np.inf), np.inf]
        integ = np.r_[np.ones(2 * n, int), np.zeros(n + 1, int)]

        rows, cols, vals, rlo, rhi = [], [], [], [], []
        nr = 0

        def add(cs, vs, a, bnd):
            nonlocal nr
            rows.extend([nr] * len(cs)); cols.extend(cs); vals.extend(vs)
            rlo.append(a); rhi.append(bnd); nr += 1

        inf = np.inf
        for i in range(n):
            if r[i] == 0 and u[i] == 0:
                col_hi[im + i] = 0.0        # y = p̂ fijo: nada que decidir
                continue
            # y − p̂ ≤ u m ;  p̂ − y ≤ r m
            add([iy + i, im + i], [1.0, -float(u[i])], -inf, float(phat[i]))
            add([iy + i, im + i], [-1.0, -float(r[i])], -inf, -float(phat[i]))
            h = hulls[i]
            if len(h) == 1:
                add([iz + i], [1.0], float(cost[i, h[0]]), inf)
            for a, bq in zip(h[:-1], h[1:]):
                slope = (cost[i, bq] - cost[i, a]) / (bq - a)
                # z ≥ c(a) + slope (y − a)  →  z − slope y ≥ c(a) − slope a
                add([iz + i, iy + i], [1.0, -slope], float(cost[i, a] - slope * a), inf)
        sp = float(phat.sum())
        add(list(range(im, im + n)), [1.0] * n, -inf, float(M))
        add(list(range(iy, iy + n)), [1.0] * n, sp + lo, sp + hi)
        add([iw] + list(range(iy, iy + n)), [1.0] + [-1.0] * n, -sp, inf)
        add([iw] + list(range(iy, iy + n)), [1.0] + [1.0] * n, sp, inf)

        A = sparse.csc_matrix((vals, (rows, cols)), shape=(nr, ncol))
        lp = highspy.HighsLp()
        lp.num_col_, lp.num_row_ = ncol, nr
        lp.col_cost_ = col_cost
        lp.col_lower_, lp.col_upper_ = col_lo, col_hi
        lp.row_lower_, lp.row_upper_ = np.array(rlo), np.array(rhi)
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.start_ = A.indptr
        lp.a_matrix_.index_ = A.indices
        lp.a_matrix_.value_ = A.data
        lp.integrality_ = [highspy.HighsVarType.kInteger if v else highspy.HighsVarType.kContinuous for v in integ]

        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("threads", self.threads)
        h.setOptionValue("time_limit", self.time_limit)
        h.setOptionValue("mip_rel_gap", self.mip_rel_gap)
        h.passModel(lp)
        t0 = _time.perf_counter()
        h.run()
        solve_s = _time.perf_counter() - t0
        status = h.modelStatusToString(h.getModelStatus())
        sol = np.asarray(h.getSolution().col_value)
        # Solo se acepta una solución primal factible (kSolutionStatusFeasible = 2).
        # Si HiGHS llega al límite de tiempo sin incumbente, devuelve ceros que
        # violarían los topes: en ese caso no se mueve nada y se avisa.
        feasible = int(h.getInfo().primal_solution_status) == 2
        if not feasible or len(sol) != ncol or not np.all(np.isfinite(sol[:n])):
            warnings.warn(f"asignador: HiGHS sin solución factible ({status}); no se mueve nada")
            return phat.copy(), f"fallback: {status}", float("nan"), solve_s
        y = np.clip(np.round(sol[:n]), phat - r, phat + u).astype(int)
        return y, status, float(h.getInfo().objective_function_value), solve_s


# ======================================================================
# Herramientas de prueba y validación (no dependen de otros workers)
# ======================================================================

# Días de selección para validar (run 2): el primero de la lista (lunes), el
# primer domingo y un jueves de otro mes. Fijos de antemano, como en el run 1.
VALIDATION_DAYS = ("2025-09-01", "2025-09-21", "2025-10-30")
VALIDATION_TIMES = ("05:30", "07:30", "09:30", "12:30", "15:30", "18:30", "21:30", "23:15")


def oracle_forecast(day, block_min: int = 60, trips: pd.DataFrame | None = None,
                    issued: list | None = None, refresh_min: int = 0) -> pd.DataFrame:
    """Pronóstico oracle en formato Forecast (run 2), armado directo de
    `data.trips(day)`: salidas por (o, bloque de t_dep) y llegadas por
    (d, bloque de t_arr), solo estaciones conocidas y dentro de [05:30, 00:30).
    `issued` = horas de emisión (default: solo 05:30); todas traen el mismo
    contenido de día completo."""
    from ecosim import data
    tr = data.trips(day) if trips is None else trips
    start, end = C.day_bounds(day)
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    names = sorted(data.stations(day)["short_name"].astype(str))
    blocks = pd.date_range(start, end, freq=f"{block_min}min", inclusive="left")
    idx = pd.MultiIndex.from_product([names, blocks], names=["short_name", "block_start"])

    def count(col, t):
        m = tr[f"{col}_known"] & (tr[t] >= start) & (tr[t] < end)
        s = tr.loc[m]
        b = start + ((s[t] - start) // pd.Timedelta(minutes=block_min)) * pd.Timedelta(minutes=block_min)
        return s.groupby([s[col].astype(str), b]).size().reindex(idx, fill_value=0)

    base = pd.DataFrame({"departures": count("o", "t_dep").astype(float),
                         "arrivals": count("d", "t_arr").astype(float)}).reset_index()
    out = []
    for s in (issued or [start]):
        f = base.copy()
        f.insert(0, "issued_at", pd.Timestamp(s))
        f.insert(1, "variant", "oracle")
        f.insert(4, "block_min", block_min)
        out.append(f)
    f = pd.concat(out, ignore_index=True)
    for c in ("dep_lo", "dep_hi", "arr_lo", "arr_hi"):
        f[c] = np.nan
    f["refresh_min"] = int(refresh_min)
    f["horizon_h"] = WINDOW_MIN // 60
    return f


def minute_counts(day, names, trips: pd.DataFrame | None = None):
    """Salidas y llegadas reales por (estación, minuto) en [05:30, 00:30)."""
    from ecosim import data
    tr = data.trips(day) if trips is None else trips
    start, _ = C.day_bounds(day)
    start = pd.Timestamp(start)
    pos = pd.Series(np.arange(len(names)), index=pd.Index(names, dtype=object))
    out = []
    for col, t in (("o", "t_dep"), ("d", "t_arr")):
        mat = np.zeros((len(names), WINDOW_MIN), int)
        m = ((tr[t] - start) // pd.Timedelta(minutes=1)).to_numpy()
        i = pos.reindex(tr[col].astype(str).to_numpy()).to_numpy()
        ok = (m >= 0) & (m < WINDOW_MIN) & ~np.isnan(i)
        np.add.at(mat, (i[ok].astype(int), m[ok].astype(int)), 1)
        out.append(mat)
    return out[0], out[1]


def flow_sim(b0, K, dep, arr, m0, m1, orders=None, count_from=None, clipped=None):
    """Simulador de flujo simple, por estación y minuto, con conteos reales:
    s ← clip(s + llegadas − salidas, 0, K) (salida en vacía y llegada en llena
    se pierden; sin desvíos). `orders` = {minuto: deltas}, aplicados al inicio
    del minuto y recortados; si `clipped` es un dict, suma ahí las bicis de
    retiros ("R") y puestas ("A") que no se pudieron aplicar. Devuelve (stock
    en m1, E, F) contando desde `count_from` (default m0): minutos con s == 0
    y con s == K (K > 0)."""
    s = np.asarray(b0, int).copy()
    K = np.asarray(K, int)
    orders = orders or {}
    cf = m0 if count_from is None else count_from
    E = F = 0
    live = K > 0
    for m in range(m0, m1):
        if m in orders:
            want = s + orders[m]
            got = np.clip(want, 0, K)
            if clipped is not None:
                clipped["R"] = clipped.get("R", 0) + int((got - want)[want < 0].sum())
                clipped["A"] = clipped.get("A", 0) + int((want - got)[want > K].sum())
            s = got
        s = np.clip(s + arr[:, m] - dep[:, m], 0, K)
        if m >= cf:
            E += int(((s == 0) & live).sum())
            F += int(((s == K) & live).sum())
    return s, E, F


def _state_at(b, K, names, t, warehouse=0, oos=None) -> SimState:
    st = pd.DataFrame({"short_name": names, "bikes": b.astype("int64"),
                       "disabled": np.zeros(len(b), "int64"),
                       "docks": (K - b).astype("int64"), "cap": K.astype("int64")})
    if oos is not None:
        st["out_of_service"] = np.asarray(oos, bool)
    return SimState(t=pd.Timestamp(t).to_pydatetime(), stations=st, warehouse=warehouse)


def _day_inputs(day):
    from ecosim import data
    ini = data.initial_state(day)
    names = ini["short_name"].astype(str).to_numpy()
    K0 = (ini["bikes"] + ini["docks"]).to_numpy(int)
    b0 = ini["bikes"].to_numpy(int)
    tr = data.trips(day)
    dep, arr = minute_counts(day, names, tr)
    return names, K0, b0, ini["out_of_service"].to_numpy(bool), tr, dep, arr


def validate(days=VALIDATION_DAYS, H_list=(1, 3, 6), times=VALIDATION_TIMES, seed=20260929,
             out_dir=None, lam_grid=(0, 5, 15, 30, 60, 120, 240)):
    """Spearman entre el costo sustituto (E_sub + F_sub) y E + F del simulador
    de flujo simple sobre un conjunto de planes, con pronóstico oracle. Todos
    los planes respetan las cotas del MILP (y ∈ [p̂ − r, p̂ + u])."""
    import json
    from scipy.stats import spearmanr

    out_dir = out_dir or (C.REPO_ROOT / "ecosim" / "results" / "asignador")
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    rows, timing = [], []
    for day in days:
        names, K0, b0, oos, tr, dep, arr = _day_inputs(day)
        fc = oracle_forecast(day, 60, tr)
        day0 = pd.Timestamp(C.day_bounds(day)[0])
        for H in H_list:
            params = PolicyParams(lead_min=60, H_horas=H, lam=0, mu=1.0, tope_hora=60, tope_bodega=200,
                                  block_min=60, max_bikes_per_move=22, refresh_min=0)
            for hhmm in times:
                t = pd.Timestamp(f"{day} {hhmm}")        # todas antes de medianoche
                m_t = _minute(t, day0)
                b_t, _, _ = flow_sim(b0, K0, dep, arr, 0, m_t)
                state = _state_at(b_t, K0, names, t, oos=oos)
                A = Asignador()
                plans = {}
                base = None
                for lam in lam_grid:
                    t1 = _time.perf_counter()
                    pl = A.plan(t, state, [], fc, params, lam=lam)
                    timing.append({"day": day, "H": H, "t": hhmm, "lam": lam,
                                   "total_s": _time.perf_counter() - t1, "solve_s": pl.solve_s,
                                   "status": pl.status, "moves": int((pl.x != 0).sum())})
                    base = pl
                    plans[f"milp_lam{lam}"] = pl.y.copy()
                pl = A.plan(t, state, [], fc, params, lam=0, tope_hora=10_000, tope_bodega=100_000)
                plans["milp_sin_tope"] = pl.y.copy()
                phat, lo_y, hi_y = base.phat, base.phat - base.r, base.phat + base.u
                plans["noop"] = phat.copy()
                opt = plans["milp_lam0"]
                moved = np.flatnonzero(opt != phat)
                for k in range(4):                      # subconjuntos del óptimo
                    y = phat.copy()
                    sel = moved[rng.random(len(moved)) < 0.5]
                    y[sel] = opt[sel]
                    plans[f"mitad_opt_{k}"] = y
                y = phat.copy()                          # al revés del óptimo
                y[moved] = np.clip(2 * phat[moved] - opt[moved], lo_y[moved], hi_y[moved])
                plans["reves_opt"] = y
                movable = np.flatnonzero(hi_y > lo_y)
                for k in (10, 30, 100):                  # aleatorios dentro de las cotas
                    for j in range(3):
                        y = phat.copy()
                        sel = rng.choice(movable, size=min(k, len(movable)), replace=False)
                        y[sel] = rng.integers(lo_y[sel], hi_y[sel] + 1)
                        plans[f"azar{k}_{j}"] = y
                m_eff = m_t + 60
                m_end = min(m_eff + H * 60, WINDOW_MIN)
                _, E0, F0 = flow_sim(b_t, K0, dep, arr, m_t, m_end, count_from=m_eff)
                for name, y in plans.items():
                    x = (y - phat).astype(int)
                    _, E, F = flow_sim(b_t, K0, dep, arr, m_t, m_end, orders={m_eff: x}, count_from=m_eff)
                    rows.append({"day": day, "H": H, "t": hhmm, "plan": name,
                                 "moves": int((x != 0).sum()), "bodega": int(x.sum()),
                                 "max_abs_delta": int(np.abs(x).max()),
                                 "sub_hull": base.surrogate(y, hull=True),
                                 "sub_exact": base.surrogate(y, hull=False),
                                 "sim_EF": E + F, "sim_E": E, "sim_F": F,
                                 "sim_EF_noop": E0 + F0})
                print(f"{day} H={H} {hhmm}: planes={len(plans)}", flush=True)
    df = pd.DataFrame(rows)
    tm = pd.DataFrame(timing)
    df.to_csv(out_dir / "validation_plans.csv", index=False)
    tm.to_csv(out_dir / "timing.csv", index=False)

    per = []
    for (day, H, t), g in df.groupby(["day", "H", "t"]):
        gm = g[g.plan.str.startswith("milp")]
        per.append({"day": day, "H": H, "t": t, "n_planes": len(g),
                    "rho_hull": spearmanr(g["sub_hull"], g["sim_EF"]).statistic,
                    "rho_exact": spearmanr(g["sub_exact"], g["sim_EF"]).statistic,
                    "rho_hull_milp": spearmanr(gm["sub_hull"], gm["sim_EF"]).statistic})
    per = pd.DataFrame(per)
    per.to_csv(out_dir / "validation_spearman.csv", index=False)
    milp = df[df.plan.str.startswith("milp_lam")]
    summary = {
        "days": list(days), "times": list(times), "H": list(H_list),
        "n_decisiones": len(per), "n_planes_por_decision": int(per["n_planes"].median()),
        "spearman_hull": {"mediana": float(per["rho_hull"].median()), "min": float(per["rho_hull"].min()),
                          "n_ge_0.8": int((per["rho_hull"] >= 0.8).sum()),
                          "mediana_por_H": per.groupby("H")["rho_hull"].median().round(3).to_dict(),
                          "min_por_H": per.groupby("H")["rho_hull"].min().round(3).to_dict(),
                          "mediana_por_t": per.groupby("t")["rho_hull"].median().round(3).to_dict()},
        "spearman_exact": {"mediana": float(per["rho_exact"].median()), "min": float(per["rho_exact"].min())},
        "spearman_hull_solo_milp": {"mediana": float(per["rho_hull_milp"].median()),
                                    "min": float(per["rho_hull_milp"].min())},
        # mejora contra no hacer nada, en sustituto y en el simulador
        "spearman_pooled_mejora": float(spearmanr(
            df["sub_hull"] - df.groupby(["day", "H", "t"])["sub_hull"].transform(
                lambda s: s[df.loc[s.index, "plan"] == "noop"].iloc[0]),
            df["sim_EF"] - df["sim_EF_noop"]).statistic),
        "max_abs_delta_milp": int(milp["max_abs_delta"].max()),
        "tiempo_decision_s": {f"H{h}": {"mediana": float(g["total_s"].median()), "p95": float(g["total_s"].quantile(0.95)),
                                        "max": float(g["total_s"].max())} for h, g in tm.groupby("H")},
        "status": tm["status"].value_counts().to_dict(),
    }
    (out_dir / "validation_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def run_day_flow(day, params: PolicyParams, inputs=None, retiro="floor"):
    """Un día completo en lazo cerrado con `flow_sim` y pronóstico oracle:
    decide cada 15 min (todas las decisiones de `config.decision_times`),
    aplica cada orden en su effective_at (recortada a [0, K]) y lleva la
    bodega aplicada. Sirve para medir tiempos por día y retiros recortados.

    `retiro` = cota de retiro del `Asignador` ("floor" default, "sigma",
    "round" = la del run 1), para comparar."""
    names, K0, b0, oos, tr, dep, arr = inputs or _day_inputs(day)
    fc = oracle_forecast(day, int(params.block_min), tr)
    day0 = pd.Timestamp(C.day_bounds(day)[0])
    A = Asignador(retiro=retiro)
    pos = {nm: i for i, nm in enumerate(names)}
    s = b0.copy()
    m_prev = 0
    pending: list[Order] = []
    W, req_R, req_A, n_moves, n_dec = 0, 0, 0, 0, 0
    clipped: dict = {}
    times, max_abs, W_path = [], 0, []
    E = F = 0
    for t in C.decision_times(day):
        t = pd.Timestamp(t)
        m = _minute(t, day0)
        # avanzar el flujo hasta t aplicando lo que se vuelve efectivo
        orders = {}
        for o in pending:
            orders.setdefault(_minute(o.effective_at, day0), np.zeros(len(names), int))[pos[o.short_name]] += o.delta
        cl = {}
        s, e, f = flow_sim(s, K0, dep, arr, m_prev, m, orders={k: v for k, v in orders.items() if m_prev <= k < m},
                           clipped=cl)
        E, F = E + e, F + f
        applied = [o for o in pending if _minute(o.effective_at, day0) < m]
        W += sum(o.delta for o in applied) + cl.get("R", 0) - cl.get("A", 0)
        for k in ("R", "A"):
            clipped[k] = clipped.get(k, 0) + cl.get(k, 0)
        pending = [o for o in pending if _minute(o.effective_at, day0) >= m]
        m_prev = m
        W_path.append(W)
        state = _state_at(s, K0, names, t, warehouse=W, oos=oos)
        t1 = _time.perf_counter()
        new = A.decide(t, state, list(pending), fc, params)
        dt = _time.perf_counter() - t1
        if A.last_plan is not None:
            n_dec += 1
            times.append(dt)
        for o in new:
            max_abs = max(max_abs, abs(o.delta))
            req_R += max(0, -o.delta)
            req_A += max(0, o.delta)
        n_moves += len(new)
        pending += new
    # lo que queda pendiente se aplica antes del fin (effective_at < 00:30)
    orders = {}
    for o in pending:
        orders.setdefault(_minute(o.effective_at, day0), np.zeros(len(names), int))[pos[o.short_name]] += o.delta
    cl = {}
    _, e, f = flow_sim(s, K0, dep, arr, m_prev, WINDOW_MIN, orders=orders, clipped=cl)
    E, F = E + e, F + f
    W += sum(o.delta for o in pending) + cl.get("R", 0) - cl.get("A", 0)
    for k in ("R", "A"):
        clipped[k] = clipped.get(k, 0) + cl.get(k, 0)
    W_path.append(W)
    return {"day": day, "H": params.H_horas, "lam": params.lam, "retiro": retiro,
            "decisiones": n_dec, "tiempo_dia_s": float(sum(times)),
            "tiempo_decision_mediana_s": float(np.median(times)), "tiempo_decision_max_s": float(max(times)),
            "ordenes": n_moves, "max_abs_delta": max_abs,
            "R_pedido": req_R, "A_pedido": req_A,
            "R_recortado": clipped.get("R", 0), "A_recortado": clipped.get("A", 0),
            "bodega_aplicada_fin": int(W), "bodega_aplicada_max_abs": int(max(abs(w) for w in W_path)),
            "E": E, "F": F, "EF": E + F,
            "fallbacks": len(A.fallbacks)}


def day_timing(days=VALIDATION_DAYS, H_list=(1, 3, 6), lam=60, out_dir=None):
    """Días completos en lazo cerrado (`run_day_flow`) con L = 60 y los
    placeholders; compara las cotas de retiro ⌊p⌋ (floor, default), ⌊p − σ⌋
    (sigma) y la del run 1 (round)."""
    import json
    out_dir = out_dir or (C.REPO_ROOT / "ecosim" / "results" / "asignador")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for day in days:
        inputs = _day_inputs(day)
        for H in H_list:
            params = PolicyParams(lead_min=60, H_horas=H, lam=lam, mu=1.0, tope_hora=60, tope_bodega=200,
                                  block_min=60, max_bikes_per_move=22, refresh_min=0)
            for retiro in ("sigma", "floor", "round"):
                r = run_day_flow(day, params, inputs, retiro=retiro)
                rows.append(r)
                print(json.dumps(r, ensure_ascii=False), flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "day_timing.csv", index=False)
    return df


def retiro_sim(days=None, arms=(("oracle", 3), ("ma", 3), ("model", 3)), retiros=("floor", "sigma"),
               lam=60, refresh_min=60, out_dir=None):
    """Cota de retiro ⌊p⌋ (floor) contra ⌊p − σ⌋ (sigma) en el simulador del
    run 2 (`ecosim.sim.run_day`, dañadas dinámicas), en días de SELECCIÓN.
    Topes reales de `ecosim/results/medicion/ecobici_stats.json`, L = 60 y
    pronósticos de `data/derived/ecosim/forecasts/{day}_{variant}_f{f}.parquet`.
    Escribe `retiro_sim.csv`."""
    import json
    from ecosim import sim
    from ecosim.days import load_days
    out_dir = out_dir or (C.REPO_ROOT / "ecosim" / "results" / "asignador")
    stats = json.loads((C.REPO_ROOT / "ecosim" / "results" / "medicion" / "ecobici_stats.json").read_text())
    days = days or load_days("seleccion")
    rows = []
    for day in days:
        for variant, H in arms:
            fc = pd.read_parquet(C.DERIVED / "forecasts" / f"{day}_{variant}_f{refresh_min}.parquet")
            params = PolicyParams(lead_min=60, H_horas=H, lam=lam, mu=1.0, tope_hora=int(stats["tope_hora"]),
                                  tope_bodega=int(stats["tope_bodega"]), block_min=60,
                                  max_bikes_per_move=C.MAX_BIKES_PER_MOVE, refresh_min=refresh_min,
                                  extra={"variant": variant})
            for retiro in retiros:
                pol = Asignador(retiro=retiro)
                t1 = _time.perf_counter()
                res = sim.run_day(day, policy=pol, lead_min=60, forecast=fc, params=params,
                                  arm=f"{variant}_H{H}_{retiro}")
                m, ex = res.metrics, res.extra
                ap = ex["orders_applied"]
                ret, put = ap[ap["delta"] < 0], ap[ap["delta"] > 0]
                r = {"day": day, "variant": variant, "H": H, "retiro": retiro,
                     "EF": m["E"] + m["F"], "E": m["E"], "F": m["F"],
                     "EF_sin_oos": m["E"] + m["F"] - ex["E_out_of_service"] - ex["F_out_of_service"],
                     "ordenes": len(ap), "moves": m["moves"], "A": m["A"], "R": m["R"],
                     "R_pedido": int(-ret["delta"].sum()),
                     "retiro_recorte_fisico": int(ret["recorte_fisico"].sum()),
                     "puesta_recorte_fisico": int(put["recorte_fisico"].sum()),
                     "recorte_bodega": m["recorte_bodega"], "recorte_por_movimiento": m["recorte_por_movimiento"],
                     "recorte_fuera_de_servicio": ex["recorte_fuera_de_servicio"],
                     "bodega_min": ex["bodega_min"], "bodega_max": ex["bodega_max"],
                     "fallbacks": len(pol.fallbacks), "segundos": _time.perf_counter() - t1}
                rows.append(r)
                print(json.dumps(r, ensure_ascii=False), flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "retiro_sim.csv", index=False)
    return df


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "validate":
        validate()
    elif cmd == "day-timing":
        day_timing()
    elif cmd == "retiro-sim":
        retiro_sim()
    else:
        print("uso: python -m ecosim.asignador validate | day-timing | retiro-sim")
