"""Pruebas sintéticas del greedy y del asignador run 3; sin datos ni módulos de otros workers.

La referencia es la copia literal del greedy original (archivo sin commit del
checkout principal, guardado junto a estas pruebas); el greedy limpio debe
decidir exactamente lo mismo.
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from ecosim import config as C
from ecosim.asignador import Asignador, flow_sim, station_costs
from ecosim.contracts import Order, PolicyParams, SimState
from ecosim.greedy import greedy
from tests.ecosim._voraz_referencia import AsignadorVoraz as Referencia

T = datetime(2025, 9, 3, 7)
LAMBDAS = (10, 61, 120)
VISITS = (2, 3, 55)


def random_case(rng, integer=False):
    """r, u ≤ 17 y costos con c(0) = 0; mezcla ahorros y costos por bici."""
    n = int(rng.integers(2, 41))
    r = rng.integers(0, 18, n) * (rng.random(n) < .6)
    u = rng.integers(0, 18, n) * (rng.random(n) < .6)
    steps = rng.integers(-60, 20, (2, n, 17)) if integer else rng.uniform(-60, 20, (2, n, 17))
    pc, dc = (np.concatenate([np.zeros((n, 1)), np.cumsum(x, axis=1)], axis=1) for x in steps)
    return r.astype(int), u.astype(int), pc, dc


def cases():
    rng = np.random.default_rng(20261006)
    for case in range(120):
        r, u, pc, dc = random_case(rng, integer=case % 2 == 1)
        for lam in LAMBDAS:
            yield r, u, pc, dc, lam, VISITS[case % 3]


def test_greedy_igual_a_referencia_en_entradas_aleatorias():
    reference = Referencia()
    moved = 0
    for count, (r, u, pc, dc, lam, V) in enumerate(cases(), start=1):
        params = PolicyParams(lam=float(lam), visits_per_decision=V)
        want_pick, want_deliver, *_ = reference._solve(r, u, pc, dc, None, None, params, None)
        pick, deliver, _ = greedy(r, u, pc, dc, float(lam), V)
        assert np.array_equal(pick, want_pick) and np.array_equal(deliver, want_deliver), count
        moved += bool(deliver.any())
    assert count >= 200 and moved >= count // 3  # casos con y sin movimiento


def test_greedy_invariantes():
    for r, u, pc, dc, lam, V in cases():
        pick, deliver, paquetes = greedy(r, u, pc, dc, float(lam), V)
        assert pick.sum() == deliver.sum()
        assert (pick <= r).all() and (deliver <= u).all() and (pick >= 0).all() and (deliver >= 0).all()
        assert not ((pick > 0) & (deliver > 0)).any()
        assert np.count_nonzero(pick) + np.count_nonzero(deliver) <= V
        rebuilt_pick, rebuilt_deliver = np.zeros_like(pick), np.zeros_like(deliver)
        for receptor, x, donors in paquetes:
            assert x == sum(k for _, k in donors) and x > 0 and donors
            rebuilt_deliver[receptor] += x
            for j, k in donors:
                rebuilt_pick[j] += k
        assert np.array_equal(rebuilt_pick, pick) and np.array_equal(rebuilt_deliver, deliver)


def hand_case():
    """Receptores 0, 1, 2 (ahorros 30, 20 y 5 por bici) y donantes 3, 4, 5."""
    r = np.array([0, 0, 0, 3, 5, 2])
    u = np.array([4, 3, 2, 0, 0, 0])
    pc, dc = np.zeros((6, 6)), np.zeros((6, 6))
    for i, per in ((0, 30), (1, 20), (2, 5)):
        dc[i, : u[i] + 1] = -per * np.arange(u[i] + 1)
    for j, per in ((3, -6), (4, 0), (5, 2)):
        pc[j, : r[j] + 1] = per * np.arange(r[j] + 1)
    return r, u, pc, dc


def test_greedy_caso_a_mano():
    r, u, pc, dc = hand_case()
    # ratio: 3 → −8/3 (k=3), 4 → 2 (k=5), 5 → 7 (k=2); gain: 0 → 110, 1 → 50, 2 → 0.
    # Receptor 0: x=4 con 3 de la 3 y 1 de la 4 (ahorro 108). Receptor 1: solo
    # queda la 5; x=2 ahorra 16, x=3 no se completa. Receptor 2: gain 0, para.
    pick, deliver, paquetes = greedy(r, u, pc, dc, 10.0, 55)
    assert deliver.tolist() == [4, 2, 0, 0, 0, 0]
    assert pick.tolist() == [0, 0, 0, 3, 1, 2]
    assert paquetes == [(0, 4, [(3, 3), (4, 1)]), (1, 2, [(5, 2)])]
    # V=4: tras el primer paquete (3 visitas) no caben dos más.
    assert greedy(r, u, pc, dc, 10.0, 4)[2] == [(0, 4, [(3, 3), (4, 1)])]
    # V=2: solo un donante por paquete; x=3 con la 3 (ahorro 88).
    assert greedy(r, u, pc, dc, 10.0, 2)[2] == [(0, 3, [(3, 3)])]
    # λ muy alto: ningún paquete ahorra.
    pick, deliver, paquetes = greedy(r, u, pc, dc, 1000.0, 55)
    assert not pick.any() and not deliver.any() and paquetes == []


class FixtureForecast:
    forma = "directa"
    modelo = "fixture"

    def __init__(self, rates):
        self.rates = rates
        self.calls = []

    def table(self, t, n_hours):
        self.calls.append((t, n_hours))
        return np.array([[list(self.rates[name]) for _ in range(4*n_hours)]
                         for name in self.rates], dtype=float) / 4


def fixture(stations=None, rates=None, t=T, disabled=None, **params):
    stations = stations or {"A": (3, 20), "B": (19, 20), "C": (18, 20)}
    rates = rates or {"A": (12, 0), "B": (0, 0), "C": (0, 8)}
    disabled = disabled or {}
    st = pd.DataFrame([{"short_name": nm, "bikes": bikes, "disabled": disabled.get(nm, 0),
                        "docks": cap-bikes-disabled.get(nm, 0), "cap": cap}
                       for nm, (bikes, cap) in stations.items()])
    return SimState(t, st), FixtureForecast(rates), PolicyParams(n_hours=2, lam=0, **params)


def deltas(orders):
    return {o.short_name: o.delta for o in orders}


def test_balance_and_two_times():
    state, fc, p = fixture()
    orders = Asignador().decide(T, state, [], fc, p)
    assert orders and sum(o.delta for o in orders) == 0
    assert any(o.delta < 0 for o in orders) and any(o.delta > 0 for o in orders)
    assert len({o.short_name for o in orders}) == len(orders)
    assert all(o.pickup_at == T + timedelta(minutes=15) and
               o.delivery_at == T + timedelta(minutes=60) for o in orders)
    assert fc.calls == [(T, 2)]


def test_visit_cap_and_per_visit_cap():
    stations = {f"A{i}": (2, 30) for i in range(8)} | {f"B{i}": (29, 30) for i in range(8)}
    rates = {nm: ((20, 0) if nm[0] == "A" else (0, 0)) for nm in stations}
    state, fc, p = fixture(stations, rates, visits_per_decision=4, max_bikes_per_visit=2)
    orders = Asignador().decide(T, state, [], fc, p)
    assert len(orders) == 4
    assert all(abs(o.delta) <= 2 for o in orders)
    assert sum(o.delta for o in orders) == 0
    state, fc, p = fixture(stations, rates, visits_per_decision=1)
    assert Asignador().decide(T, state, [], fc, p) == []


def test_one_hour_has_no_delivery_window():
    state, fc, _ = fixture()
    assert Asignador().decide(T, state, [], fc, PolicyParams(n_hours=1)) == []
    assert fc.calls == []


def test_empty_station_cannot_donate_and_disabled_reduce_capacity():
    stations = {"A": (0, 20), "B": (16, 20), "C": (0, 20)}
    state, fc, p = fixture(stations, {"A": (12, 0), "B": (0, 0), "C": (0, 0)},
                            disabled={"B": 3})
    a = Asignador()
    orders = a.decide(T, state, [], fc, p)
    assert all(o.delta >= 0 for o in orders if o.short_name in ("A", "C"))
    assert a.last_plan.K.tolist() == [20, 17, 20]
    assert all(o.delta >= -16 for o in orders if o.short_name == "B")


def test_pending_pickup_and_delivery_affect_both_projections_and_cost():
    state, fc, p = fixture({"A": (2, 20), "B": (15, 20)},
                           {"A": (8, 0), "B": (0, 0)})
    old = Asignador().plan(T, state, [], fc, p)
    pending = [Order(T-timedelta(minutes=15), T+timedelta(minutes=15),
                     T+timedelta(minutes=60), "B", -8),
               Order(T-timedelta(minutes=15), T+timedelta(minutes=15),
                     T+timedelta(minutes=60), "A", 8)]
    new = Asignador().plan(T, state, pending, fc, p)
    assert new.pickup_stock[1] < old.pickup_stock[1]
    assert new.delivery_stock[0] > old.delivery_stock[0]
    assert new.r[1] < old.r[1]
    assert new.delivery_cost[0, 0] == 0  # costo incremental frente a pendiente
    orders = Asignador().decide(T, state, pending, fc, p)
    assert sum(o.delta for o in orders) == 0


def test_pending_after_delivery_changes_cost_but_not_limit():
    state, fc, p = fixture()
    a = Asignador().plan(T, state, [], fc, p)
    o = Order(T-timedelta(minutes=15), T, T+timedelta(minutes=75), "A", 12)
    b = Asignador().plan(T, state, [o], fc, p)
    assert np.array_equal(a.delivery_stock, b.delivery_stock)
    assert not np.array_equal(a.delivery_cost, b.delivery_cost)


def test_no_delivery_at_or_after_close():
    for t in (datetime(2025, 9, 3, 23, 30), datetime(2025, 9, 4, 0, 15)):
        state, fc, p = fixture(t=t)
        assert Asignador().decide(t, state, [], fc, p) == []
        assert not fc.calls
    t = datetime(2025, 9, 3, 23, 15)
    state, fc, p = fixture(t=t)
    orders = Asignador().decide(t, state, [], fc, p)
    assert orders and all(o.delivery_at < C.day_bounds("2025-09-03")[1] for o in orders)


def test_paquetes_numerados_y_equilibrados():
    stations = {f"A{i}": (2, 30) for i in range(4)} | {f"B{i}": (29, 30) for i in range(4)}
    rates = {nm: ((20, 0) if nm[0] == "A" else (0, 0)) for nm in stations}
    state, fc, p = fixture(stations, rates, max_bikes_per_visit=5)
    a = Asignador()
    orders = a.decide(T, state, [], fc, p)
    plan = a.last_plan
    assert plan.status == "greedy" and len(plan.paquetes) >= 2
    numbers = sorted({o.paquete for o in orders})
    assert numbers == list(range(1, len(plan.paquetes) + 1))
    for number, (receptor, x, donors) in enumerate(plan.paquetes, start=1):
        group = [o for o in orders if o.paquete == number]
        assert sum(o.delta for o in group) == 0
        assert {(o.short_name, o.delta) for o in group} == {(plan.names[receptor], x)} | {
            (plan.names[j], -k) for j, k in donors}
    assert plan.surrogate() == pytest.approx(sum(plan.delivery_cost[i, x] for i, x, _ in plan.paquetes)
                                             + sum(plan.pickup_cost[j, k] for _, _, d in plan.paquetes for j, k in d))


def test_noop_sin_receptores():
    state, fc, p = fixture({"A": (10, 20), "B": (10, 20)}, {"A": (0, 0), "B": (0, 0)})
    a = Asignador()
    assert a.decide(T, state, [], fc, p) == []
    assert a.last_plan.status in ("noop", "greedy") and a.last_plan.paquetes == []


def test_cost_and_flow():
    net = np.full((1, 60), -3/60)
    c = station_costs(np.array([5]), net, 0, 60)[0]
    assert c[0] == 60 and c[0] > c[1] > c[2] > c[3]
    dep, arr = np.zeros((1, 30), int), np.zeros((1, 30), int)
    dep[0, 10] = 1
    _, E, F = flow_sim(np.array([1]), np.array([3]), dep, arr, 0, 30)
    assert (E, F) == (20, 0)


def test_high_lambda_and_delivery_capacity():
    state, fc, p = fixture({"A": (0, 20), "B": (20, 20)},
                            {"A": (12, 0), "B": (0, 0)},
                            max_bikes_per_visit=3)
    orders = Asignador().decide(T, state, [], fc, p)
    assert orders and sum(o.delta for o in orders) == 0
    assert max(abs(o.delta) for o in orders) <= 3
    p.lam = 10_000
    assert Asignador().decide(T, state, [], fc, p) == []


def test_sigma_and_out_of_service():
    state, fc, p = fixture(retiro=1)
    state.stations.loc[state.stations.short_name == "B", "out_of_service"] = True
    a = Asignador()
    orders = a.decide(T, state, [], fc, p)
    assert "B" not in deltas(orders)
    assert sum(o.delta for o in orders) == 0


# --- costo por km (α) ------------------------------------------------------------

def random_D(rng, n):
    xy = rng.uniform(0, 10, (n, 2))
    return np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])


def tramos_km(paquetes, D):
    return sum(D[i, j] * k for i, _, donors in paquetes for j, k in donors)


def test_alfa_cero_o_sin_D_es_el_greedy_de_siempre():
    rng = np.random.default_rng(7)
    for r, u, pc, dc, lam, V in cases():
        D = random_D(rng, len(r))
        base = greedy(r, u, pc, dc, float(lam), V)
        for got in (greedy(r, u, pc, dc, float(lam), V, 0.0, D), greedy(r, u, pc, dc, float(lam), V, 5.0, None),
                    greedy(r, u, pc, dc, float(lam), V, alfa=0.0)):
            assert np.array_equal(got[0], base[0]) and np.array_equal(got[1], base[1]) and got[2] == base[2]


def test_alfa_invariantes_y_cuadre_por_paquete():
    rng = np.random.default_rng(11)
    moved = 0
    for r, u, pc, dc, lam, V in cases():
        D = random_D(rng, len(r))
        for alfa in (1.0, 5.0, 30.0):
            pick, deliver, paquetes = greedy(r, u, pc, dc, float(lam), V, alfa, D)
            assert pick.sum() == deliver.sum()
            assert (pick <= r).all() and (deliver <= u).all() and not ((pick > 0) & (deliver > 0)).any()
            assert np.count_nonzero(pick) + np.count_nonzero(deliver) <= V
            seen = set()
            for i, x, donors in paquetes:
                assert x == sum(k for _, k in donors) and x == deliver[i] and x > 0
                assert all(pick[j] == k for j, k in donors)
                assert i not in seen and not seen & {j for j, _ in donors}
                seen |= {i} | {j for j, _ in donors}
            assert sum(deliver) == sum(x for _, x, _ in paquetes)
            moved += bool(deliver.any())
    assert moved > 100


def test_alfa_baja_los_km_en_un_caso_chico():
    # Un receptor (0) pide 3; el donante lejano (1) es más barato por bici que el cercano (2).
    r = np.array([0, 3, 3])
    u = np.array([3, 0, 0])
    pc = np.array([[0, 0, 0, 0], [0, 5, 10, 15], [0, 20, 40, 60.0]])
    dc = np.array([[0, -100, -200, -300.0], [0] * 4, [0] * 4])
    D = np.array([[0, 10, 1], [10, 0, 9], [1, 9, 0.0]])
    base = greedy(r, u, pc, dc, 1.0, 55)
    cerca = greedy(r, u, pc, dc, 1.0, 55, 6.0, D)
    assert base[2] == [(0, 3, [(1, 3)])]
    assert cerca[2] == [(0, 3, [(2, 3)])]
    assert tramos_km(cerca[2], D) < tramos_km(base[2], D)


def test_alfa_baja_los_km_en_agregado():
    rng = np.random.default_rng(13)
    km0 = km = 0.0
    for r, u, pc, dc, lam, V in cases():
        D = random_D(rng, len(r))
        km0 += tramos_km(greedy(r, u, pc, dc, float(lam), V)[2], D)
        km += tramos_km(greedy(r, u, pc, dc, float(lam), V, 5.0, D)[2], D)
    assert km < 0.95 * km0


def test_alfa_nan_en_coordenadas_no_toma_ese_donante():
    r, u = np.array([0, 3, 3]), np.array([3, 0, 0])
    pc = np.array([[0, 0, 0, 0], [0, 5, 10, 15], [0, 20, 40, 60.0]])
    dc = np.array([[0, -100, -200, -300.0], [0] * 4, [0] * 4])
    D = np.array([[0, np.nan, 1], [np.nan, 0, 9], [1, 9, 0.0]])
    assert greedy(r, u, pc, dc, 1.0, 55, 5.0, D)[2] == [(0, 3, [(2, 3)])]


def test_asignador_con_alfa_usa_coordenadas_y_acerca_donantes():
    stations = {"R": (3, 20), "L": (19, 20), "N": (19, 20)}
    rates = {"R": (12, 0), "L": (0, 0), "N": (0, 0)}
    state, fc, _ = fixture(stations, rates, max_bikes_per_visit=20)
    coords = {"R": (19.43, -99.15), "L": (19.52, -99.15), "N": (19.431, -99.15)}  # L a ~10 km, N a ~0.1 km
    base = Asignador(coords).decide(T, state, [], fc, PolicyParams(n_hours=2, lam=0, max_bikes_per_visit=20))
    cerca = Asignador(coords).decide(T, state, [], fc, PolicyParams(n_hours=2, lam=0, max_bikes_per_visit=20, alfa=50.0))
    assert sum(o.delta for o in cerca) == 0 and all(o.paquete for o in cerca)
    donors = lambda orders: {o.short_name for o in orders if o.delta < 0}  # noqa: E731
    assert donors(base) and donors(cerca) == {"N"}
    with pytest.raises(ValueError):
        PolicyParams(alfa=-1)
