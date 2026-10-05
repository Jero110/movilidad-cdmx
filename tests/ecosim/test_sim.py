"""Regresión del motor run 3: flota, tránsito, daños y replay."""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from ecosim import config as C, contracts as K, sim

DAY = "2025-08-13"
START, END = C.day_bounds(DAY)


def at(h, m=0):
    return START.replace(hour=h, minute=m) if h >= 5 else END.replace(hour=h, minute=m)


def init(**stocks):
    return pd.DataFrame([dict(short_name=s, bikes=b, disabled=d, docks_disabled=0,
                              cap=c, out_of_service=False) for s, (b, d, c) in stocks.items()])


def neighbors():
    return pd.DataFrame([(a, b, d) for a, b, d in [("A", "B", 300.), ("B", "A", 300.),
             ("A", "C", 750.), ("C", "A", 750.), ("B", "C", 400.), ("C", "B", 400.)]],
             columns=["short_name", "nbr", "dist_m"])


def trips(*rows):
    df = pd.DataFrame(rows, columns=["o", "d", "t_dep", "t_arr"])
    df["bike"] = [str(i) for i in range(len(df))]
    df["t_dep"] = pd.to_datetime(df.t_dep)
    df["t_arr"] = pd.to_datetime(df.t_arr)
    return df


def damage(*rows):
    df = pd.DataFrame(rows, columns=K.DAMAGE_EVENTS_COLUMNS)
    df.t = pd.to_datetime(df.t)
    df.n = df.n.astype(int)
    return df


def run(st, travel=None, orders=None, policy=None, damage_events=None, **kw):
    return sim.simulate(st, travel if travel is not None else trips(), neighbors(), START, END,
                        orders=orders, policy=policy, damage_events=damage_events, day=DAY, **kw)


