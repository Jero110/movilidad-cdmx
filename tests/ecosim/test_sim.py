"""Tests a mano del simulador (plan 2026-09-28-ecosim, regla 1.4; run 2:
plan 2026-09-28-ecosim2, subtask simulador2, ventana 05:30–00:30).

Casi todos usan un mundo de juguete (estaciones A..W con distancias
escritas a mano) y el mismo motor `sim.simulate` que usa `run_day`.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ecosim import config as C
from ecosim import contracts as K
from ecosim import sim

DAY = "2025-09-03"
T0 = datetime(2025, 9, 3, 5, 30)
T_END = datetime(2025, 9, 4, 0, 30)          # 00:30 del día siguiente


def at(hh, mm, ss=0):
    """Hora dentro de la ventana del 2025-09-03: 00:00–05:29 es del día siguiente."""
    d = 4 if hh < 5 else 3
    return datetime(2025, 9, d, hh, mm, ss)


def make_init(rows):
    """rows: dict short_name -> (bikes, cap) o (bikes, cap, disabled)."""
    out = []
    for s, v in rows.items():
        b, cap = v[0], v[1]
        dis = v[2] if len(v) > 2 else 0
        out.append({"short_name": s, "bikes": b, "disabled": dis, "docks_disabled": 0,
                    "cap": cap, "docks": cap - b - dis, "out_of_service": False})
    return pd.DataFrame(out)


def make_nbrs(dist):
    """dist: dict (a, b) -> metros, simétrico."""
    rows = []
    for (a, b), d in dist.items():
        rows += [(a, b, float(d)), (b, a, float(d))]
    return pd.DataFrame(rows, columns=["short_name", "nbr", "dist_m"])


def make_trips(rows):
    """rows: lista de (o, d, t_dep, t_arr)."""
    df = pd.DataFrame(rows, columns=["o", "d", "t_dep", "t_arr"])
    df.insert(0, "bike", [f"b{i}" for i in range(len(df))])
    df["t_dep"] = pd.to_datetime(df["t_dep"])
    df["t_arr"] = pd.to_datetime(df["t_arr"])
    return df


def make_events(rows):
    """rows: lista de (short_name, t, kind, n) → DamageEvents."""
    df = pd.DataFrame(rows, columns=K.DAMAGE_EVENTS_COLUMNS)
    df["t"] = pd.to_datetime(df["t"])
    df["n"] = df["n"].astype("int64")
    return df


def run(init, trips, nbrs, **kw):
    return sim.simulate(init, trips, nbrs, T0, T_END, **kw)


def bikes_at(res, station, t):
    m = int((pd.Timestamp(t) - pd.Timestamp(T0)) / pd.Timedelta(minutes=1))
    j = list(res.extra["stations"]).index(station)
    return int(res.extra["bikes_minute"][m, j])


def disabled_at(res, station, t):
    m = int((pd.Timestamp(t) - pd.Timestamp(T0)) / pd.Timedelta(minutes=1))
    j = list(res.extra["stations"]).index(station)
    return int(res.extra["disabled_minute"][m, j])


# Tres estaciones en línea: A–B 300 m, B–C 400 m, A–C 700 m, más una lejana D.
LINE = make_nbrs({("A", "B"): 300, ("B", "C"): 400, ("A", "C"): 700,
                  ("A", "D"): 2000, ("B", "D"): 1800, ("C", "D"): 1500})


# ---------------------------------------------------------------------------
# (a) conservación
# ---------------------------------------------------------------------------

def test_a_conservacion_minuto_a_minuto():
    init = make_init({"A": (3, 10), "B": (0, 5), "C": (5, 6), "D": (2, 8)})
    rng = np.random.default_rng(0)
    names = ["A", "B", "C", "D"]
    rows = [("A", "B", at(5, 0), at(5, 45)),          # salió antes de 05:30, llega dentro
            ("C", "A", at(0, 20), at(0, 50))]         # sale dentro, llega fuera (después de 00:30)
    for _ in range(200):
        o, d = rng.choice(names, 2)
        dep = T0 + timedelta(seconds=int(rng.integers(0, 19 * 3600 - 700)))
        rows.append((o, d, dep, dep + timedelta(seconds=int(rng.integers(60, 600)))))
    trips = make_trips(rows)
    orders = [K.Order(at(7, 0), at(7, 0), "B", 4), K.Order(at(8, 15), at(8, 15), "C", -3),
              K.Order(at(9, 0, 30), at(9, 0, 30), "A", 50), K.Order(at(10, 0), at(10, 0), "D", -50)]
    ev = make_events([("C", at(11, 0), "daño", 1), ("C", at(13, 0), "reparacion", 1),
                      ("A", at(14, 0), "daño", 2), ("A", at(15, 0), "taller_retiro", 1)])
    res = run(init, trips, LINE, orders=orders, damage_events=ev)

    # disponibles + dañadas en estaciones + en tránsito − bodega neta + taller
    # acumulado, calculado por fuera del motor
    minutes = res.extra["minutes"]
    oa = res.extra["orders_applied"]
    dl = res.extra["damage_applied"]
    totals = []
    for k, m in enumerate(minutes):
        in_st = int(res.extra["bikes_minute"][k].sum()) + int(res.extra["disabled_minute"][k].sum())
        started = (trips["t_dep"] <= m)
        arrived = (trips["t_arr"] <= m) & (trips["t_arr"] >= T0)
        transit = int((started & ~arrived).sum())
        wh = int(oa.loc[oa["effective_at"] <= m, "applied"].sum())
        taller = int(dl.loc[(dl["t"] <= m) & (dl["kind"] == "taller_retiro"), "applied"].sum())
        totals.append(in_st + transit - wh + taller)
    assert len(set(totals)) == 1, set(totals)
    assert totals[0] == 3 + 0 + 5 + 2 + 1            # stock inicial + el viaje en curso
    assert res.metrics["taller_aplicado"] == 1 and res.extra["disabled_minute"][-1].sum() == 1
    assert res.metrics["trips_served"] == 201          # todas las salidas en la ventana
    assert res.extra["dep_failed"] == 0 and res.extra["arr_failed"] == 0


# ---------------------------------------------------------------------------
# (b) (c) salidas en estación vacía
# ---------------------------------------------------------------------------

def test_b_salida_desviada_a_vecina_menor_500():
    init = make_init({"A": (0, 10), "B": (2, 10), "C": (4, 10), "D": (9, 10)})
    trips = make_trips([("A", "D", at(6, 0), at(6, 20))])
    res = run(init, trips, LINE)
    assert res.metrics["detours_dep"] == 1
    assert res.metrics["walk_m_dep"] == 300
    det = res.extra["detours"].iloc[0]
    assert (det["kind"], det["orig"], det["to"]) == ("dep", "A", "B")
    assert bikes_at(res, "B", at(6, 1)) == 1 and bikes_at(res, "A", at(6, 1)) == 0
    assert res.extra["detours_dep_far"] == 0
    assert res.metrics["trips_served"] == 1


def test_c_sin_vecina_en_500_va_a_la_mas_cercana_con_bici():
    # B (300 m) vacía; C (700 m) y D (2000 m) con bici → C
    init = make_init({"A": (0, 10), "B": (0, 10), "C": (1, 10), "D": (5, 10)})
    trips = make_trips([("A", "B", at(6, 0), at(6, 20))])
    res = run(init, trips, LINE)
    det = res.extra["detours"].iloc[0]
    assert (det["orig"], det["to"], det["dist_m"]) == ("A", "C", 700)
    assert res.extra["detours_dep_far"] == 1
    assert bikes_at(res, "C", at(6, 5)) == 0
    # la llegada del mismo viaje sigue a su destino original (B)
    assert bikes_at(res, "B", at(6, 20)) == 1


# ---------------------------------------------------------------------------
# (d) llegada a estación llena
# ---------------------------------------------------------------------------

def test_d_llegada_llena_desviada_sin_radio():
    # C llena; B (400 m) también llena; A (700 m) con anclaje → A, aunque está a >500 m
    init = make_init({"A": (2, 10), "B": (5, 5), "C": (3, 4, 1), "D": (1, 10)})
    trips = make_trips([("D", "C", at(7, 0), at(7, 30))])
    res = run(init, trips, LINE)
    assert res.metrics["detours_arr"] == 1
    det = res.extra["detours"].iloc[0]
    assert (det["kind"], det["orig"], det["to"], det["dist_m"]) == ("arr", "C", "A", 700)
    assert res.metrics["walk_m_arr"] == 700
    assert bikes_at(res, "A", at(7, 30)) == 3
    assert bikes_at(res, "C", at(7, 30)) == 3            # la dañada ocupa anclaje


# ---------------------------------------------------------------------------
# (e) órdenes en el minuto exacto
# ---------------------------------------------------------------------------

def test_e_orden_aplicada_en_effective_at_exacto():
    init = make_init({"A": (0, 10), "B": (5, 10), "C": (5, 10), "D": (5, 10)})
    # salida desde A justo a las 06:30:00: la orden del mismo instante va primero → sin desvío
    trips = make_trips([("A", "B", at(6, 30), at(6, 40))])
    orders = [K.Order(at(5, 30), at(6, 30), "A", 3),
              K.Order(at(5, 30), at(7, 0, 20), "C", -2)]    # a media minuto
    res = run(init, trips, LINE, orders=orders)
    assert bikes_at(res, "A", at(6, 29)) == 0
    assert bikes_at(res, "A", at(6, 30)) == 2                 # +3 y luego la salida
    assert res.metrics["detours_dep"] == 0
    assert bikes_at(res, "C", at(7, 0)) == 5                  # 07:00:20 aún no
    assert bikes_at(res, "C", at(7, 1)) == 3
    # E de A: vacía de 05:30 a 06:29 → 60 minutos
    sh = res.station_hour.set_index(["short_name", "hour"])
    assert sh.loc[("A", 5), "E"] == 30 and sh.loc[("A", 6), "E"] == 30
    assert res.metrics["E"] == 60


# ---------------------------------------------------------------------------
# (f) recortes
# ---------------------------------------------------------------------------

def test_f_orden_recortada_por_capacidad_y_por_falta_de_bicis():
    init = make_init({"A": (8, 10), "B": (1, 10), "C": (6, 6), "D": (5, 10)})
    orders = [K.Order(T0, at(6, 0), "A", 5),       # caben 2 → recorta 3
              K.Order(T0, at(6, 0), "B", -4),      # hay 1 → recorta 3
              K.Order(T0, at(6, 0), "C", 1)]       # llena → nada, no es movimiento
    res = run(init, make_trips([]), LINE, orders=orders)
    m = res.metrics
    assert (m["A"], m["R"], m["clipped"]) == (2, 1, 7)
    assert m["moves"] == 2 and m["stations_touched"] == 2
    assert m["rebalanced"] == 1 and m["warehouse"] == 1
    assert bikes_at(res, "A", at(6, 0)) == 10 and bikes_at(res, "B", at(6, 0)) == 0
    assert res.extra["orders_applied"]["applied"].tolist() == [2, -1, 0]


# ---------------------------------------------------------------------------
# (g) E y F minuto a minuto
# ---------------------------------------------------------------------------

def test_g_E_F_minuto_a_minuto_caso_conocido():
    # S: cap 2, 1 bici. B y C holgadas (nunca vacías ni llenas).
    init = make_init({"S": (1, 2), "B": (5, 10), "C": (5, 10)})
    nb = make_nbrs({("S", "B"): 200, ("S", "C"): 300, ("B", "C"): 250})
    trips = make_trips([
        ("S", "B", at(5, 35, 30), at(6, 0)),     # S vacía desde 05:35:30
        ("B", "S", at(5, 45), at(5, 50)),         # vuelve a 1 a las 05:50:00
        ("B", "S", at(6, 5), at(6, 10, 10)),      # S llena desde 06:10:10
        ("S", "B", at(6, 20), at(6, 30)),         # deja de estar llena a las 06:20:00
    ])
    res = run(init, trips, nb)
    sh = res.station_hour.set_index(["short_name", "hour"])
    # vacía en los minutos 05:36..05:49 → 14
    assert sh.loc[("S", 5), "E"] == 14
    # llena en los minutos 06:11..06:19 → 9
    assert sh.loc[("S", 6), "F"] == 9
    assert res.metrics["E"] == 14 and res.metrics["F"] == 9
    assert len(res.station_hour) == 3 * 20                     # 3 estaciones × horas 5..24
    assert sorted(res.station_hour["hour"].unique()) == list(K.STATION_HOURS)
    assert res.extra["bikes_minute"].shape == (1140, 3)


# ---------------------------------------------------------------------------
# (h) replay de EcobiciMoves
# ---------------------------------------------------------------------------

def moves_frame(rows):
    """rows: (short_name, t0, t1, delta, taller_retiro, undo) → EcobiciMoves."""
    mv = pd.DataFrame(rows, columns=["short_name", "t0", "t1", "delta", "taller_retiro", "undo"])
    mv["t0"] = pd.to_datetime(mv["t0"])
    mv["t1"] = pd.to_datetime(mv["t1"])
    mv["delta"] = mv["delta"].astype("int64")
    mv["taller_retiro"] = mv["taller_retiro"].astype("int64")
    mv["undo"] = mv["undo"].astype(bool)
    mv["delta_rebal"] = mv["delta"] + mv["taller_retiro"]
    mv["arrivals"] = 0
    mv["departures"] = 0
    mv["day"] = DAY
    return K.validate_ecobici_moves(mv)


@pytest.fixture
def moves_file(tmp_path):
    mv = moves_frame([
        ("A", at(6, 4, 12), at(6, 19, 5), 3, 0, False),
        ("B", at(6, 4, 12), at(6, 19, 5), -2, 1, False),     # retira 1 dañada y quita 1 disponible
        ("C", at(7, 49, 40), at(8, 5, 0), 4, 0, False),
        ("A", at(5, 20), at(5, 34), 1, 0, False),            # t0 antes de 05:30, t1 dentro
        ("D", at(9, 0), at(9, 15), 1, 0, True),              # par ±1 que se deshace
        ("D", at(9, 15), at(9, 30), -1, 0, True),
        ("C", at(10, 0), at(10, 15), -2, 2, False),          # solo taller: delta_rebal 0
        ("B", at(0, 20), at(0, 35), -1, 0, False),           # t0 dentro (00:20), t1 fuera
        ("A", datetime(2025, 9, 4, 6, 0), datetime(2025, 9, 4, 6, 15), 2, 0, False),  # otro día
    ])
    p = tmp_path / "ecobici_moves.parquet"
    mv.to_parquet(p)
    return p


def test_h_replay_ecobici_t0(moves_file):
    orders = sim.ecobici_orders(DAY, when="t0", path=moves_file)
    # principal = rebal sin ±1: el taller puro (C 10:00) y el par de D no son órdenes;
    # 05:20 queda fuera de [05:30, 00:30); 00:20 del día siguiente sí entra
    assert [(o.short_name, o.effective_at, o.delta) for o in orders] == [
        ("A", at(6, 4, 12), 3), ("B", at(6, 4, 12), -1), ("C", at(7, 49, 40), 4), ("B", at(0, 20), -1)]
    assert all(o.issued_at == o.effective_at for o in orders)

    init = make_init({"A": (1, 10), "B": (5, 10), "C": (0, 10), "D": (5, 10)})
    res = run(init, make_trips([]), LINE, orders=orders, arm="ecobici")
    assert bikes_at(res, "A", at(6, 4)) == 1 and bikes_at(res, "A", at(6, 5)) == 4
    assert bikes_at(res, "B", at(6, 5)) == 4 and bikes_at(res, "B", at(0, 20)) == 3
    assert bikes_at(res, "C", at(7, 49)) == 0 and bikes_at(res, "C", at(7, 50)) == 4
    m = res.metrics
    assert (m["moves"], m["A"], m["R"], m["warehouse"], m["stations_touched"]) == (4, 7, 2, 5, 3)
    assert res.arm == "ecobici"

    t1 = sim.ecobici_orders(DAY, when="t1", path=moves_file)
    # t1: 05:34 entra, 00:35 queda fuera
    assert [(o.short_name, o.effective_at) for o in t1] == [
        ("A", at(5, 34)), ("A", at(6, 19, 5)), ("B", at(6, 19, 5)), ("C", at(8, 5))]


def test_h_variant_stock_como_run1(moves_file):
    # stock: columna delta (con el taller dentro) y con ±1 por defecto, como el run 1
    so = sim.ecobici_orders(DAY, variant="stock", path=moves_file)
    assert [(o.short_name, o.delta) for o in so] == [
        ("A", 3), ("B", -2), ("C", 4), ("D", 1), ("D", -1), ("C", -2), ("B", -1)]
    sn = sim.ecobici_orders(DAY, variant="stock", undo=False, path=moves_file)
    assert [(o.short_name, o.delta) for o in sn] == [("A", 3), ("B", -2), ("C", 4), ("C", -2), ("B", -1)]


def test_h_variant_avail_lee_su_propio_archivo(tmp_path):
    """El archivo principal solo trae intervalos con delta de stock ≠ 0; uno
    con delta = 0 y delta_avail ≠ 0 (p. ej. Ecobici se lleva una dañada y
    deja una buena) solo está en el archivo _avail, con su delta en `delta`."""
    stock = moves_frame([("A", at(6, 0), at(6, 15), 3, 0, False)])
    stock["delta_avail"] = np.array([2], dtype="int64")
    avail = moves_frame([("A", at(6, 0), at(6, 15), 2, 0, False),
                         ("B", at(7, 0), at(7, 15), 1, 0, False)])
    ps, pa = tmp_path / "ecobici_moves.parquet", tmp_path / "ecobici_moves_avail.parquet"
    stock.to_parquet(ps)
    avail.to_parquet(pa)
    so = sim.ecobici_orders(DAY, variant="stock", path=ps)
    av = sim.ecobici_orders(DAY, variant="avail", path=pa)
    assert [(o.short_name, o.delta) for o in so] == [("A", 3)]
    assert [(o.short_name, o.delta) for o in av] == [("A", 2), ("B", 1)]    # B solo existe en avail
    # por defecto cada variante lee su archivo
    assert sim.MOVES_FILES["rebal"].name == "ecobici_moves.parquet"
    assert sim.MOVES_FILES["stock"].name == "ecobici_moves.parquet"
    assert sim.MOVES_FILES["avail"].name == "ecobici_moves_avail.parquet"
    with pytest.raises(ValueError):
        sim.ecobici_orders(DAY, variant="delta_avail", path=ps)


def test_stock_y_avail_van_siempre_con_danadas_fijas():
    assert sim.REPLAY_DAMAGE == {"rebal": "auto", "stock": "fixed", "avail": "fixed"}
    assert sim.resolve_damage("baseline", None) == "auto"
    assert sim.resolve_damage("ecobici", None) == "auto"
    assert sim.resolve_damage("ecobici", "fixed") == "fixed"
    for arm in ("ecobici_stock", "ecobici_avail"):
        assert sim.resolve_damage(arm, None) == "fixed"
        assert sim.resolve_damage(arm, "fixed") == "fixed"
        with pytest.raises(ValueError, match="dos veces"):
            sim.resolve_damage(arm, "auto")
    with pytest.raises(SystemExit):
        sim.main(["--day", DAY, "--arm", "ecobici_avail", "--damage", "auto"])


def test_ordenes_como_dataframe_equivalen_a_lista(moves_file):
    orders = sim.ecobici_orders(DAY, path=moves_file)
    init = make_init({"A": (1, 10), "B": (5, 10), "C": (0, 10), "D": (5, 10)})
    r1 = run(init, make_trips([]), LINE, orders=orders)
    r2 = run(init, make_trips([]), LINE, orders=K.orders_to_frame(orders))
    assert r1.metrics == r2.metrics


# ---------------------------------------------------------------------------
# (o) replay rebal sin ±1 contra undo=True
# ---------------------------------------------------------------------------

def test_o_replay_rebal_sin_undo_contra_con_undo(moves_file):
    sin = sim.ecobici_orders(DAY, path=moves_file)
    con = sim.ecobici_orders(DAY, undo=True, path=moves_file)
    extra = set(con) - set(sin)
    assert sorted((o.short_name, o.effective_at, o.delta) for o in extra) == [
        ("D", at(9, 0), 1), ("D", at(9, 15), -1)]
    assert len(con) == len(sin) + 2
    init = make_init({"A": (1, 10), "B": (5, 10), "C": (0, 10), "D": (5, 10)})
    r_sin = run(init, make_trips([]), LINE, orders=sin)
    r_con = run(init, make_trips([]), LINE, orders=con)
    assert (r_con.metrics["moves"], r_sin.metrics["moves"]) == (6, 4)
    assert r_con.metrics["A"] == r_sin.metrics["A"] + 1 and r_con.metrics["R"] == r_sin.metrics["R"] + 1
    assert r_con.metrics["warehouse"] == r_sin.metrics["warehouse"]    # el par se deshace
    assert bikes_at(r_con, "D", at(9, 5)) == 6 and bikes_at(r_con, "D", at(9, 20)) == 5
    assert bikes_at(r_sin, "D", at(9, 5)) == 5


# ---------------------------------------------------------------------------
# política, ventana y estaciones desconocidas
# ---------------------------------------------------------------------------

class Recorder:
    """Política de prueba: a las 06:00 pide +2 en A y a las 23:30 +1 en A;
    registra lo que ve."""

    def __init__(self):
        self.calls = []

    def decide(self, t, state, pending, forecast, params):
        self.calls.append((t, state.stations.copy(), list(pending), state.warehouse, forecast))
        if t in (at(6, 0), at(23, 30)):
            return [K.Order(t, t + timedelta(minutes=params.lead_min), "A", 2 if t == at(6, 0) else 1)]
        return []


def test_politica_cada_15_min_hasta_t_mas_L_antes_de_0030():
    init = make_init({"A": (0, 10, 1), "B": (5, 10), "C": (5, 10), "D": (5, 10)})
    pol = Recorder()
    res = run(init, make_trips([]), LINE, policy=pol, lead_min=45, forecast="FC",
              params=K.PolicyParams(lead_min=45))
    times = [c[0] for c in pol.calls]
    L = timedelta(minutes=45)
    assert times == [t for t in C.decision_times(DAY) if t + L < T_END]
    assert times[-1] == at(23, 30) and len(times) == 73           # 23:30 + 45 = 00:15 < 00:30
    assert res.extra["decision_times"] == times
    with_60 = run(init, make_trips([]), LINE, policy=Recorder())
    assert with_60.extra["decision_times"][-1] == at(23, 15) and len(with_60.extra["decision_times"]) == 72
    st = pol.calls[0][1]
    K.validate_state(st)
    a = st.set_index("short_name").loc["A"]
    assert (a["bikes"], a["disabled"], a["docks"], a["cap"], a["out_of_service"]) == (0, 1, 9, 10, False)
    assert pol.calls[0][4] == "FC"
    # pendiente visible hasta que se aplica a las 06:45
    by_t = {c[0]: c for c in pol.calls}
    assert len(by_t[at(6, 15)][2]) == 1 and len(by_t[at(6, 30)][2]) == 1
    assert by_t[at(6, 45)][2] == []
    # la política ve la orden ya aplicada a las 06:45 y la bodega acumulada
    assert by_t[at(6, 45)][1].set_index("short_name").loc["A", "bikes"] == 2
    assert by_t[at(6, 45)][3] == 2
    assert bikes_at(res, "A", at(6, 44)) == 0 and bikes_at(res, "A", at(6, 45)) == 2
    # la orden de las 23:30 se aplica a las 00:15 del día siguiente
    assert bikes_at(res, "A", at(0, 14)) == 2 and bikes_at(res, "A", at(0, 15)) == 3
    assert res.metrics["A"] == 3 and res.extra["orders_outside_window"] == 0
    assert res.arm == "policy"


# ---------------------------------------------------------------------------
# (i) viaje que cruza medianoche
# ---------------------------------------------------------------------------

def test_i_viaje_que_cruza_medianoche():
    # A→B sale 23:50 y llega 00:10 del día siguiente; B queda llena de 00:10 a 00:30
    init = make_init({"A": (1, 10), "B": (4, 5), "C": (5, 10), "D": (5, 10)})
    trips = make_trips([("A", "B", at(23, 50), at(0, 10))])
    res = run(init, trips, LINE)
    assert bikes_at(res, "A", at(23, 50)) == 0 and bikes_at(res, "B", at(0, 9)) == 4
    assert bikes_at(res, "B", at(0, 10)) == 5
    sh = res.station_hour.set_index(["short_name", "hour"])
    assert sh.loc[("A", 23), "E"] == 10 and sh.loc[("A", 24), "E"] == 30
    assert sh.loc[("B", 24), "F"] == 20 and sh.loc[("B", 23), "F"] == 0
    assert res.metrics["trips_served"] == 1 and res.extra["n_arrivals"] == 1
    assert res.extra["transit_end"] == 0


def test_viajes_en_los_bordes_de_la_ventana():
    init = make_init({"A": (2, 10), "B": (2, 10), "C": (2, 10), "D": (2, 10)})
    trips = make_trips([
        ("A", "B", at(5, 10), at(5, 40)),     # antes de 05:30 → solo entrega la bici
        ("C", "D", at(0, 10), at(0, 40)),     # llega después de 00:30 → solo cuenta la salida
        ("D", "C", at(0, 30), at(0, 45)),     # sale a las 00:30 → fuera de la ventana
    ])
    res = run(init, trips, LINE)
    assert bikes_at(res, "A", at(5, 45)) == 2 and bikes_at(res, "B", at(5, 40)) == 3
    assert bikes_at(res, "C", at(0, 29)) == 1 and bikes_at(res, "D", at(0, 29)) == 2
    assert res.metrics["trips_served"] == 1
    assert res.extra["transit_start"] == 1 and res.extra["transit_end"] == 1
    assert res.extra["n_arrivals_after_end"] == 1


# ---------------------------------------------------------------------------
# (j) (k) (l) dañadas dinámicas
# ---------------------------------------------------------------------------

def test_j_dano_convierte_disponible_en_danada_y_vacia_la_estacion():
    # A tiene 1 bici; a las 08:00 se daña. Una salida de A en el mismo instante
    # ya la encuentra dañada (dañadas antes que todo) y se desvía a B.
    init = make_init({"A": (1, 10), "B": (5, 10), "C": (5, 10), "D": (5, 10)})
    trips = make_trips([("A", "C", at(8, 0), at(8, 20))])
    ev = make_events([("A", at(8, 0), "daño", 1)])
    res = run(init, trips, LINE, damage_events=ev)
    assert bikes_at(res, "A", at(7, 59)) == 1 and disabled_at(res, "A", at(7, 59)) == 0
    assert bikes_at(res, "A", at(8, 0)) == 0 and disabled_at(res, "A", at(8, 0)) == 1
    det = res.extra["detours"].iloc[0]
    assert (det["kind"], det["orig"], det["to"]) == ("dep", "A", "B")
    # A vacía desde 08:00 hasta 00:30: 16.5 h
    sh = res.station_hour.groupby("short_name")[["E", "F"]].sum()
    assert sh.loc["A", "E"] == 990 and sh.loc["A", "F"] == 0
    assert res.metrics["E"] == 990
    assert (res.metrics["danos_aplicados"], res.metrics["danos_no_aplicables"]) == (1, 0)
    # sin eventos (dañadas fijas) la salida se lleva la bici de A, sin desvío
    fixed = run(init, trips, LINE)
    assert fixed.metrics["detours_dep"] == 0 and fixed.metrics["danos_aplicados"] == 0
    assert bikes_at(fixed, "A", at(8, 0)) == 0 and disabled_at(fixed, "A", at(8, 0)) == 0


def test_k_taller_retiro_quita_una_danada():
    # A llena: 7 disponibles + 3 dañadas en cap 10. A las 09:00 el taller se lleva 2.
    init = make_init({"A": (7, 10, 3), "B": (5, 10), "C": (5, 10), "D": (5, 10)})
    ev = make_events([("A", at(9, 0), "taller_retiro", 2)])
    res = run(init, make_trips([]), LINE, damage_events=ev)
    assert disabled_at(res, "A", at(8, 59)) == 3 and disabled_at(res, "A", at(9, 0)) == 1
    assert bikes_at(res, "A", at(9, 0)) == 7                  # las disponibles no cambian
    sh = res.station_hour.groupby("short_name")[["E", "F"]].sum()
    assert sh.loc["A", "F"] == 210                             # llena de 05:30 a 09:00
    assert (res.metrics["taller_aplicado"], res.metrics["taller_no_aplicable"]) == (2, 0)
    # la política ve las dañadas de t, no las de 05:30
    pol = Recorder()
    run(init, make_trips([]), LINE, policy=pol, damage_events=ev)
    st = {c[0]: c[1].set_index("short_name") for c in pol.calls}
    assert st[at(8, 45)].loc["A", "disabled"] == 3 and st[at(9, 0)].loc["A", "disabled"] == 1
    assert st[at(9, 0)].loc["A", "docks"] == 2


def test_l_eventos_no_aplicables_se_cuentan():
    init = make_init({"A": (1, 10), "B": (0, 10, 2), "C": (5, 10), "D": (5, 10)})
    ev = make_events([
        ("A", at(6, 0), "daño", 3),            # solo hay 1 disponible → 1 aplicado, 2 no
        ("C", at(6, 0), "taller_retiro", 2),   # C no tiene dañadas → 2 no aplicables
        ("B", at(7, 0), "reparacion", 1),      # B tiene 2 dañadas → 1 reparada
        ("D", at(7, 0), "reparacion", 1),      # D no tiene dañadas → no aplicable
        ("Z", at(8, 0), "daño", 1),            # estación desconocida → no aplicable
        ("A", datetime(2025, 9, 4, 6, 0), "daño", 1),   # fuera de la ventana: se ignora
    ])
    res = run(init, make_trips([]), LINE, damage_events=ev)
    m, x = res.metrics, res.extra
    assert (m["danos_aplicados"], m["danos_no_aplicables"]) == (1, 3)
    assert (m["taller_aplicado"], m["taller_no_aplicable"]) == (0, 2)
    assert (x["reparaciones_aplicadas"], x["reparaciones_no_aplicables"]) == (1, 1)
    assert x["damage_outside_window"] == 1
    assert bikes_at(res, "A", at(6, 0)) == 0 and disabled_at(res, "A", at(6, 0)) == 1
    assert bikes_at(res, "B", at(7, 0)) == 1 and disabled_at(res, "B", at(7, 0)) == 1
    dl = x["damage_applied"]
    assert dl[["short_name", "kind", "n", "applied"]].values.tolist() == [
        ["A", "daño", 3, 1], ["C", "taller_retiro", 2, 0],
        ["B", "reparacion", 1, 1], ["D", "reparacion", 1, 0], ["Z", "daño", 1, 0]]


def test_load_damage_events_auto_fixed_y_filtro(tmp_path):
    ev = make_events([("A", at(6, 0), "daño", 1), ("A", datetime(2025, 9, 4, 6, 0), "daño", 1)])
    p = tmp_path / "damage_events.parquet"
    ev.to_parquet(p)
    got, src = sim.load_damage_events(DAY, "auto", path=p)
    assert len(got) == 1 and got["t"].iloc[0] == pd.Timestamp(at(6, 0)) and src == p.name
    assert sim.load_damage_events(DAY, "fixed") == (None, "fixed")
    assert sim.load_damage_events(DAY, None) == (None, "fixed")
    with pytest.warns(UserWarning):
        assert sim.load_damage_events(DAY, "auto", path=tmp_path / "no.parquet") == (None, "fixed (sin archivo)")
    got, src = sim.load_damage_events(DAY, ev)
    assert len(got) == 1 and src == "DataFrame"
    with pytest.raises(ValueError):
        sim.load_damage_events(DAY, "otro")
    assert sim.DAMAGE_EVENTS_FILE.name == "damage_events.parquet"


# ---------------------------------------------------------------------------
# (m) bodega finita sobre lo aplicado
# ---------------------------------------------------------------------------

class Script:
    """Política de prueba: devuelve, en cada t, las órdenes (estación, delta) del guion."""

    def __init__(self, plan):
        self.plan = plan

    def decide(self, t, state, pending, forecast, params):
        eff = t + timedelta(minutes=params.lead_min)
        return [K.Order(t, eff, s, d) for s, d in self.plan.get(t, [])]


def test_m_bodega_aplicada_nunca_pasa_el_tope():
    # tope 2. 06:00 (efectiva 07:00): +5 en A y −5 en B, que solo tiene 2.
    # Pedido neto 0, pero aplicado neto +5 − 2 = +3 > 2 → la puesta se recorta a 4.
    # (el caso que rompió el run 1: la bodega se calculaba sobre lo pedido)
    # 07:00 (08:00): +3 en C → no cabe nada (bodega ya en +2).
    # 08:00 (09:00): −10 en D (tiene 8) → físico −8, bodega 2 − 8 = −6 < −2 → −4.
    init = make_init({"A": (0, 10), "B": (2, 10), "C": (0, 10), "D": (8, 10)})
    pol = Script({at(6, 0): [("A", 5), ("B", -5)], at(7, 0): [("C", 3)], at(8, 0): [("D", -10)]})
    params = K.PolicyParams(lead_min=60, tope_bodega=2)
    res = run(init, make_trips([]), LINE, policy=pol, params=params)
    oa = res.extra["orders_applied"]
    assert oa["applied"].tolist() == [4, -2, 0, -4]
    assert oa["recorte_bodega"].tolist() == [1, 0, 3, 4]
    assert oa["recorte_fisico"].tolist() == [0, 3, 0, 2]
    m = res.metrics
    assert (m["A"], m["R"], m["warehouse"]) == (4, 6, -2)
    assert m["recorte_bodega"] == 8 and m["clipped"] == 5
    # la bodega acumulada aplicada nunca sale de [−2, 2], en ningún instante
    cum = oa.groupby("effective_at")["applied"].sum().cumsum()
    assert cum.tolist() == [2, 2, -2]
    assert cum.abs().max() <= 2
    assert (res.extra["bodega_min"], res.extra["bodega_max"]) == (-2, 2)
    assert bikes_at(res, "A", at(7, 0)) == 4 and bikes_at(res, "B", at(7, 0)) == 0
    assert bikes_at(res, "D", at(9, 0)) == 4
    # identidad: pedido − aplicado = recortes (movimiento + fuera de servicio + físico + bodega)
    cuts = oa[["recorte_por_movimiento", "recorte_fuera_de_servicio", "recorte_fisico", "recorte_bodega"]]
    assert (oa["delta"].abs() - oa["applied"].abs() == cuts.sum(axis=1)).all()
    # el replay de Ecobici no tiene tope de bodega
    rep = run(init, make_trips([]), LINE,
              orders=[K.Order(at(7, 0), at(7, 0), "A", 10), K.Order(at(7, 0), at(7, 0), "C", 10)])
    assert rep.metrics["warehouse"] == 20 and rep.metrics["recorte_bodega"] == 0


def test_m_recorte_de_bodega_no_depende_del_orden_del_lote():
    # tope 4; puestas aplicadas +6 (A), +2 (B), +4 (C) → neto +12, sobran 8.
    # Reparto proporcional a lo aplicado: 8·6/12 = 4, 8·2/12 = 1.33, 8·4/12 = 2.67
    # → pisos 4, 1, 2 y la bici que falta va al resto mayor (C) → recortes 4, 1, 3.
    init = make_init({"A": (0, 10), "B": (0, 10), "C": (0, 10), "D": (5, 10)})
    plan = [("A", 6), ("B", 2), ("C", 4)]
    by_order = []
    for perm in (plan, plan[::-1], [plan[1], plan[2], plan[0]]):
        res = run(init, make_trips([]), LINE, policy=Script({at(6, 0): perm}),
                  params=K.PolicyParams(tope_bodega=4))
        oa = res.extra["orders_applied"].set_index("short_name")
        by_order.append((oa["applied"].to_dict(), oa["recorte_bodega"].to_dict()))
        assert res.metrics["warehouse"] == 4 and res.metrics["recorte_bodega"] == 8
    assert all(x == by_order[0] for x in by_order)
    assert by_order[0] == ({"A": 2, "B": 1, "C": 1}, {"A": 4, "B": 1, "C": 3})
    # del lado de los retiros: tope 1, retiros −4 (A) y −2 (B) → neto −6, faltan 5
    init2 = make_init({"A": (8, 10), "B": (8, 10), "C": (5, 10), "D": (5, 10)})
    for perm in ([("A", -4), ("B", -2)], [("B", -2), ("A", -4)]):
        res = run(init2, make_trips([]), LINE, policy=Script({at(6, 0): perm}),
                  params=K.PolicyParams(tope_bodega=1))
        oa = res.extra["orders_applied"].set_index("short_name")
        assert oa["applied"].to_dict() == {"A": -1, "B": 0}      # recortes 3.33 → 3, 1.67 → 2
        assert res.metrics["warehouse"] == -1


def test_spread_reparte_proporcional_sin_pasarse():
    assert sim._spread(8, [6, 2, 4]) == [4, 1, 3]
    assert sim._spread(0, [3, 3]) == [0, 0]
    assert sim._spread(1, [3, 3]) == [1, 0]              # empate: índice menor
    assert sim._spread(5, [5, 0]) == [5, 0]
    rng = np.random.default_rng(1)
    for _ in range(200):
        caps = rng.integers(0, 10, size=5).tolist()
        tot = int(rng.integers(0, sum(caps) + 1))
        out = sim._spread(tot, caps)
        assert sum(out) == tot and all(0 <= o <= c for o, c in zip(out, caps))
    with pytest.raises(AssertionError):
        sim._spread(7, [3, 3])


# ---------------------------------------------------------------------------
# (n) tope de bicis por movimiento
# ---------------------------------------------------------------------------

def test_n_recorte_por_tope_de_bicis_por_movimiento():
    init = make_init({"A": (0, 20), "B": (10, 20), "C": (5, 10), "D": (5, 10)})
    pol = Script({at(6, 0): [("A", 8), ("B", -7), ("C", 3)]})
    params = K.PolicyParams(max_bikes_per_move=5, tope_bodega=100)
    res = run(init, make_trips([]), LINE, policy=pol, params=params)
    oa = res.extra["orders_applied"]
    assert oa["applied"].tolist() == [5, -5, 3]
    assert oa["recorte_por_movimiento"].tolist() == [3, 2, 0]
    assert res.metrics["recorte_por_movimiento"] == 5 and res.metrics["clipped"] == 0
    assert bikes_at(res, "A", at(7, 0)) == 5 and bikes_at(res, "B", at(7, 0)) == 5
    assert res.extra["max_bikes_per_move"] == 5
    # por defecto el tope es C.MAX_BIKES_PER_MOVE = 22
    big = run(init, make_trips([]), LINE, policy=Script({at(6, 0): [("A", 30)]}),
              params=K.PolicyParams(tope_bodega=100))
    assert big.extra["orders_applied"]["applied"].tolist() == [20]      # 22, y luego el físico (cap 20)
    assert big.metrics["recorte_por_movimiento"] == 8 and big.metrics["clipped"] == 2
    # el replay de Ecobici no tiene tope por movimiento
    rep = run(init, make_trips([]), LINE, orders=[K.Order(at(7, 0), at(7, 0), "A", 15)])
    assert rep.metrics["A"] == 15 and rep.metrics["recorte_por_movimiento"] == 0


def test_orden_de_politica_a_estacion_fuera_de_servicio_se_recorta():
    init = make_init({"A": (0, 10), "B": (5, 10), "C": (0, 15), "D": (5, 10)})
    init.loc[init["short_name"] == "C", "out_of_service"] = True
    pol = Script({at(6, 0): [("C", 4), ("A", 2)]})
    res = run(init, make_trips([]), LINE, policy=pol)
    oa = res.extra["orders_applied"]
    assert oa["applied"].tolist() == [0, 2]
    assert oa["recorte_fuera_de_servicio"].tolist() == [4, 0]
    assert res.extra["recorte_fuera_de_servicio"] == 4 and res.metrics["clipped"] == 0
    assert res.metrics["moves"] == 1
    assert res.extra["E_out_of_service"] == 1140


def test_politica_con_effective_at_equivocado_falla():

    class Bad:
        def decide(self, t, state, pending, forecast, params):
            return [K.Order(t, t + timedelta(minutes=30), "A", 1)]
    init = make_init({"A": (0, 10), "B": (5, 10), "C": (5, 10), "D": (5, 10)})
    with pytest.raises(ValueError):
        run(init, make_trips([]), LINE, policy=Bad(), lead_min=60)


def test_orders_y_policy_juntos_falla():
    init = make_init({"A": (0, 10), "B": (5, 10), "C": (5, 10), "D": (5, 10)})
    with pytest.raises(ValueError):
        run(init, make_trips([]), LINE, orders=[], policy=Recorder())


def test_estaciones_desconocidas():
    init = make_init({"A": (2, 10), "B": (2, 10), "C": (2, 10), "D": (2, 10)})
    trips = make_trips([
        ("A", "1000", at(6, 0), at(6, 30)),   # llega a desconocida → sale del sistema
        ("1000", "B", at(7, 0), at(7, 20)),   # sale de desconocida → se ignora la salida
    ])
    trips["o_known"] = trips["o"] != "1000"
    trips["d_known"] = trips["d"] != "1000"
    res = run(init, trips, LINE)
    assert res.metrics["arrivals_unknown"] == 1
    assert res.metrics["trips_served"] == 1
    assert res.extra["n_departures_unknown"] == 1
    assert bikes_at(res, "A", at(6, 30)) == 1 and bikes_at(res, "B", at(7, 20)) == 3


def test_guarda_estacion_sin_coordenadas():
    # X no aparece en los vecinos (coordenadas inválidas): vacía y llena a la vez (cap 0)
    init = make_init({"A": (0, 10), "B": (2, 10), "C": (3, 3), "D": (4, 10), "X": (0, 0)})
    trips = make_trips([("X", "C", at(6, 0), at(6, 10)),     # salida: primera válida con bici = B
                        ("A", "X", at(7, 0), at(7, 10))])    # llegada: primera válida con anclaje = A
    # (la llegada a C llena va a B, a 400 m; la salida de A vacía va a B, a 300 m)
    res = run(init, trips, LINE)
    det = res.extra["detours"]
    assert det[["kind", "orig", "to"]].values.tolist() == [
        ["dep", "X", "B"], ["arr", "C", "B"], ["dep", "A", "B"], ["arr", "X", "A"]]
    assert res.extra["detours_no_coords"] == 2
    assert det.loc[det["orig"] == "X", "dist_m"].isna().all()
    assert res.metrics["walk_m_dep"] == 300 and res.metrics["walk_m_arr"] == 400   # NaN no suma
    assert res.metrics["trips_served"] == 2 and res.extra["dep_failed"] == 0


def test_guarda_viaje_de_duracion_cero():
    # F llena: el viaje F→F de duración 0 primero saca la bici y luego la devuelve;
    # si la llegada fuera primero se desviaría a otra estación.
    init = make_init({"A": (2, 10), "B": (2, 10), "C": (2, 10), "F": (2, 2)})
    nb = make_nbrs({("F", "A"): 100, ("F", "B"): 200, ("F", "C"): 300,
                    ("A", "B"): 150, ("A", "C"): 250, ("B", "C"): 120})
    trips = make_trips([("F", "F", at(6, 0), at(6, 0))])
    res = run(init, trips, nb)
    assert res.metrics["detours_arr"] == 0 and res.metrics["detours_dep"] == 0
    assert bikes_at(res, "F", at(6, 0)) == 2 and bikes_at(res, "A", at(6, 0)) == 2
    bad = make_trips([("A", "B", at(6, 10), at(6, 0))])
    with pytest.raises(ValueError):
        run(init, bad, nb)


def test_record_at_guarda_bicis_en_instantes_dados():
    init = make_init({"A": (2, 10), "B": (2, 10), "C": (2, 10), "D": (2, 10)})
    trips = make_trips([("A", "B", at(6, 0, 30), at(6, 10, 30))])
    res = run(init, trips, LINE, record_at=[at(6, 0, 29), at(6, 0, 30), at(6, 10, 30)])
    names = list(res.extra["stations"])
    rec = res.extra["bikes_record"]
    assert rec[0, names.index("A")] == 2
    assert rec[1, names.index("A")] == 1
    assert rec[2, names.index("B")] == 3


# ---------------------------------------------------------------------------
# día real (solo si están los datos)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not C.TRIPS_PARQUET.exists() or not (C.SNAPSHOT_DIR / f"{DAY}.parquet").exists(),
                    reason="sin datos locales")
def test_dia_real_baseline():
    res = sim.run_day(DAY, damage_events="fixed")
    K.validate_day_result(res)
    m = res.metrics
    n = len(res.extra["stations"])
    assert m["moves"] == 0 and m["A"] == 0 and m["R"] == 0
    assert res.extra["dep_failed"] == 0 and res.extra["arr_failed"] == 0
    assert 0 < m["E"] < n * 1140 and 0 < m["F"] < n * 1140
    assert res.extra["bikes_minute"].shape == (1140, n)
    assert set(res.station_hour["hour"]) == set(K.STATION_HOURS)
    assert res.extra["n_arrivals_after_end"] > 0          # viajes que llegan después de 00:30
    assert res.extra["damage_source"] == "fixed"
