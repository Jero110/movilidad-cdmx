"""Tests a mano del asignador (plan 2026-09-28-ecosim, 1.6; run 2: plan
2026-09-28-ecosim2, subtask asignador2).

Todo es sintético: pocas estaciones, pronóstico armado a mano en formato
Forecast (run 2), sin datos reales ni código de otros workers.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ecosim import config as C
from ecosim.asignador import Asignador, forecast_rates, lower_hull, station_costs, flow_sim
from ecosim.contracts import Order, PolicyParams, SimState, validate_forecast

DAY = "2025-09-03"
T = datetime(2025, 9, 3, 7, 0)


def make_forecast(rates: dict, block_min=60, issued_at=None, refresh_min=60, variant="oracle") -> pd.DataFrame:
    """rates = {short_name: (salidas/h, llegadas/h)} constantes todo el día, o
    una función (short_name, block_start) → (salidas/h, llegadas/h) si
    `rates` trae llaves y valores callables. Una emisión en `issued_at`
    (default 05:30) que cubre todo el día [05:30, 00:30)."""
    start = pd.Timestamp(f"{DAY} 05:30")
    end = pd.Timestamp(C.day_bounds(DAY)[1])
    issued_at = start if issued_at is None else pd.Timestamp(issued_at)
    blocks = pd.date_range(start, end, freq=f"{block_min}min", inclusive="left")
    rows = []
    for nm, rate in rates.items():
        for b in blocks:
            d, a = rate(b) if callable(rate) else rate
            rows.append((issued_at, variant, nm, b, block_min, d * block_min / 60, a * block_min / 60))
    f = pd.DataFrame(rows, columns=["issued_at", "variant", "short_name", "block_start", "block_min",
                                    "departures", "arrivals"])
    for c in ("dep_lo", "dep_hi", "arr_lo", "arr_hi"):
        f[c] = np.nan
    f["refresh_min"] = refresh_min
    f["horizon_h"] = 6
    return validate_forecast(f)


def make_state(stations: dict, t=T, warehouse=0, disabled=None, oos=None) -> SimState:
    """stations = {short_name: (bikes, cap)}; `disabled` = {short_name: dañadas}
    (ocupan anclaje); `oos` = estaciones fuera de servicio (columna extra
    `out_of_service`, como la manda el simulador)."""
    disabled = disabled or {}
    rows = [(nm, b, disabled.get(nm, 0), cap - b - disabled.get(nm, 0), cap) for nm, (b, cap) in stations.items()]
    st = pd.DataFrame(rows, columns=["short_name", "bikes", "disabled", "docks", "cap"])
    if oos is not None:
        st["out_of_service"] = st["short_name"].isin(oos)
    return SimState(t=t, stations=st, warehouse=warehouse)


def params(**kw):
    base = dict(lead_min=60, H_horas=2, lam=0.5, mu=1.0, tope_hora=60, tope_bodega=200, block_min=60)
    base.update(kw)
    return PolicyParams(**base)


def by_station(orders):
    return {o.short_name: o.delta for o in orders}


# ---------------------------------------------------------------- piezas

def test_forecast_rates_uniform_within_block():
    f = make_forecast({"001": (6, 3)})
    dep, arr = forecast_rates(f, np.array(["001", "999"]), T, 60)
    assert dep.shape == (2, C.WINDOW_MIN) == (2, 1140)
    assert np.allclose(dep[0], 0.1) and np.allclose(arr[0], 0.05)
    assert np.allclose(dep[1], 0) and np.allclose(arr[1], 0)


def test_station_costs_and_hull():
    # 3 salidas/h durante 60 min, capacidad 5: con y bicis se vacía en 20·y min
    net = np.full((1, 420), -3 / 60)
    c = station_costs(np.array([5]), net, 0, 60)[0]
    # y = 0 → vacía los 60 min; más bicis → menos minutos vacía
    assert c[0] == 60
    assert c[0] > c[1] > c[2] > c[3]
    h = lower_hull(c)
    assert h[0] == 0 and h[-1] == 5
    # la envolvente nunca queda por encima de la curva
    assert all(np.interp(y, h, c[h]) <= c[y] + 1e-9 for y in range(6))


def test_flow_sim_counts_minutes():
    dep = np.zeros((1, 420), int)
    arr = np.zeros((1, 420), int)
    dep[0, 10] = 1
    _, E, F = flow_sim(np.array([1]), np.array([3]), dep, arr, 0, 30)
    assert E == 20 and F == 0            # vacía de los minutos 10 a 29


# ---------------------------------------------------------------- (a)

def test_a_transfers_between_stations_before_using_bodega():
    # A se vacía (6 salidas/h, ~12 bicis en H = 2 h); B tiene 14 de sobra y no
    # tiene demanda; C tampoco tiene demanda ni le falta nada.
    fc = make_forecast({"A": (6, 0), "B": (0, 0), "C": (0, 0)})
    st = make_state({"A": (4, 20), "B": (15, 20), "C": (5, 20)})
    orders = Asignador().decide(T, st, [], fc, params(lam=0.5, mu=1.0))
    d = by_station(orders)
    assert d.get("A", 0) > 0, d
    assert d.get("B", 0) < 0, d                  # las saca de B
    assert sum(d.values()) == 0, d               # nada de bodega


# ---------------------------------------------------------------- (b)

def test_b_large_lambda_no_moves_when_saving_below_lambda():
    fc = make_forecast({"A": (12, 0), "B": (0, 0)})
    st = make_state({"A": (4, 20), "B": (15, 20)})
    a = Asignador()
    plan = a.plan(T, st, [], fc, params(lam=0))
    saving = plan.surrogate(plan.phat) - plan.surrogate()
    assert saving > 0
    # λ mayor que todo el ahorro posible → nada se mueve
    assert Asignador().decide(T, st, [], fc, params(lam=saving + 1)) == []
    # λ menor que el ahorro de un solo movimiento (desde bodega) → sí se mueve
    assert Asignador().decide(T, st, [], fc, params(lam=saving / 10)) != []


# ---------------------------------------------------------------- (c)

def test_c_hourly_cap_counts_pending():
    names = [f"S{i}" for i in range(10)]
    rates = {nm: (12, 0) for nm in names} | {f"D{i}": (0, 0) for i in range(10)}
    stations = {nm: (3, 20) for nm in names} | {f"D{i}": (18, 20) for i in range(10)}
    fc, st = make_forecast(rates), make_state(stations)
    eff = T + timedelta(minutes=60)
    pending = [Order(T - timedelta(minutes=15), eff - timedelta(minutes=15), f"D{i}", -1) for i in range(3)]
    # otras 5 pendientes efectivas en t: fuera de toda ventana de 60 min que contenga t+L
    pending += [Order(T - timedelta(minutes=60), T, f"S{i}", 1) for i in range(5)]
    orders = Asignador().decide(T, st, pending, fc, params(lam=0, tope_hora=5))
    assert 0 < len(orders) <= 2
    # sin pendientes en la ventana, llega hasta el tope
    orders = Asignador().decide(T, st, [], fc, params(lam=0, tope_hora=5))
    assert len(orders) == 5


def test_c_hourly_cap_uses_own_history_for_short_lead():
    # L = 30: las órdenes ya aplicadas (no pendientes) siguen contando en la ventana
    fc = make_forecast({f"S{i}": (12, 0) for i in range(6)})
    st = make_state({f"S{i}": (2, 20) for i in range(6)})
    a = Asignador()
    p = params(lam=0, tope_hora=3, lead_min=30)
    first = a.decide(T, st, [], fc, p)
    assert len(first) == 3
    # 30 min después las órdenes de `first` ya se aplicaron (no llegan como pendientes)
    second = a.decide(T + timedelta(minutes=30), st, [], fc, p)
    assert second == []


# ---------------------------------------------------------------- (d)

def test_d_bodega_cap_respected():
    # todas se vacían y no hay donantes: solo la bodega puede surtir
    names = [f"S{i}" for i in range(5)]
    fc = make_forecast({nm: (12, 0) for nm in names})
    st = make_state({nm: (2, 20) for nm in names}, warehouse=1)
    pending = [Order(T - timedelta(minutes=30), T + timedelta(minutes=30), "S0", 1)]
    orders = Asignador().decide(T, st, pending, fc, params(lam=0, tope_bodega=4))
    net = sum(o.delta for o in orders)
    assert net > 0
    assert abs(1 + 1 + net) <= 4
    # sin tope, pediría más de la bodega
    orders = Asignador().decide(T, st, pending, fc, params(lam=0, tope_bodega=1000))
    assert sum(o.delta for o in orders) > 2


def test_d_bodega_cap_negative_side():
    # todas se llenan: solo se pueden mandar bicis a bodega
    names = [f"S{i}" for i in range(5)]
    fc = make_forecast({nm: (0, 12) for nm in names})
    st = make_state({nm: (18, 20) for nm in names}, warehouse=-2)
    orders = Asignador().decide(T, st, [], fc, params(lam=0, tope_bodega=5))
    net = sum(o.delta for o in orders)
    assert net < 0 and abs(-2 + net) <= 5


# ---------------------------------------------------------------- (e)

def test_e_integer_orders_effective_at_t_plus_L():
    fc = make_forecast({"A": (12, 0), "B": (0, 0), "C": (0, 9)})
    st = make_state({"A": (4, 20), "B": (15, 20), "C": (15, 20)})
    for L in (60, 45, 30):
        orders = Asignador().decide(T, st, [], fc, params(lam=0, lead_min=L))
        assert orders
        for o in orders:
            assert isinstance(o.delta, int) and o.delta != 0
            assert o.issued_at == T
            assert o.effective_at == T + timedelta(minutes=L)
        # nunca deja una estación fuera de [0, cap] según la proyección
        pl = Asignador().plan(T, st, [], fc, params(lam=0, lead_min=L))
        assert ((pl.y >= 0) & (pl.y <= pl.K)).all()


# ---------------------------------------------------------------- (f)

def test_f_no_orders_when_effective_at_or_after_0030():
    fc = make_forecast({"A": (12, 0), "B": (0, 0)})
    st = lambda t: make_state({"A": (4, 20), "B": (15, 20)}, t=t)
    for t, L in [(datetime(2025, 9, 3, 23, 30), 60), (datetime(2025, 9, 4, 0, 15), 60),
                 (datetime(2025, 9, 4, 0, 0), 30), (datetime(2025, 9, 3, 23, 45), 45),
                 (datetime(2025, 9, 4, 0, 15), 15)]:
        assert Asignador().decide(t, st(t), [], fc, params(lam=0, lead_min=L)) == []
    t = datetime(2025, 9, 3, 23, 15)          # efectiva 00:15 < 00:30: sí puede ordenar
    assert Asignador().decide(t, st(t), [], fc, params(lam=0, lead_min=60, H_horas=1)) != []
    t = datetime(2025, 9, 3, 23, 45)          # L = 30: efectiva 00:15, ya del día siguiente
    assert Asignador().decide(t, st(t), [], fc, params(lam=0, lead_min=30, H_horas=1)) != []


def test_pending_orders_enter_projection():
    # si ya hay una orden pendiente que surte A, no hace falta volver a pedir
    fc = make_forecast({"A": (12, 0), "B": (0, 0)})
    st = make_state({"A": (4, 20), "B": (15, 20)})
    first = Asignador().decide(T, st, [], fc, params(lam=0))
    add = by_station(first)["A"]
    pending = [Order(T - timedelta(minutes=15), T + timedelta(minutes=45), "A", 12)]
    second = Asignador().decide(T, st, pending, fc, params(lam=0))
    assert by_station(second).get("A", 0) < add


# ---------------------------------------------------------------- correcciones de review

def test_k_time_limit_without_feasible_solution_falls_back_to_no_moves():
    # 600 estaciones y límite de tiempo ~0: HiGHS no llega a una solución factible
    rates = {f"S{i:03d}": ((12, 0) if i % 2 else (0, 12)) for i in range(600)}
    stations = {nm: ((2, 20) if i % 2 else (18, 20)) for i, nm in enumerate(rates)}
    fc, st = make_forecast(rates), make_state(stations)
    a = Asignador(time_limit=1e-9)
    with pytest.warns(UserWarning, match="sin solución factible"):
        orders = a.decide(T, st, [], fc, params(lam=0, tope_hora=60, tope_bodega=200))
    # o no salen órdenes, o las que salen respetan los topes
    assert len(orders) <= 60
    assert abs(sum(o.delta for o in orders)) <= 200
    assert orders == [] and len(a.fallbacks) == 1 and a.fallbacks[0][1].startswith("fallback")
    # con tiempo normal sí resuelve y no registra fallback
    b = Asignador()
    orders = b.decide(T, st, [], fc, params(lam=0, tope_hora=60, tope_bodega=200, H_horas=6))
    assert 0 < len(orders) <= 60 and all(abs(o.delta) <= 22 for o in orders)
    assert b.fallbacks == []


def test_history_resets_on_new_run_same_day():
    fc = make_forecast({f"S{i}": (12, 0) for i in range(6)})
    st = make_state({f"S{i}": (2, 20) for i in range(6)})
    a = Asignador()
    p = params(lam=0, tope_hora=3, lead_min=30)
    assert len(a.decide(T, st, [], fc, p)) == 3
    assert a.decide(T + timedelta(minutes=30), st, [], fc, p) == []   # misma corrida: tope gastado
    # nueva corrida del mismo día (t vuelve atrás): la historia no se arrastra
    assert len(a.decide(T, st, [], fc, p)) == 3


def test_forecast_without_requested_block_raises():
    fc = make_forecast({"A": (12, 0), "B": (0, 0)}, block_min=30)
    st = make_state({"A": (4, 20), "B": (15, 20)})
    with pytest.raises(ValueError, match="sin bloques de 60"):
        Asignador().decide(T, st, [], fc, params(lam=0, block_min=60))
    assert Asignador().decide(T, st, [], fc, params(lam=0, block_min=30)) != []


# ---------------------------------------------------------------- run 2

def test_g_no_order_above_max_bikes_per_move():
    # A se vacía muy rápido (60/h) y cabe mucho; B y C tienen de sobra
    fc = make_forecast({"A": (60, 0), "B": (0, 0), "C": (0, 0)})
    st = make_state({"A": (0, 60), "B": (60, 60), "C": (60, 60)})
    for cap in (14, 22, 42):
        orders = Asignador().decide(T, st, [], fc, params(lam=0, max_bikes_per_move=cap, tope_bodega=0))
        d = by_station(orders)
        assert orders and all(abs(o.delta) <= cap for o in orders), (cap, d)
        assert d["A"] == cap                      # el tope es lo que ata
    # sin el tope (enorme) A recibiría más de 42
    d = by_station(Asignador().decide(T, st, [], fc, params(lam=0, max_bikes_per_move=10_000, tope_bodega=0)))
    assert d["A"] > 42


def test_h_uses_latest_issue_not_future():
    # emisión 05:30: A sin demanda (nada que hacer); 07:00: A se vacía;
    # 07:15 (futura para t = 07:00): A se llena. Todas con refresh_min = 15.
    f0 = make_forecast({"A": (0, 0), "B": (0, 0)}, refresh_min=15)
    f1 = make_forecast({"A": (12, 0), "B": (0, 0)}, issued_at=f"{DAY} 07:00", refresh_min=15)
    f2 = make_forecast({"A": (0, 12), "B": (0, 0)}, issued_at=f"{DAY} 07:15", refresh_min=15)
    fc = validate_forecast(pd.concat([f0, f1, f2], ignore_index=True))
    st = lambda t: make_state({"A": (4, 20), "B": (15, 20)}, t=t)
    p = params(lam=0.5, refresh_min=15)            # λ > 0: sin empates de costo 0
    a = Asignador()
    assert a.decide(datetime(2025, 9, 3, 6, 45), st(datetime(2025, 9, 3, 6, 45)), [], fc, p) == []
    assert a.last_plan.issued_at == pd.Timestamp(f"{DAY} 05:30")
    a = Asignador()
    d = by_station(a.decide(T, st(T), [], fc, p))
    assert a.last_plan.issued_at == pd.Timestamp(f"{DAY} 07:00")   # no la de 07:15
    assert d.get("A", 0) > 0                                        # se vacía, no se llena
    dep, arr = forecast_rates(fc, np.array(["A"]), T, 60, refresh_min=15)
    assert np.allclose(dep[0], 0.2) and np.allclose(arr[0], 0)
    # sin ninguna emisión ≤ t: error explícito
    with pytest.raises(ValueError, match="issued_at"):
        Asignador().decide(datetime(2025, 9, 3, 6, 45), st(T), [], f1, p)


def test_h_series_selected_by_refresh_min():
    # misma hora de emisión en dos series (f = 15 y f = 60) con contenido distinto
    f15 = make_forecast({"A": (12, 0), "B": (0, 0)}, refresh_min=15)
    f60 = make_forecast({"A": (0, 0), "B": (0, 0)}, refresh_min=60)
    fc = validate_forecast(pd.concat([f15, f60], ignore_index=True))
    st = make_state({"A": (4, 20), "B": (15, 20)})
    assert Asignador().decide(T, st, [], fc, params(lam=0.5, refresh_min=15)) != []
    assert Asignador().decide(T, st, [], fc, params(lam=0.5, refresh_min=60)) == []
    with pytest.raises(ValueError, match="refresh_min=0"):
        Asignador().decide(T, st, [], fc, params(lam=0, refresh_min=0))


def test_h_issue_must_cover_horizon():
    # una emisión que solo trae bloques hasta las 09:30 no alcanza para H = 2 desde 07:00
    fc = make_forecast({"A": (12, 0), "B": (0, 0)})
    fc = fc[fc["block_start"] < pd.Timestamp(f"{DAY} 09:30")]
    st = make_state({"A": (4, 20), "B": (15, 20)})
    with pytest.raises(ValueError, match="no cubre"):
        Asignador().decide(T, st, [], fc, params(lam=0, H_horas=2))
    assert Asignador().decide(T, st, [], fc, params(lam=0, H_horas=1)) != []


def test_i_full_day_no_orders_at_or_after_0030():
    fc = make_forecast({"A": (12, 0), "B": (0, 12), "C": (0, 0)})
    st0 = {"A": (4, 20), "B": (15, 20), "C": (10, 20)}
    for L in (60, 45, 30):
        a = Asignador()
        orders = []
        for t in C.decision_times(DAY):
            orders += a.decide(t, make_state(st0, t=t), [], fc, params(lam=0, lead_min=L, H_horas=6))
        end = C.day_bounds(DAY)[1]
        assert orders and max(o.effective_at for o in orders) < end
        assert max(o.issued_at for o in orders) + timedelta(minutes=L) < end
        # la última decisión con órdenes es la última con t + L < 00:30
        last = max(o.issued_at for o in orders)
        assert last == end - timedelta(minutes=L + C.STEP_MIN)


def test_i_hourly_history_survives_midnight():
    # L = 15: decisiones a las 23:45 (efectiva 00:00) y a las 00:00 del 4-sep
    # (efectiva 00:15), las dos del día (ventana) 3-sep y en la misma ventana
    # de 60 min: la historia del tope por hora no se borra a medianoche.
    fc = make_forecast({f"S{i}": (12, 0) for i in range(6)})
    st = lambda t: make_state({f"S{i}": (2, 20) for i in range(6)}, t=t)
    a = Asignador()
    p = params(lam=0, tope_hora=3, lead_min=15, H_horas=1)
    t1 = datetime(2025, 9, 3, 23, 45)
    assert len(a.decide(t1, st(t1), [], fc, p)) == 3
    t2 = datetime(2025, 9, 4, 0, 0)
    assert a.decide(t2, st(t2), [], fc, p) == []
    # una instancia nueva a las 00:00 sí ordena (la historia es lo que ata)
    b = Asignador()
    orders = b.decide(t2, st(t2), [], fc, p)
    assert len(orders) == 3 and all(o.effective_at == datetime(2025, 9, 4, 0, 15) for o in orders)
    # y usa el ancla de la ventana del 3-sep: minuto 00:00 = 05:30 + 1110
    assert b.last_plan.issued_at == pd.Timestamp(f"{DAY} 05:30")


def test_day0_after_midnight_is_same_window():
    from ecosim.asignador import _day0
    assert _day0(datetime(2025, 9, 4, 0, 15)) == pd.Timestamp("2025-09-03 05:30")
    assert _day0(datetime(2025, 9, 3, 5, 30)) == pd.Timestamp("2025-09-03 05:30")
    # tasas en 00:00–00:30 del 4-sep = bloque de las 23:30 del 3-sep
    fc = make_forecast({"A": (lambda b: (30, 0) if b == pd.Timestamp(f"{DAY} 23:30") else (0, 0))})
    dep, _ = forecast_rates(fc, np.array(["A"]), datetime(2025, 9, 4, 0, 0), 60)
    assert np.allclose(dep[0, 1080:1140], 0.5) and np.allclose(dep[0, :1080], 0)


def test_no_forecast_raises():
    st = make_state({"A": (4, 20), "B": (15, 20)})
    with pytest.raises(ValueError, match="sin pronóstico"):
        Asignador().decide(T, st, [], None, params(lam=0))
    # después del corte de 00:30 no hace falta pronóstico: no hay nada que ordenar
    t = datetime(2025, 9, 3, 23, 45)
    assert Asignador().decide(t, make_state({"A": (4, 20)}, t=t), [], None, params(lam=0)) == []


def test_j_no_retiro_above_projection():
    # B: 1 bici en 07:30 y +1.6 llegadas en [07:30, 08:30) → p = 2.6, p̂ = 3,
    # ⌊p⌋ = 2 (default), σ = √1.6 ≈ 1.26 y ⌊p − σ⌋ = 1 (variante "sigma"). Desde las 08:30 B se vacía sola
    # (600 salidas/h), así que es donante gratis; A necesita bicis y la
    # bodega está cerrada: el retiro de B lo fija la cota.
    t = datetime(2025, 9, 3, 7, 30)
    def rate_b(b):
        return (0, 1.6) if b == pd.Timestamp(f"{DAY} 07:30") else (600, 0)
    fc = make_forecast({"A": (3, 0), "B": rate_b})
    st = make_state({"A": (0, 20), "B": (1, 10)}, t=t)
    p = params(lam=0, tope_bodega=0)
    a = Asignador()
    d = by_station(a.decide(t, st, [], fc, p))
    pl = a.last_plan
    i = list(pl.names).index("B")
    assert pl.p[i] == pytest.approx(2.6) and pl.phat[i] == 3
    assert d["B"] == -2 and d["A"] == 2           # default: ⌊p⌋, no p̂
    assert pl.r[i] == 2
    a = Asignador(retiro="sigma")                 # variante con margen: ⌊p − σ⌋
    d = by_station(a.decide(t, st, [], fc, p))
    assert d["B"] == -1 and a.last_plan.r[i] == np.floor(2.6 - np.sqrt(1.6))
    # la cota del run 1 (p̂) pediría 3, una bici que tal vez no esté
    d = by_station(Asignador(retiro="round").decide(t, st, [], fc, p))
    assert d["B"] == -3
    # propiedad general: ningún retiro supera las bicis proyectadas en t+L
    rng = np.random.default_rng(0)
    names = [f"S{k:02d}" for k in range(40)]
    rates = {nm: (float(rng.uniform(0, 15)), float(rng.uniform(0, 15))) for nm in names}
    stations = {nm: (int(rng.integers(0, 20)), 20) for nm in names}
    a = Asignador()
    orders = a.decide(T, make_state(stations), [], make_forecast(rates), params(lam=0, tope_bodega=1000))
    pl = a.last_plan
    pos = {nm: k for k, nm in enumerate(pl.names)}
    assert any(o.delta < 0 for o in orders)
    for o in orders:
        k = pos[o.short_name]
        if o.delta < 0:
            assert -o.delta <= pl.r[k] <= np.floor(pl.p[k] + 1e-9)
        else:
            assert o.delta <= pl.u[k] <= pl.K[k] - np.ceil(pl.p[k] - 1e-9)


def test_damaged_bikes_reduce_useful_capacity_and_can_change():
    # cap 20, 8 dañadas: lugar útil = bicis + anclajes libres = 12
    fc = make_forecast({"A": (0, 0), "B": (0, 30)})
    a = Asignador()
    st = make_state({"A": (2, 20), "B": (0, 20)}, disabled={"A": 8})
    a.decide(T, st, [], fc, params(lam=0))
    assert list(a.last_plan.K) == [12, 20]
    assert (a.last_plan.y <= a.last_plan.K).all()
    # en la siguiente decisión se repararon 6 dañadas: el asignador usa el estado nuevo
    t2 = T + timedelta(minutes=15)
    st2 = make_state({"A": (8, 20), "B": (0, 20)}, t=t2, disabled={"A": 2})
    a.decide(t2, st2, [], fc, params(lam=0))
    assert list(a.last_plan.K) == [18, 20]


def test_no_orders_for_out_of_service_stations():
    fc = make_forecast({"A": (12, 0), "B": (0, 0), "X": (0, 0)})
    # X como la 698 el 2025-10-25: 0 bicis, 15 anclajes libres, fuera de servicio
    st = make_state({"A": (4, 20), "B": (15, 20), "X": (0, 15)}, oos={"B", "X"})
    d = by_station(Asignador().decide(T, st, [], fc, params(lam=0.5)))
    assert "B" not in d and "X" not in d          # A se surte de bodega, no de B
    assert d.get("A", 0) > 0
    # sin la marca, X (vacía) sí recibiría bicis y B sí donaría
    d = by_station(Asignador().decide(T, make_state({"A": (4, 20), "B": (15, 20), "X": (0, 15)}),
                                      [], fc, params(lam=0.5, tope_bodega=0)))
    assert d.get("X", 0) > 0 and d.get("B", 0) < 0