def stock(r, station, when, field="bikes_minute"):
    i = list(r.extra["stations"]).index(station)
    m = int((when - START).total_seconds() // 60)
    return int(r.extra[field][m, i])


def order(t, s, d, pickup=15, delivery=60):
    return K.Order(t, t + timedelta(minutes=pickup), t + timedelta(minutes=delivery), s, d)


class Script:
    def __init__(self, schedule):
        self.schedule = schedule
        self.seen = []

    def decide(self, t, state, pending_orders, forecast, params):
        self.seen.append((t, state.warehouse, list(pending_orders)))
        return [order(t, s, d, params.pickup_min, params.delivery_min)
                for s, d in self.schedule.get(t, [])]


def test_conservacion_sin_y_con_ordenes_en_cada_minuto():
    st = init(A=(3, 0, 8), B=(2, 1, 8), C=(2, 0, 8))
    travel = trips(("A", "B", at(6), at(6, 30)), ("C", "A", at(7), at(7, 20)))
    ev = damage(("B", at(6, 10), "sube", 1), ("B", at(8), "baja", 1))
    for policy in (None, Script({at(6): [("A", -2), ("C", 2)]})):
        r = run(st, travel, policy=policy, damage_events=ev)
        assert r.extra["truck_end"] == 0
        for m, stamp in enumerate(r.extra["minutes"]):
            in_station = int(r.extra["bikes_minute"][m].sum() + r.extra["disabled_minute"][m].sum())
            in_trip = sum(dep <= stamp < arr for _, _, dep, arr in travel[["o", "d", "t_dep", "t_arr"]].itertuples(index=False, name=None))
            oa = r.extra["orders_applied"]
            load = -int(oa.loc[(oa.delta < 0) & (oa.pickup_at <= stamp), "applied"].sum()) - int(oa.loc[(oa.delta > 0) & (oa.delivery_at <= stamp), "applied"].sum())
            assert in_station + in_trip + load == r.extra["initial_total"]


def test_pickup_15_delivery_60_y_sensibilidad():
    st = init(A=(3, 0, 6), B=(0, 0, 6), C=(1, 0, 6))
    for delay in C.DELIVERY_SENS_MIN:
        p = Script({at(6): [("A", -2), ("B", 2)]})
        r = run(st, policy=p, params=K.PolicyParams(delivery_min=delay))
        assert stock(r, "A", at(6, 14)) == 3
        assert stock(r, "A", at(6, 15)) == 1
        before = at(6 + (delay - 1) // 60, (delay - 1) % 60)
        assert stock(r, "B", before) == 0
        assert stock(r, "B", at(6 + delay // 60, delay % 60)) == 2
        assert r.en_transito_max == 2 and r.extra["truck_end"] == 0
        assert r.visitas == 2 and r.bicis_movidas == 4


def test_recorte_recogida_escala_entregas_de_misma_decision():
    st = init(A=(1, 0, 5), B=(0, 0, 5), C=(0, 0, 5))
    p = Script({at(6): [("A", -4), ("C", 1), ("B", 3)]})
    r = run(st, policy=p)
    # B gana el único resto: 3/4 contra 1/4, sin deuda entre decisiones.
    assert (stock(r, "B", at(7)), stock(r, "C", at(7))) == (1, 0)
    assert r.recortes["entrega_sin_recogida"] == 3
    assert r.extra["truck_end"] == 0
    with pytest.raises(ValueError, match="equilibrar"):
        run(st, policy=Script({at(6): [("A", -1), ("B", 2)]}))


def test_entrega_sin_anclaje_vuelve_al_origen():
    st = init(A=(2, 0, 3), B=(0, 0, 1), C=(1, 0, 3))
    travel = trips(("C", "B", at(6, 30), at(6, 45)))
    r = run(st, travel, policy=Script({at(6): [("A", -2), ("B", 2)]}))
    assert stock(r, "B", at(7)) == 1
    assert stock(r, "A", at(7)) == 2
    assert r.recortes["devolucion_origen"] == 2
    assert r.extra["truck_end"] == 0


def test_danadas_en_sitio_antes_de_orden_y_no_aplicables():
    st = init(A=(1, 0, 5), B=(0, 2, 5), C=(1, 0, 5))
    ev = damage(("A", at(6, 15), "sube", 3), ("B", at(6, 15), "baja", 3))
    r = run(st, policy=Script({at(6): [("A", -1), ("C", 1)]}), damage_events=ev)
    assert stock(r, "A", at(6, 15), "disabled_minute") == 1
    assert stock(r, "B", at(6, 15)) == 2
    assert r.danadas_no_aplicables == 3
    assert r.visitas == 0 and r.extra["truck_end"] == 0
    with pytest.raises(ValueError, match="kind"):
        run(st, damage_events=damage(("A", at(6), "taller_retiro", 1)))


def test_desvios_distancias_y_parte_mayor_500():
    st = init(A=(0, 0, 4), B=(0, 0, 2), C=(2, 0, 2))
    travel = trips(("A", "C", at(6), at(6, 30)))
    fill = [K.Order(at(6, 10), at(6, 10), at(6, 10), "C", 1)]
    r = run(st, travel, orders=fill)
    assert r.desvios_salida == 1 and r.desvios_llegada == 1
    assert r.extra["detours"].dist_m.tolist() == [750., 400.]
    assert r.km_desvio_medio == pytest.approx(.575)
    assert r.extra["desvios_gt500_fraccion"] == .5
    assert r.extra["trips_served"] == 1


def test_replay_sintetico_reconstruye_foto_y_filtra_pares(tmp_path):
    t0, t1, t2 = at(6), at(6, 15), at(6, 30)
    mv = pd.DataFrame([("A", t0, t1, -1, 0, 0, False),
                       ("B", t0, t1, 1, 0, 0, False),
                       ("A", t1, t2, 1, 0, 0, True),
                       ("A", t2, at(6, 45), -1, 0, 0, True)], columns=K.ECOBICI_MOVES_COLUMNS)
    mv.t0 = pd.to_datetime(mv.t0)
    mv.t1 = pd.to_datetime(mv.t1)
    mv.par = mv.par.astype(bool)
    p = tmp_path / "moves.parquet"
    mv.to_parquet(p)
    oo = sim.ecobici_orders(DAY, path=p)
    assert [(o.short_name, o.delta, o.delivery_at) for o in oo] == [("A", -1, t1), ("B", 1, t1)]
    st = init(A=(3, 0, 5), B=(0, 0, 5), C=(1, 0, 5))
    ev = damage(("A", t1, "sube", 1), ("A", t2, "baja", 1))
    r = run(st, orders=oo, damage_events=ev, record_at=[t1, t2])
    assert r.extra["bikes_record"][:, :2].tolist() == [[1, 1], [2, 1]]
    assert r.extra["disabled_record"][:, :2].tolist() == [[1, 0], [0, 0]]
    assert r.visitas == 2 and r.extra["replay_external"] == 0


def test_variante_feed_entrada_y_salida_de_danada():
    st = init(A=(1, 1, 5), B=(0, 0, 5), C=(1, 0, 5))
    now = at(6)
    oo = [K.Order(now, now, now, "A", -1), K.Order(now, now, now, "B", 1)]
    ev = damage(("A", now, "baja", 1), ("B", now, "sube", 1))
    onsite = run(st, orders=oo, damage_events=ev)
    mv = pd.DataFrame([("A", at(5, 45), now, -1, 0, 0, False),
                       ("B", at(5, 45), now, 1, 0, 0, False)], columns=K.ECOBICI_MOVES_COLUMNS)
    mv.t0 = pd.to_datetime(mv.t0)
    mv.t1 = pd.to_datetime(mv.t1)
    feed = run(st, orders=oo, damage_events=ev, damage_variant="feed", feed_moves=mv)
    assert (stock(onsite, "A", now), stock(onsite, "A", now, "disabled_minute")) == (1, 0)
    assert (stock(feed, "A", now), stock(feed, "A", now, "disabled_minute")) == (1, 0)
    assert (stock(onsite, "B", now), stock(onsite, "B", now, "disabled_minute")) == (1, 0)
    assert (stock(feed, "B", now), stock(feed, "B", now, "disabled_minute")) == (0, 1)
    assert onsite.danadas_no_aplicables == 1 and feed.danadas_no_aplicables == 0
    assert feed.extra["truck_end"] == 0


def test_variante_feed_mismos_flujos_externos_con_politica_y_baseline():
    st = init(A=(1, 1, 5), B=(0, 0, 5), C=(1, 0, 5))
    now = at(6)
    ev = damage(("A", now, "baja", 1), ("B", now, "sube", 1))
    mv = pd.DataFrame([("A", at(5, 45), now, -1, 0, 0, False),
                       ("B", at(5, 45), now, 1, 0, 0, False)], columns=K.ECOBICI_MOVES_COLUMNS)
    mv.t0 = pd.to_datetime(mv.t0)
    mv.t1 = pd.to_datetime(mv.t1)
    pol = Script({at(7): [("C", -1), ("A", 1)]})
    baseline = run(st, damage_events=ev, damage_variant="feed", feed_moves=mv)
    policy = run(st, damage_events=ev, damage_variant="feed", feed_moves=mv, policy=pol)
    replay = run(st, damage_events=ev, damage_variant="feed", feed_moves=mv,
                 orders=[K.Order(now, now, now, "A", -1), K.Order(now, now, now, "B", 1)])
    assert baseline.extra["damage_external"] == policy.extra["damage_external"] == replay.extra["damage_external"] == 0
    for res in (baseline, policy, replay):
        assert stock(res, "A", now, "disabled_minute") == 0
        assert stock(res, "B", now, "disabled_minute") == 1
        assert res.extra["truck_end"] == 0
    assert policy.visitas == 2 and policy.extra["replay_external"] == 0
    with pytest.raises(ValueError, match="feed_moves"):
        run(st, policy=Script({}), damage_events=ev, damage_variant="feed")


def test_variante_feed_flujo_danadas_no_nulo_con_politica():
    st = init(A=(1, 0, 5), B=(1, 2, 5), C=(1, 0, 5))
    ev = damage(("A", at(6), "sube", 1), ("B", at(7), "baja", 1))
    mv = pd.DataFrame([("A", at(5, 45), at(6), 1, 0, 0, False),
                       ("B", at(6, 45), at(7), -1, 0, 0, False)], columns=K.ECOBICI_MOVES_COLUMNS)
    mv.t0 = pd.to_datetime(mv.t0)
    mv.t1 = pd.to_datetime(mv.t1)
    pol = Script({at(8): [("C", -1), ("A", 1)]})
    r = run(st, policy=pol, damage_events=ev, damage_variant="feed", feed_moves=mv)
    assert stock(r, "A", at(6), "disabled_minute") == 1
    assert stock(r, "B", at(7), "disabled_minute") == 1
    assert r.extra["damage_external"] == 0  # +1 a las 06:00 y −1 a las 07:00
    assert r.extra["replay_external"] == 0 and r.extra["truck_end"] == 0
    in_st = r.extra["bikes_minute"].sum(axis=1) + r.extra["disabled_minute"].sum(axis=1)
    assert in_st[60] == r.extra["initial_total"] + 1
    assert in_st[120] == r.extra["initial_total"]
    assert r.visitas == 2


def test_conservacion_desconocidas_y_replay_neto_explicito():
    st = init(A=(2, 0, 5), B=(2, 0, 5), C=(1, 0, 5))
    travel = trips(("A", "Z", at(6), at(6, 20)), ("Z", "B", at(6), at(6, 20)))
    oo = [K.Order(at(6), at(6), at(6), "A", -1)]
    r = run(st, travel, orders=oo)
    assert (r.extra["unknown_in"], r.extra["unknown_out"], r.extra["replay_external"]) == (1, 1, -1)
    assert r.extra["truck_end"] == 0


def test_replay_neto_igual_suma_movimientos_aplicados_y_balance_minuto():
    st = init(A=(3, 0, 5), B=(2, 0, 5), C=(1, 0, 5))
    oo = [K.Order(at(6), at(6), at(6), "A", 2),
          K.Order(at(6, 15), at(6, 15), at(6, 15), "B", -1)]
    r = run(st, orders=oo)
    applied = r.extra["orders_applied"]
    assert r.extra["replay_external"] == int(applied.applied.sum()) == 1
    initial = r.extra["initial_total"]
    for m, stamp in enumerate(r.extra["minutes"]):
        net = int(applied.loc[applied.delivery_at <= stamp, "applied"].sum())
        stock_total = int(r.extra["bikes_minute"][m].sum() + r.extra["disabled_minute"][m].sum())
        assert stock_total - net == initial
    assert r.extra["replay_neto"] == 1
    diag = run(st, orders=oo, neutralize_replay=True)
    assert diag.extra["replay_external"] == 0
    assert sum(x[2] for x in diag.extra["neutralization"]) == -1


def test_ordenes_dataframe_y_lista_equivalentes():
    st = init(A=(3, 0, 5), B=(1, 0, 5), C=(1, 0, 5))
    oo = [K.Order(at(6), at(6), at(6), "A", -1),
          K.Order(at(6), at(6), at(6), "B", 1)]
    a = run(st, orders=oo)
    b = run(st, orders=K.orders_to_frame(oo))
    assert a.metrics == b.metrics
    pd.testing.assert_frame_equal(a.extra["orders_applied"], b.extra["orders_applied"])


def test_record_at_guarda_antes_y_despues_del_viaje():
    st = init(A=(2, 0, 5), B=(1, 0, 5), C=(1, 0, 5))
    t = at(6) + timedelta(seconds=30)
    r = run(st, trips(("A", "B", t, at(6, 10))),
            record_at=[t - timedelta(seconds=1), t, at(6, 10)])
    j = list(r.extra["stations"]).index("A")
    assert r.extra["bikes_record"][:, j].tolist() == [2, 1, 1]
    assert r.extra["record_times"] == list(pd.to_datetime([t - timedelta(seconds=1), t, at(6, 10)]))


def test_viaje_duracion_cero_sale_antes_de_llegar():
    st = init(A=(2, 0, 2), B=(1, 0, 5), C=(1, 0, 5))
    r = run(st, trips(("A", "A", at(6), at(6))))
    assert stock(r, "A", at(6)) == 2
    assert r.desvios_salida == r.desvios_llegada == 0
    with pytest.raises(ValueError, match="antes de salida"):
        run(st, trips(("A", "B", at(6, 20), at(6))))


def test_estacion_sin_coordenadas_fallback_determinista():
    st = init(A=(2, 0, 5), B=(2, 0, 5), C=(1, 0, 5), X=(0, 0, 0))
    r = run(st, trips(("X", "C", at(6), at(6, 15))))
    det = r.extra["detours"]
    assert det.iloc[0][["kind", "orig", "to"]].tolist() == ["dep", "X", "A"]
    assert pd.isna(det.iloc[0].dist_m)
    assert r.extra["trips_served"] == 1
    assert r.km_desvio_medio == 0


def test_load_damage_events_filtros_y_validacion(tmp_path):
    ev = damage(("A", at(6), "sube", 1), ("A", at(4), "baja", 1))
    path = tmp_path / "damage.parquet"
    ev.to_parquet(path)
    selected, source = sim.load_damage_events(DAY, path=path)
    assert source == path.name and selected.kind.tolist() == ["sube"]
    frame, source = sim.load_damage_events(DAY, damage_events=ev)
    assert source == "DataFrame" and frame.kind.tolist() == ["sube"]
    assert sim.load_damage_events(DAY, "fixed") == (None, "fixed")
    with pytest.raises(FileNotFoundError):
        sim.load_damage_events(DAY, path=tmp_path / "missing.parquet")
    with pytest.raises(ValueError, match="damage_events"):
        sim.load_damage_events(DAY, "inventado")


def test_orders_y_policy_juntos_falla():
    st = init(A=(1, 0, 5), B=(1, 0, 5), C=(1, 0, 5))
    with pytest.raises(ValueError, match="excluyentes"):
        run(st, orders=[], policy=Script({}))


def test_run_day_real_si_existe_cache():
    if not (C.SNAPSHOT_DIR / f"{DAY}.parquet").exists() or not C.TRIPS_PARQUET.exists():
        pytest.skip("sin caché")
    r = sim.run_day(DAY)
    K.validate_day_result(r)
    assert r.extra["bikes_minute"].shape[0] == C.WINDOW_MIN
    assert r.extra["truck_end"] == 0
