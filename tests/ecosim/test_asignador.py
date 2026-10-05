"""Pruebas sintéticas del asignador run 3; sin datos ni módulos de otros workers."""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from ecosim import config as C
from ecosim.asignador import Asignador, flow_sim, lower_hull, station_costs
from ecosim.contracts import Order, PolicyParams, SimState

T = datetime(2025, 9, 3, 7)


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


def test_time_limit_fallback_and_counts():
    state, fc, p = fixture()
    a = Asignador(time_limit=1e-9)
    assert a.decide(T, state, [], fc, p) == []
    assert len(a.fallbacks) == 1
    assert len(a.timeouts) == 1


def test_cost_hull_and_flow():
    net = np.full((1, 60), -3/60)
    c = station_costs(np.array([5]), net, 0, 60)[0]
    assert c[0] == 60 and c[0] > c[1] > c[2] > c[3]
    h = lower_hull(c)
    assert all(np.interp(i, h, c[h]) <= c[i] + 1e-9 for i in range(6))
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
