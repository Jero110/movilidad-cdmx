"""Tests de ecosim/medicion (regla 1.3 + descomposición de dañadas del run 2)
con fixtures a mano."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ecosim import config as C
from ecosim import contracts as K
from ecosim import medicion as M

DAY = "2025-09-03"
NEXT = "2025-09-04"
T = lambda hm: pd.Timestamp(f"{DAY} {hm}")  # noqa: E731
N = lambda hm: pd.Timestamp(f"{NEXT} {hm}")  # noqa: E731   (después de medianoche)


def _snaps(rows, station="001"):
    """rows: (t, bikes, disabled[, docks, blank]); t es 'hh:mm' (día DAY) o Timestamp."""
    out = []
    for r in rows:
        hm, b, dis = r[:3]
        docks = r[3] if len(r) > 3 else 20 - b - dis
        blank = r[4] if len(r) > 4 else False
        out.append({"short_name": station, "t": hm if isinstance(hm, pd.Timestamp) else T(hm),
                    "bikes": b, "disabled": dis, "docks": docks, "blank": blank})
    return pd.DataFrame(out)


def _trips(rows):
    """rows: (o, d, t_dep, t_arr), tiempos 'hh:mm' (día DAY) o Timestamp."""
    ts = lambda x: x if isinstance(x, pd.Timestamp) else T(x)  # noqa: E731
    return pd.DataFrame([{"bike": str(i), "o": o, "d": d, "t_dep": ts(a), "t_arr": ts(b)}
                         for i, (o, d, a, b) in enumerate(rows)],
                        columns=["bike", "o", "d", "t_dep", "t_arr"])


def _row(iv, k=0):
    return iv.iloc[k]


# ----------------------------------------------------------------------
# (a) casos del run 1
# ----------------------------------------------------------------------

def test_a_departures_without_intervention_give_zero_and_arrival_plus_intervention():
    # 05:30 stock 5 → 05:45 stock 3 con 2 salidas ⇒ delta 0
    # 05:45 stock 3 → 06:00 stock 7 con 1 llegada ⇒ delta +3
    snaps = _snaps([("05:30", 5, 0), ("05:45", 3, 0), ("06:00", 7, 0)])
    trips = _trips([
        ("001", "999", "05:35", "05:50"),
        ("001", "999", "05:40", "05:55"),
        ("999", "001", "05:40", "05:50"),   # llegada en (05:45, 06:00]
    ])
    iv = M.intervals(snaps, trips, DAY)
    assert list(iv["t0"]) == [T("05:30"), T("05:45")]
    assert list(iv["departures"]) == [2, 0]
    assert list(iv["arrivals"]) == [0, 1]
    assert list(iv["delta"]) == [0, 3]
    assert list(iv["delta_rebal"]) == [0, 3] and list(iv["taller_retiro"]) == [0, 0]
    mv = M.moves_from_intervals(iv)
    K.validate_ecobici_moves(mv)
    assert len(mv) == 1 and mv.iloc[0]["delta"] == 3 and mv.iloc[0]["t0"] == T("05:45")
    s = M.day_summary(iv, mv, DAY)
    assert (s["moves"], s["stations_touched"], s["A"], s["R"], s["rebalanced"], s["warehouse"]) == (1, 1, 3, 0, 0, 3)
    assert len(M.damage_events(iv)) == 0


def test_a_brief_example_5_to_9_with_one_arrival():
    snaps = _snaps([("05:30", 5, 0), ("05:45", 9, 0)])
    iv = M.intervals(snaps, _trips([("999", "001", "05:31", "05:40")]), DAY)
    assert iv["delta"].tolist() == [3]


def test_a_plus_minus_one_undo_is_marked_and_excluded_only_from_main():
    snaps = _snaps([("05:30", 5, 0), ("05:45", 6, 0), ("06:00", 5, 0), ("06:15", 8, 0)])
    iv = M.intervals(snaps, _trips([]), DAY)
    assert iv["delta"].tolist() == [1, -1, 3]
    assert iv["undo"].tolist() == [True, True, False]
    mv = M.moves_from_intervals(iv)
    assert len(mv) == 3                            # el parquet trae todo delta ≠ 0
    assert mv["undo"].tolist() == [True, True, False]
    assert M.main_moves(mv)["delta"].tolist() == [3]
    s = M.day_summary(iv, mv, DAY)
    assert (s["moves"], s["A"], s["R"], s["warehouse"]) == (1, 3, 0, 3)            # principal sin ±1
    assert (s["undo_moves"], s["undo_A"], s["undo_R"], s["undo_warehouse"]) == (3, 4, 1, 3)   # sensibilidad
    assert s["undo_pairs"] == 1 and s["undo_rows"] == 2
    assert s["undo_pct"] == pytest.approx(100 * 2 / 3, abs=0.01)


def test_a_non_undo_opposite_bigger_delta():
    # +1 seguido de −2 no es un ±1 que se deshace
    snaps = _snaps([("05:30", 5, 0), ("05:45", 6, 0), ("06:00", 4, 0)])
    iv = M.intervals(snaps, _trips([]), DAY)
    assert iv["delta"].tolist() == [1, -2]
    assert not iv["undo"].any()


def test_a_odd_undo_chain_pairs_without_overlap():
    # +1, −1, +1: un solo par (1-2) que suma 0; el tercer +1 es un movimiento normal
    snaps = _snaps([("05:30", 5, 0), ("05:45", 6, 0), ("06:00", 5, 0), ("06:15", 6, 0)])
    iv = M.intervals(snaps, _trips([]), DAY)
    assert iv["delta"].tolist() == [1, -1, 1]
    assert iv["undo_start"].tolist() == [True, False, False]
    assert iv["undo"].tolist() == [True, True, False]
    assert iv.loc[iv["undo"], "delta"].sum() == 0
    mv = M.moves_from_intervals(iv)
    assert M.main_moves(mv)["delta"].tolist() == [1]
    s = M.day_summary(iv, mv, DAY)
    assert s["undo_pairs"] == 1 and (s["moves"], s["warehouse"]) == (1, 1)
    assert s["undo_warehouse"] == s["warehouse"]              # quitar ±1 no cambia la bodega


def test_a_even_undo_chain_is_two_pairs():
    # +1, −1, +1, −1: pares 1-2 y 3-4; todo excluido, suma 0
    snaps = _snaps([("05:30", 5, 0), ("05:45", 6, 0), ("06:00", 5, 0), ("06:15", 6, 0), ("06:30", 5, 0)])
    iv = M.intervals(snaps, _trips([]), DAY)
    assert iv["undo_start"].tolist() == [True, False, True, False]
    assert iv["undo"].all() and iv["delta"].sum() == 0


def test_a_undo_chain_does_not_cross_stations():
    s1 = _snaps([("05:30", 5, 0), ("05:45", 6, 0)], "001")          # +1 al final de 001
    s2 = _snaps([("05:30", 5, 0), ("05:45", 4, 0), ("06:00", 5, 0)], "002")   # −1, +1 en 002
    iv = M.intervals(pd.concat([s1, s2]), _trips([]), DAY)
    assert iv["undo"].tolist() == [False, True, True]


# ----------------------------------------------------------------------
# (b)–(e) descomposición de dañadas
# ----------------------------------------------------------------------

def test_b_bike_marked_damaged_is_dano_event_and_zero_rebal():
    snaps = _snaps([("05:30", 5, 0), ("05:45", 4, 1)])
    iv = M.intervals(snaps, _trips([]), DAY)
    r = _row(iv)
    assert (r["delta"], r["delta_rebal"], r["taller_retiro"], r["dano"], r["reparacion"]) == (0, 0, 0, 1, 0)
    assert r["delta_avail"] == -1                   # la sensibilidad solo-disponibles sí lo ve
    assert len(M.moves_from_intervals(iv)) == 0
    assert len(M.moves_from_intervals(iv, "delta_avail")) == 1
    ev = M.damage_events(iv)
    assert ev[["short_name", "t", "kind", "n"]].values.tolist() == [["001", T("05:45"), "daño", 1]]


def test_b_bike_arriving_damaged_is_dano():
    # llega una bici y queda dañada: stock +1 = flujo ⇒ delta 0; daño 1
    snaps = _snaps([("05:30", 5, 0), ("05:45", 5, 1)])
    iv = M.intervals(snaps, _trips([("999", "001", "05:32", "05:40")]), DAY)
    r = _row(iv)
    assert (r["delta"], r["delta_rebal"], r["dano"]) == (0, 0, 1)


def test_c_ecobici_removes_two_damaged():
    snaps = _snaps([("05:30", 5, 2), ("05:45", 5, 0)])
    iv = M.intervals(snaps, _trips([]), DAY)
    r = _row(iv)
    assert (r["delta"], r["taller_retiro"], r["delta_rebal"], r["reparacion"]) == (-2, 2, 0, 0)
    mv = M.moves_from_intervals(iv)
    assert len(mv) == 1                              # delta ≠ 0 queda en el parquet…
    assert len(M.main_moves(mv)) == 0                # …pero no es rebalanceo
    s = M.day_summary(iv, mv, DAY)
    assert (s["moves"], s["A"], s["R"], s["taller_retiro"]) == (0, 0, 0, 2)
    assert (s["stock_moves"], s["stock_R"]) == (1, 2)   # definición del run 1
    ev = M.damage_events(iv)
    assert ev[["kind", "n"]].values.tolist() == [["taller_retiro", 2]]


def test_d_removes_two_damaged_and_puts_five():
    snaps = _snaps([("05:30", 5, 2), ("05:45", 10, 0)])
    iv = M.intervals(snaps, _trips([]), DAY)
    r = _row(iv)
    assert (r["delta"], r["taller_retiro"], r["delta_rebal"], r["reparacion"]) == (3, 2, 5, 0)
    mv = M.moves_from_intervals(iv)
    K.validate_ecobici_moves(mv)
    s = M.day_summary(iv, mv, DAY)
    assert (s["moves"], s["A"], s["R"], s["taller_retiro"]) == (1, 5, 0, 2)
    assert s["taller_rama_delta_pos"] == 2                   # la rama ambigua
    assert s["warehouse_rama_pos_reparacion"] == 3           # leída como reparación + poner 3


def test_d_removes_damaged_and_takes_available():
    # 2 dañadas + 2 disponibles salen: taller 2, rebalanceo −2
    snaps = _snaps([("05:30", 5, 2), ("05:45", 3, 0)])
    r = _row(M.intervals(snaps, _trips([]), DAY))
    assert (r["delta"], r["taller_retiro"], r["delta_rebal"], r["reparacion"]) == (-4, 2, -2, 0)


def test_e_repair_in_place():
    snaps = _snaps([("05:30", 5, 2), ("05:45", 6, 1)])
    iv = M.intervals(snaps, _trips([]), DAY)
    r = _row(iv)
    assert (r["delta"], r["taller_retiro"], r["delta_rebal"], r["reparacion"], r["dano"]) == (0, 0, 0, 1, 0)
    assert len(M.moves_from_intervals(iv)) == 0
    assert M.damage_events(iv)[["kind", "n"]].values.tolist() == [["reparacion", 1]]


def test_e_repair_plus_taller_when_delta_negative():
    # dañadas 3 → 0, disponibles 5 → 6, stock 8 → 6: 2 al taller, 1 reparada
    r = _row(M.intervals(_snaps([("05:30", 5, 3), ("05:45", 6, 0)]), _trips([]), DAY))
    assert (r["delta"], r["taller_retiro"], r["reparacion"], r["delta_rebal"]) == (-2, 2, 1, 0)


@pytest.mark.parametrize("delta, d_dis", [(d, x) for d in range(-6, 7) for x in range(-5, 6)])
def test_decomposition_reconstructs_state(delta, d_dis):
    """Aplicar delta_rebal + eventos reproduce Δdisponibles y Δdañadas
    exactos, con taller ≥ 0 y delta_rebal = delta + taller."""
    dano, rep, taller = (int(x[0]) for x in M.decompose(np.array([delta]), np.array([d_dis])))
    assert min(dano, rep, taller) >= 0
    rebal = delta + taller
    assert dano - rep - taller == d_dis                        # dañadas
    assert rebal - dano + rep == delta - d_dis                 # disponibles (= delta_avail)
    if d_dis < 0 and delta == 0:
        assert (rep, taller) == (-d_dis, 0)                    # reparación en sitio
    if d_dis < 0 and delta > 0:
        assert taller == -d_dis                                # caso (d)


# ----------------------------------------------------------------------
# (f) medianoche y corte de las 00:30
# ----------------------------------------------------------------------

def test_f_interval_crossing_midnight():
    # 23:55 stock 6 → 00:10 stock 5; un viaje sale 23:58 y llega a otra
    # estación 00:05, otro sale 00:04 y uno llega 00:02 ⇒ delta = 5 − 6 − (1 − 2) = 0
    # 00:10 → 00:25 stock 9 sin viajes ⇒ +4
    snaps = _snaps([("05:30", 6, 0), (T("23:55"), 6, 0), (N("00:10"), 5, 0), (N("00:25"), 9, 0)])
    trips = _trips([("001", "999", T("23:58"), N("00:05")),
                    ("999", "001", T("23:50"), N("00:02")),
                    ("001", "999", N("00:04"), N("00:20"))])
    iv = M.intervals(snaps, trips, DAY)
    x = iv[iv["t0"] == T("23:55")].iloc[0]
    assert (x["t1"], x["departures"], x["arrivals"], x["delta"]) == (N("00:10"), 2, 1, 0)
    y = iv[iv["t0"] == N("00:10")].iloc[0]
    assert (y["t1"], y["delta"]) == (N("00:25"), 4)
    mv = M.moves_from_intervals(iv)
    assert mv["t0"].tolist() == [N("00:10")]
    assert C.window_day(mv["t0"].iloc[0]).isoformat() == DAY
    assert M.day_summary(iv, mv, DAY)["manana_moves"] == 0          # no es de la mañana


def test_f_closing_at_0030_is_cut():
    # último commit antes de 00:30 = 00:26; 00:26 → 00:41 es el cierre
    # (todas pasan a dañadas): no genera movimientos ni eventos
    snaps = _snaps([("05:30", 6, 1), (N("00:11"), 6, 1), (N("00:26"), 7, 1), (N("00:41"), 0, 8)])
    iv = M.intervals(snaps, _trips([]), DAY)
    assert iv["t1"].max() == N("00:26")
    assert M.moves_from_intervals(iv)["t0"].tolist() == [N("00:11")]
    assert len(M.damage_events(iv)) == 0


def test_f_cut_uses_commit_time():
    # con hora de estado (−30 s) el commit 00:30:10 queda en 00:29:40 < 00:30,
    # pero su commit es ≥ 00:30: ya es el cierre y no entra
    snaps = M.state_time(_snaps([("05:30", 6, 1), (N("00:20"), 6, 1), (N("00:30:10"), 0, 7)]))
    iv = M.intervals(snaps, _trips([]), DAY)
    assert iv["t1"].max() == N("00:19:30")
    o = M.observed_ef(snaps, DAY).iloc[0]
    assert o["E"] == 0                                        # el cierre no cuenta como vacía


# ----------------------------------------------------------------------
# resto (run 1 adaptado a la ventana 05:30–00:30)
# ----------------------------------------------------------------------

def test_event_boundaries_are_t0_exclusive_t1_inclusive():
    snaps = _snaps([("05:30", 5, 0), ("05:45", 4, 0), ("06:00", 4, 0)])
    iv = M.intervals(snaps, _trips([("001", "999", "05:45", "05:50")]), DAY)
    assert iv["departures"].tolist() == [1, 0]
    assert iv["delta"].tolist() == [0, 0]


def test_blank_intervals_excluded_and_counted():
    snaps = _snaps([("05:30", 5, 0), ("05:45", 0, 0, 0, True), ("06:00", 18, 2), ("06:15", 17, 2)])
    iv = M.intervals(snaps, _trips([]), DAY)
    assert iv["blank_touch"].tolist() == [True, True, False]
    assert iv["dano"].sum() == 0                              # un blanco no genera eventos
    mv = M.moves_from_intervals(iv)
    assert mv["delta"].tolist() == [-1]
    s = M.day_summary(iv, mv, DAY)
    assert s["excluded_blank"] == 2 and s["excluded_blank_nonzero"] == 2


def test_window_selection_uses_initial_snapshot_and_full_day():
    # t_ini = snapshot más cercano a 05:30 (05:33); lo anterior no entra;
    # las 12:30 ya no cortan: la ventana llega al último commit < 00:30
    snaps = _snaps([("05:18", 1, 0), ("05:33", 5, 0), ("05:48", 9, 0),
                    ("12:18", 9, 0), ("12:33", 2, 0), (N("00:20"), 3, 0), (N("00:35"), 0, 3)])
    iv = M.intervals(snaps, _trips([]), DAY)
    assert iv["t0"].tolist() == [T("05:33"), T("05:48"), T("12:18"), T("12:33")]
    assert iv["t1"].iloc[-1] == N("00:20")


def test_multi_station_trip_attribution():
    s1 = _snaps([("05:30", 5, 0), ("05:45", 4, 0)], "001")
    s2 = _snaps([("05:30", 2, 0), ("05:45", 3, 0)], "002")
    iv = M.intervals(pd.concat([s1, s2]), _trips([("001", "002", "05:35", "05:40")]), DAY)
    iv = iv.set_index("short_name")
    assert iv.loc["001", "departures"] == 1 and iv.loc["002", "arrivals"] == 1
    assert (iv["delta"] == 0).all()


def test_window_counts_full_day():
    mv = pd.DataFrame({"t0": [T("05:30"), T("05:40"), T("06:29"), T("06:30"), T("23:40"), N("00:20")]})
    w = M.window_counts(mv, DAY)
    assert len(w) == 73                         # 05:30 … 23:30
    assert w.index[0] == T("05:30") and w.index[-1] == T("23:30")
    assert w.iloc[0] == 3                        # [05:30, 06:30)
    assert w.iloc[1] == 2                        # [05:45, 06:45): 06:29, 06:30
    assert w.iloc[-1] == 2                       # [23:30, 00:30): 23:40, 00:20


def test_flow_stats_cumulative_warehouse():
    mv = pd.DataFrame({"short_name": ["a", "b", "c", "a"], "t0": [T("06:00"), T("07:00"), T("08:00"), T("09:00")],
                       "delta": [-3, -4, 2, 6]})
    s = M.flow_stats(mv)
    assert (s["A"], s["R"], s["rebalanced"], s["warehouse"]) == (8, 7, 7, 1)
    assert s["warehouse_cum_max_abs"] == 7 and s["stations_touched"] == 3


def test_damage_events_contract_and_moves_avail():
    snaps = _snaps([("05:30", 5, 2), ("05:45", 10, 0), ("06:00", 9, 1), ("06:15", 10, 0)])
    iv = M.intervals(snaps, _trips([]), DAY)
    ev = M.damage_events(iv)
    K.validate_damage_events(ev)
    assert ev[["t", "kind", "n"]].values.tolist() == [[T("05:45"), "taller_retiro", 2],
                                                      [T("06:00"), "daño", 1],
                                                      [T("06:15"), "reparacion", 1]]
    av = M.moves_from_intervals(iv, "delta_avail")
    assert av["delta"].tolist() == [5, -1, 1]
    assert (av["delta_rebal"] == av["delta"]).all() and (av["taller_retiro"] == 0).all()
    assert av["undo"].tolist() == [False, True, True]          # ±1 sobre la serie avail


def test_observed_ef_step_function():
    # 05:30–06:00 vacía, 06:00–06:30 llena, 06:30–07:00 en blanco, luego normal
    snaps = _snaps([("05:20", 0, 0, 20), ("06:00", 20, 0, 0), ("06:30", 0, 0, 0, True), ("07:00", 5, 0, 15)])
    o = M.observed_ef(snaps, DAY).iloc[0]
    assert o["E"] == pytest.approx(30)          # 05:30–06:00 (arranca de 05:20)
    assert o["F"] == pytest.approx(30)
    assert o["blank_min"] == pytest.approx(30)
    assert o["unobs_min"] == 0
    om = M.observed_ef(snaps, DAY, 0, 45).iloc[0]   # 05:30–06:15
    assert (om["E"], om["F"]) == (pytest.approx(30), pytest.approx(15))


def test_observed_ef_runs_to_0030():
    snaps = _snaps([("05:30", 5, 0, 15), (T("23:30"), 0, 0, 20)])
    o = M.observed_ef(snaps, DAY).iloc[0]
    assert o["E"] == pytest.approx(60)          # 23:30–00:30


def test_state_time_subtracts_commit_lag():
    snaps = _snaps([("05:30", 5, 0), ("05:45", 6, 0)])
    st = M.state_time(snaps)
    assert C.GBFS_COMMIT_LAG_S == 30
    assert (st["t"] == snaps["t"] - pd.Timedelta(seconds=30)).all()
    assert (st["t_commit"] == snaps["t"]).all()
    # una llegada a 05:44:45 cae antes del estado de 05:44:30 → intervalo siguiente
    iv = M.intervals(st, _trips([("999", "001", "05:40", "05:44:45")]), DAY)
    assert iv["arrivals"].tolist() == [0] and iv["delta"].tolist() == [1]


def test_initial_snapshot_picked_by_raw_commit_like_initial_state():
    snaps = M.state_time(_snaps([("05:24:51", 5, 0), ("05:35:14", 8, 0), ("05:49:08", 8, 0)]))
    iv = M.intervals(snaps, _trips([]), DAY)
    assert iv["t0"].iloc[0] == T("05:24:21")
    assert iv["delta"].tolist() == [3, 0]


def test_hour_blocks_anchored_at_0530():
    mv = pd.DataFrame({"t0": [T("05:24"), T("05:30"), T("06:29"), T("06:30"), N("00:29")],
                       "delta": [1, 2, -1, 3, -4]})
    b = M.hour_blocks(mv, DAY).set_index("block_start")
    assert list(b.index) == ["antes 05:30", "05:30", "06:30", "23:30"]
    assert b.loc["05:30", "moves"] == 2 and b.loc["05:30", "A"] == 2 and b.loc["05:30", "R"] == 1
    assert b.loc["06:30", "A"] == 3 and b.loc["23:30", "R"] == 4
    assert b.loc["antes 05:30", "moves"] == 1


def test_a_undo_pair_with_damaged_bike_rented_is_repair_not_taller():
    # una dañada se habilita y se renta; la salida (05:46) cae en el intervalo
    # siguiente al snapshot que ya no la ve: −1 con Δdañadas −1, luego +1.
    # Es ruido de timestamps: reparación, nunca taller, y el par es undo.
    snaps = _snaps([("05:30", 5, 1), ("05:45", 5, 0), ("06:00", 5, 0)])
    iv = M.intervals(snaps, _trips([("001", "999", "05:46", "05:55")]), DAY)
    assert iv["delta"].tolist() == [-1, 1]
    assert iv["undo"].tolist() == [True, True]
    assert iv["taller_retiro"].tolist() == [0, 0]
    assert iv["reparacion"].tolist() == [1, 0]
    assert iv["delta_rebal"].tolist() == [-1, 1]
    assert M.damage_events(iv)[["kind", "n"]].values.tolist() == [["reparacion", 1]]
    assert len(M.main_moves(M.moves_from_intervals(iv))) == 0


def test_day_summary_uses_explicit_day_not_first_t0():
    # snapshot inicial de 05:00:00 (t_estado 04:59:30): con window_day del
    # primer t0 la mañana caería en el día anterior y saldría vacía
    snaps = M.state_time(_snaps([("05:00", 5, 0), ("06:00", 9, 0)]))
    iv = M.intervals(snaps, _trips([]), DAY)
    assert iv["t0"].iloc[0] == T("04:59:30")
    s = M.day_summary(iv, M.moves_from_intervals(iv), DAY)
    assert s["manana_moves"] == 1 and s["manana_A"] == 4
