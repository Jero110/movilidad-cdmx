"""Tests de ecosim/fundamentos: config, contratos, loaders y días.

Los tests de `data` usan los cachés reales (`data/derived/ecosim/`); los
construye `uv run python -m ecosim.data build-trips|build-snapshots`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from ecosim import config as C
from ecosim import contracts as K
from ecosim import data, days

DAY_SEP = "2025-09-03"   # sale de 2025-09.csv
DAY_OCT = "2025-10-22"   # sale de 2025-10-1.csv

needs_cache = pytest.mark.skipif(
    not (C.TRIPS_PARQUET.exists() and (C.SNAPSHOT_DIR / f"{DAY_SEP}.parquet").exists()),
    reason="faltan cachés de ecosim (python -m ecosim.data build-trips / build-snapshots)",
)


# ============================================================================
# config
# ============================================================================

def test_config_constants():
    assert C.TZ == "America/Mexico_City"
    assert (C.DAY_START.hour, C.DAY_START.minute) == (5, 30)
    assert (C.DAY_END.hour, C.DAY_END.minute) == (0, 30)       # del día siguiente
    assert C.WINDOW_MIN == 19 * 60
    assert C.SNAPSHOT_WINDOW == (time(5, 0), time(1, 0)) and C.SNAPSHOT_WINDOW_MIN == 20 * 60
    assert C.MAX_BIKES_PER_MOVE == 22 and C.MAX_BIKES_PER_MOVE_SENS == (14, 22, 42)
    assert C.FORECAST_REFRESH_MIN == (15, 60, 180) and C.FORECAST_BLOCK_MIN == 60
    assert C.SELECTION_SEED == 20260929 and (C.N_SEL_WEEKDAY, C.N_SEL_WEEKEND) == (11, 4)
    assert C.COVERAGE_BLOCK_MIN == 60 and C.COVERAGE_MIN == 0.90
    assert C.EVAL_END == date(2025, 11, 29) and C.SNAPSHOT_END == date(2025, 11, 29)
    assert len(C.RUN1_EVAL_DAYS) == 15
    assert C.STEP_MIN == 15
    assert C.LEAD_MIN == 60 and C.LEAD_GRID_MIN == [60, 45, 30]
    assert C.BLOCK_MIN == 60 and C.BLOCK_GRID_MIN == [60, 30] and 15 not in C.BLOCK_GRID_MIN
    assert C.H_GRID_HOURS == [1, 2, 3]
    assert C.DETOUR_RADIUS_M == 500
    assert C.EVAL_SEED == 20260928
    assert (C.N_EVAL_WEEKDAY, C.N_EVAL_WEEKEND) == (11, 4)
    assert C.TRAIN_START == date(2025, 1, 1) and C.TRAIN_END == date(2025, 8, 31)
    assert "2025-12.csv" not in C.RAW_TRIP_FILES
    assert all(m != "Dec" for _, m in C.GBFS_MONTHS)


def test_decision_times():
    ts = C.decision_times("2025-09-03")
    assert len(ts) == 76
    assert ts[0] == datetime(2025, 9, 3, 5, 30) and ts[-1] == datetime(2025, 9, 4, 0, 15)
    assert all(b - a == timedelta(minutes=15) for a, b in zip(ts, ts[1:]))


def test_day_and_snapshot_bounds_cross_midnight():
    assert C.day_bounds("2025-09-03") == (datetime(2025, 9, 3, 5, 30), datetime(2025, 9, 4, 0, 30))
    assert C.snapshot_bounds("2025-09-03") == (datetime(2025, 9, 3, 5, 0), datetime(2025, 9, 4, 1, 0))
    assert C.day_bounds("2025-10-31")[1] == datetime(2025, 11, 1, 0, 30)   # cruza de mes


def test_window_day():
    d = date(2025, 9, 3)
    for t in ["2025-09-03 05:00", "2025-09-03 05:30", "2025-09-03 23:59", "2025-09-04 00:15",
              "2025-09-04 00:59", "2025-09-04 04:59"]:
        assert C.window_day(pd.Timestamp(t)) == d, t
    assert C.window_day(datetime(2025, 9, 4, 5, 0)) == date(2025, 9, 4)
    assert all(C.window_day(t) == d for t in C.decision_times(d))
    # as_date toma la fecha de calendario: con 00:15 da otra ventana (por eso existe window_day)
    assert C.as_date(pd.Timestamp("2025-09-04 00:15")) == date(2025, 9, 4)


def test_mexico_city_is_utc_minus_6_without_dst():
    tz = ZoneInfo(C.TZ)
    d = date(2025, 8, 1)
    while d <= date(2025, 11, 30):
        off = datetime(d.year, d.month, d.day, 6, tzinfo=tz).utcoffset()
        assert off == timedelta(hours=C.UTC_OFFSET_HOURS), d
        d += timedelta(days=1)


# ============================================================================
# contracts
# ============================================================================

T0 = pd.Timestamp("2025-09-03 05:30")


def _trips():
    return pd.DataFrame({
        "bike": ["1", "2"], "o": ["001", "002"], "d": ["002", "001"],
        "t_dep": [T0, T0 + pd.Timedelta(minutes=5)],
        "t_arr": [T0 + pd.Timedelta(minutes=10), T0 + pd.Timedelta(minutes=20)],
    })


def _forecast():
    return pd.DataFrame({
        "issued_at": [T0, T0], "variant": ["oracle", "oracle"], "short_name": ["001", "002"],
        "block_start": [T0, T0], "block_min": [60, 60],
        "departures": [3.0, 1.0], "arrivals": [2.0, 0.0],
        "dep_lo": [np.nan, np.nan], "dep_hi": [np.nan, np.nan],
        "arr_lo": [np.nan, np.nan], "arr_hi": [np.nan, np.nan],
        "refresh_min": [60, 60], "horizon_h": [6, 6],
    })


def _day_result():
    sh = pd.DataFrame({"short_name": ["001", "001"], "hour": [5, 6], "E": [10, 5], "F": [0, 2]})
    m = dict.fromkeys(K.DAY_METRICS, 0)
    m.update(E=15, F=2, A=4, R=1, rebalanced=1, warehouse=3)
    m.update(recorte_bodega=2, recorte_por_movimiento=np.int64(1), danos_aplicados=3, taller_aplicado=1)
    return K.DayResult(day="2025-09-03", arm="test", metrics=m, station_hour=sh)


def test_validate_trips():
    K.validate_trips(_trips())
    with pytest.raises(ValueError, match="faltan"):
        K.validate_trips(_trips().drop(columns="bike"))
    bad = _trips()
    bad["t_arr"] = bad["t_dep"] - pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="t_arr < t_dep"):
        K.validate_trips(bad)
    tz = _trips()
    tz["t_dep"] = tz["t_dep"].dt.tz_localize("UTC")
    with pytest.raises(ValueError, match="datetime"):
        K.validate_trips(tz)


def test_validate_snapshots_and_initial_state():
    s = pd.DataFrame({
        "short_name": ["001"], "t": [T0], "bikes": [3], "disabled": [1], "docks": [5],
        "docks_disabled": [0], "cap": [9], "is_renting": [True], "is_returning": [True],
    })
    K.validate_snapshots(s)
    with pytest.raises(ValueError, match="duplicado"):
        K.validate_snapshots(pd.concat([s, s]))
    neg = s.assign(bikes=-1)
    with pytest.raises(ValueError, match="negativos"):
        K.validate_snapshots(neg)
    i = s.rename(columns={"t": "t_snap"}).assign(
        offset_min=0.5, stale=False, is_installed=True, blank=False, out_of_service=False,
        bikes_snap=4, docks_snap=4, roll_adj=-1, roll_clipped=False)
    K.validate_initial_state(i)
    with pytest.raises(ValueError, match="bool"):
        K.validate_initial_state(i.assign(stale=1.0))


def test_validate_forecast():
    K.validate_forecast(_forecast())
    with pytest.raises(ValueError, match="variant"):
        K.validate_forecast(_forecast().assign(variant="nope"))
    with pytest.raises(ValueError, match="block_min"):
        K.validate_forecast(_forecast().assign(block_min=15))
    with pytest.raises(ValueError, match="duplicada"):
        K.validate_forecast(pd.concat([_forecast(), _forecast()]))
    with pytest.raises(ValueError, match="nulos"):
        K.validate_forecast(_forecast().assign(departures=np.nan))
    # bloques anclados a las 05:30 y dentro de [05:30, 00:30 del día siguiente)
    K.validate_forecast(_forecast().assign(block_start=pd.Timestamp("2025-09-03 11:30")))
    K.validate_forecast(_forecast().assign(block_start=pd.Timestamp("2025-09-03 06:00"), block_min=30))
    K.validate_forecast(_forecast().assign(block_start=pd.Timestamp("2025-09-03 23:30")))    # último de 60
    K.validate_forecast(_forecast().assign(block_start=pd.Timestamp("2025-09-04 00:00"), block_min=30))
    for bad, bm in [("2025-09-03 05:45", 60), ("2025-09-03 06:00", 60), ("2025-09-04 00:00", 60),
                    ("2025-09-03 05:00", 60), ("2025-09-03 05:45", 30), ("2025-09-04 00:30", 60),
                    ("2025-09-04 00:30", 30), ("2025-09-04 01:30", 60)]:
        with pytest.raises(ValueError, match="anclados"):
            K.validate_forecast(_forecast().assign(block_start=pd.Timestamp(bad), block_min=bm))
    for col, v in [("refresh_min", -1), ("horizon_h", 0)]:
        with pytest.raises(ValueError, match=col):
            K.validate_forecast(_forecast().assign(**{col: v}))
    with pytest.raises(ValueError, match="faltan"):
        K.validate_forecast(_forecast().drop(columns="refresh_min"))


def test_validate_forecast_multiple_issues():
    """Run 2: varias emisiones por día; la llave incluye refresh_min."""
    f = _forecast()
    late = f.assign(issued_at=pd.Timestamp("2025-09-04 00:15"),
                    block_start=pd.Timestamp("2025-09-03 23:30"))       # emitida después de medianoche
    other_f = f.assign(refresh_min=15)                                   # misma hora, otra serie
    daily = f.assign(variant="daily", refresh_min=0, horizon_h=19)
    K.validate_forecast(pd.concat([f, late, other_f, daily], ignore_index=True))
    for bad in ["2025-09-03 05:15", "2025-09-04 00:30", "2025-09-02 23:00"]:
        with pytest.raises(ValueError, match="issued_at"):
            K.validate_forecast(f.assign(issued_at=pd.Timestamp(bad)))


def test_orders_roundtrip_and_rules():
    eff = T0.to_pydatetime() + timedelta(minutes=60)
    o = [K.Order(T0.to_pydatetime(), eff, "001", 3), K.Order(T0.to_pydatetime(), eff, "002", -3)]
    df = K.orders_to_frame(o)
    K.validate_orders(df)
    assert K.frame_to_orders(df) == o
    with pytest.raises(ValueError):
        K.Order(T0.to_pydatetime(), eff, "001", 0)
    with pytest.raises(ValueError):
        K.Order(T0.to_pydatetime(), eff, "001", 1.5)
    with pytest.raises(ValueError):
        K.Order(eff, T0.to_pydatetime(), "001", 1)
    o64 = K.Order(T0.to_pydatetime(), eff, "001", np.int64(2))   # salida típica de un solver
    assert type(o64.delta) is int and o64.delta == 2
    with pytest.raises(ValueError, match="int"):
        K.validate_orders(df.assign(delta=df["delta"].astype(float)))
    with pytest.raises(ValueError, match="delta 0"):
        K.validate_orders(df.assign(delta=[3, 0]))


def test_validate_ecobici_moves():
    m = pd.DataFrame({
        "short_name": ["001", "002", "003"], "t0": [T0] * 3, "t1": [T0 + pd.Timedelta(minutes=15)] * 3,
        "delta": [3, -2, 3], "arrivals": [1, 0, 0], "departures": [0, 0, 0],
        # 002: retira 2 dañadas (solo taller); 003: retira 2 dañadas y pone 5
        "delta_rebal": [3, 0, 5], "taller_retiro": [0, 2, 2], "undo": [False, False, True],
    })
    K.validate_ecobici_moves(m)
    with pytest.raises(ValueError, match="t1 <= t0"):
        K.validate_ecobici_moves(m.assign(t1=T0))
    with pytest.raises(ValueError, match="faltan"):
        K.validate_ecobici_moves(m.drop(columns="undo"))
    with pytest.raises(ValueError, match="bool"):
        K.validate_ecobici_moves(m.assign(undo=[0, 1, 0]))
    with pytest.raises(ValueError, match="delta_rebal"):
        K.validate_ecobici_moves(m.assign(delta_rebal=[3, -2, 5]))
    with pytest.raises(ValueError, match="negativos"):
        K.validate_ecobici_moves(m.assign(taller_retiro=[0, -2, 2], delta_rebal=[3, -4, 5]))
    # delta_avail: opcional; si está, entero y sin nulos
    assert K.ECOBICI_MOVES_OPTIONAL == {"delta_avail": "int"} and "delta_avail" not in K.ECOBICI_MOVES_COLUMNS
    K.validate_ecobici_moves(m.assign(delta_avail=[3, 0, 3]))
    with pytest.raises(ValueError, match="delta_avail.*int"):
        K.validate_ecobici_moves(m.assign(delta_avail=[3.0, 0.5, 3.0]))
    with pytest.raises(ValueError, match="delta_avail"):
        K.validate_ecobici_moves(m.assign(delta_avail=[3, None, 3]))
    with pytest.raises(ValueError, match="delta_avail.*int"):
        K.validate_ecobici_moves(m.assign(delta_avail=["3", "0", "3"]))


def test_validate_damage_events():
    e = pd.DataFrame({
        "short_name": ["001", "001", "002"],
        "t": [T0, T0, T0 + pd.Timedelta(hours=19)],       # el último a las 00:30 del día siguiente
        "kind": ["daño", "taller_retiro", "reparacion"], "n": [1, 2, 1],
    })
    assert K.DAMAGE_EVENTS_COLUMNS == ["short_name", "t", "kind", "n"]
    K.validate_damage_events(e)
    with pytest.raises(ValueError, match="kind"):
        K.validate_damage_events(e.assign(kind=["daño", "robo", "reparacion"]))
    with pytest.raises(ValueError, match="> 0"):
        K.validate_damage_events(e.assign(n=[1, 0, 1]))
    with pytest.raises(ValueError, match="> 0"):
        K.validate_damage_events(e.assign(n=[1, -2, 1]))
    with pytest.raises(ValueError, match="int"):
        K.validate_damage_events(e.assign(n=[1.0, 2.0, 1.0]))
    with pytest.raises(ValueError, match="duplicado"):
        K.validate_damage_events(pd.concat([e, e.iloc[:1]]))
    with pytest.raises(ValueError, match="faltan"):
        K.validate_damage_events(e.drop(columns="kind"))


def test_policy_params_run2():
    p = K.PolicyParams()
    assert p.max_bikes_per_move == C.MAX_BIKES_PER_MOVE == 22 and p.refresh_min == 60
    for H in (1, 6, 12, 19, np.int64(8)):
        assert K.PolicyParams(H_horas=H).H_horas == int(H)
    for bad in (0, -1, 1.5, True, "2"):
        with pytest.raises(ValueError, match="H_horas"):
            K.PolicyParams(H_horas=bad)
    assert K.PolicyParams(max_bikes_per_move=42, refresh_min=0).max_bikes_per_move == 42
    with pytest.raises(ValueError, match="max_bikes_per_move"):
        K.PolicyParams(max_bikes_per_move=0)
    with pytest.raises(ValueError, match="refresh_min"):
        K.PolicyParams(refresh_min=-15)
    q = K.PolicyParams(**{**p.__dict__, "H_horas": 3})     # patrón de copia que usa el asignador
    assert q.H_horas == 3 and q.max_bikes_per_move == 22


def test_day_result_and_policy_signature():
    K.validate_day_result(_day_result())
    r = _day_result()
    r.metrics["E"] = 99
    with pytest.raises(ValueError, match="no suma"):
        K.validate_day_result(r)
    r = _day_result()
    del r.metrics["clipped"]
    with pytest.raises(ValueError, match="faltan"):
        K.validate_day_result(r)
    assert "arrivals_unknown" in K.DAY_METRICS
    r = _day_result()
    del r.metrics["arrivals_unknown"]
    with pytest.raises(ValueError, match="arrivals_unknown"):
        K.validate_day_result(r)
    assert K.RUN2_METRICS == ["recorte_bodega", "recorte_por_movimiento", "danos_aplicados",
                              "danos_no_aplicables", "taller_aplicado", "taller_no_aplicable"]
    for k in K.RUN2_METRICS:
        r = _day_result()
        del r.metrics[k]
        with pytest.raises(ValueError, match=k):
            K.validate_day_result(r)
        for bad in (-1, 1.5):
            r = _day_result()
            r.metrics[k] = bad
            with pytest.raises(ValueError, match=k):
                K.validate_day_result(r)
    # horas 5..24 (24 = 00:00–00:30 del día siguiente)
    r = _day_result()
    r.station_hour = pd.DataFrame({"short_name": ["001"] * 3, "hour": [5, 23, 24], "E": [10, 0, 5], "F": [0, 2, 0]})
    K.validate_day_result(r)
    for bad in (0, 4, 25):
        r.station_hour = r.station_hour.assign(hour=[5, 23, bad])
        with pytest.raises(ValueError, match="hour"):
            K.validate_day_result(r)

    class Nada:
        def decide(self, t, state, pending_orders, forecast, params):
            return []

    assert isinstance(Nada(), K.Policy)
    assert "dañadas" in K.SimState.__doc__ and "cambian durante el día" in K.SimState.__doc__
    st = K.SimState(t=T0.to_pydatetime(), stations=pd.DataFrame(
        {"short_name": ["001"], "bikes": [1], "disabled": [0], "docks": [2], "cap": [3]}))
    K.validate_state(st.stations)
    assert Nada().decide(st.t, st, [], _forecast(), K.PolicyParams()) == []


# ============================================================================
# data: viajes
# ============================================================================

def _raw_csv_day(fname: str, day: str) -> pd.DataFrame:
    """Parseo independiente (pandas, no duckdb) del CSV crudo."""
    raw = pd.read_csv(C.RAW_TRIPS_DIR / fname, dtype=str,
                      usecols=["Bici", "Ciclo_Estacion_Retiro", "Ciclo_EstacionArribo",
                               "Fecha_Retiro", "Hora_Retiro", "Fecha_Arribo", "Hora_Arribo"])
    t_dep = pd.to_datetime(raw["Fecha_Retiro"] + " " + raw["Hora_Retiro"], format="%d/%m/%Y %H:%M:%S")
    t_arr = pd.to_datetime(raw["Fecha_Arribo"] + " " + raw["Hora_Arribo"], format="%d/%m/%Y %H:%M:%S")
    clean = lambda c: raw[c].str.replace('"', "").str.strip()  # noqa: E731  (ID "Temporal …")
    df = pd.DataFrame({"bike": raw["Bici"].str.strip(), "o": clean("Ciclo_Estacion_Retiro"),
                       "d": clean("Ciclo_EstacionArribo"), "t_dep": t_dep, "t_arr": t_arr})
    s, e = (pd.Timestamp(x) for x in C.day_bounds(day))
    keep = ((df.t_dep >= s) & (df.t_dep < e)) | ((df.t_dep < s) & (df.t_arr >= s) & (df.t_arr < e))
    return df[keep]


@needs_cache
@pytest.mark.parametrize("day,fname", [(DAY_SEP, "2025-09.csv"), (DAY_OCT, "2025-10-1.csv")])
def test_trips_match_raw_csv(day, fname):
    got = data.trips(day)
    K.validate_trips(got)
    raw = _raw_csv_day(fname, day)
    # los CSV van por mes de llegada: un viaje de la ventana que llega el mes
    # siguiente o después está en otro archivo (p. ej. 09-03 18:48 → 11-19)
    next_month = pd.Timestamp(day[:8] + "01") + pd.offsets.MonthBegin(1)
    later = got[got.t_arr >= next_month]
    assert len(later) <= 5 and later.long.all()
    got = got[got.t_arr < next_month]
    key = ["bike", "o", "d", "t_dep", "t_arr"]
    a = got[key].sort_values(key).reset_index(drop=True)
    b = raw[key].sort_values(key).reset_index(drop=True)
    b["t_dep"] = b["t_dep"].astype(a["t_dep"].dtype)
    b["t_arr"] = b["t_arr"].astype(a["t_arr"].dtype)
    assert len(a) == len(b) > 10_000
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


@needs_cache
def test_trips_window_semantics():
    t = data.trips(DAY_SEP)
    s, e = (pd.Timestamp(x) for x in C.day_bounds(DAY_SEP))
    dep_in = (t.t_dep >= s) & (t.t_dep < e)
    arr_in = (t.t_arr >= s) & (t.t_arr < e)
    assert (dep_in | ((t.t_dep < s) & arr_in)).all()
    assert (t.t_dep < s).sum() > 0             # en curso a las 05:30
    assert (dep_in & (t.t_arr >= e)).sum() > 0  # salen dentro, llegan después de 00:30
    midnight = pd.Timestamp("2025-09-04")
    assert ((t.t_dep >= midnight) & dep_in).sum() > 50           # salen después de medianoche
    assert ((t.t_dep < midnight) & (t.t_arr >= midnight)).sum() > 20   # cruzan medianoche
    assert t.t_dep.max() >= pd.Timestamp("2025-09-04 00:20") and t.t_dep.max() < e
    assert not t.duplicated(["bike", "t_dep"]).any()
    assert {"long", "o_known", "d_known"} <= set(t.columns)
    st = set(data.stations(DAY_SEP)["short_name"])
    assert (t.o_known == t.o.isin(st)).all() and (t.d_known == t.d.isin(st)).all()


@needs_cache
def test_trips_range_and_seal():
    t = data.trips_range("2025-08-30", "2025-08-31")
    assert t.t_dep.min() >= pd.Timestamp("2025-08-30") and t.t_dep.max() < pd.Timestamp("2025-09-01")
    assert len(t) > 20_000
    with pytest.raises(ValueError, match="sellado"):
        data.trips_range("2025-11-30", "2025-12-01")
    with pytest.raises(ValueError, match="sellado"):
        data.trips("2025-12-02")


def test_window_of_nov30_is_sealed():
    """La ventana del 30-nov llega al 1-dic (sellado): ninguna función por día
    la lee, ni siquiera el caché crudo."""
    for f in (data.trips, data.snapshots, data.stations, data.initial_state, data.coverage,
              data._raw_snapshots, lambda d: data._check_day(C.as_date(d))):
        with pytest.raises(ValueError, match="sellado"):
            f("2025-11-30") if f is not data._raw_snapshots else f(date(2025, 11, 30))
    data._check_day(date(2025, 11, 29))     # termina el 30-nov 00:30 / 01:00: permitido
    data._check_not_sealed(datetime(2025, 11, 30, 23, 59))
    with pytest.raises(ValueError, match="sellado"):
        data._check_not_sealed(datetime(2025, 12, 1, 0, 0))


@needs_cache
def test_trips_audit_file():
    a = json.loads(C.TRIPS_AUDIT.read_text())
    assert "2025-12.csv" not in a["files"]
    assert a["bad_timestamp"] == 0 and a["negative_duration"] == 0
    assert a["exact_duplicates"] == 0 and a["file_month_ne_arrival_month"] == 0
    assert a["kept"] == a["raw_rows"]
    ids = [i for i, _ in a["non_numeric_station_ids"]]
    assert "Temporal - 2da Sección Bosque Chapultepec" in ids and not any('"' in i for i in ids)
    import duckdb
    n = duckdb.sql(f"select count(*) from '{C.TRIPS_PARQUET}' where contains(o, '\"') or contains(d, '\"')").fetchone()[0]
    assert n == 0


# --- viajes sintéticos (parquet temporal) ------------------------------------

def _fake_trips(tmp_path, monkeypatch, rows):
    """Reemplaza el parquet de viajes por uno con `rows` = (bike, o, d, t_dep, t_arr)."""
    df = pd.DataFrame(rows, columns=["bike", "o", "d", "t_dep", "t_arr"])
    df["t_dep"] = pd.to_datetime(df["t_dep"])
    df["t_arr"] = pd.to_datetime(df["t_arr"])
    df["long"] = (df["t_arr"] - df["t_dep"]) > pd.Timedelta(hours=3)
    p = tmp_path / "trips.parquet"
    df.to_parquet(p, index=False)
    monkeypatch.setattr(C, "TRIPS_PARQUET", p)


def test_trips_crossing_midnight(tmp_path, monkeypatch):
    """Ventana [09-03 05:30, 09-04 00:30). Un viaje que sale el día anterior y
    llega dentro entrega su bici; uno que cruza medianoche pero llega antes de
    las 05:30 no entra; los que salen antes de 00:30 entran aunque crucen
    medianoche o lleguen después del cierre."""
    monkeypatch.setattr(data, "_raw_snapshots", lambda d: _fake_raw([
        ("001", "1", "a", 19.43, -99.16, 10, 5, 0, 5, 0, True, True, True, "2025-09-03 05:31"),
        ("002", "2", "b", 19.44, -99.16, 10, 5, 0, 5, 0, True, True, True, "2025-09-03 05:31"),
    ]))
    _fake_trips(tmp_path, monkeypatch, [
        ("A", "001", "002", "2025-09-02 23:50:00", "2025-09-03 05:40:00"),  # cruza medianoche, llega dentro
        ("B", "001", "002", "2025-09-02 23:50:00", "2025-09-03 00:10:00"),  # cruza medianoche, llega antes
        ("C", "001", "002", "2025-09-03 12:00:00", "2025-09-04 00:30:00"),  # sale dentro, llega al cierre
        ("D", "002", "1000", "2025-09-02 06:00:00", "2025-09-03 06:00:00"),  # >1 día, llega dentro
        ("E", "001", "002", "2025-09-03 12:30:00", "2025-09-03 12:40:00"),  # cierre del run 1: ahora dentro
        ("F", "001", "002", "2025-09-03 05:20:00", "2025-09-03 05:29:59"),  # termina antes de 05:30
        ("G", "001", "002", "2025-09-03 23:50:00", "2025-09-04 00:10:00"),  # cruza medianoche dentro
        ("H", "001", "002", "2025-09-04 00:29:59", "2025-09-04 00:45:00"),  # sale justo antes del cierre
        ("I", "001", "002", "2025-09-04 00:30:00", "2025-09-04 00:40:00"),  # sale al cierre: fuera
        ("J", "001", "002", "2025-09-04 05:40:00", "2025-09-04 05:50:00"),  # ventana del día siguiente
        ("K", "001", "002", "2025-09-04 00:20:00", "2025-09-04 06:10:00"),  # sale dentro, llega al otro día
    ])
    t = data.trips(DAY_SEP).set_index("bike")
    assert set(t.index) == {"A", "C", "D", "E", "G", "H", "K"}
    assert t.loc["A", "t_dep"] == pd.Timestamp("2025-09-02 23:50") and t.loc["A", "long"]  # 5 h 50 min
    assert t.loc["D", "long"] and not t.loc["D", "d_known"] and t.loc["D", "o_known"]


# ============================================================================
# data: snapshots
# ============================================================================

@needs_cache
def test_snapshots_contract_and_window():
    s = data.snapshots(DAY_SEP)
    K.validate_snapshots(s)
    lo, hi = (pd.Timestamp(x) for x in C.snapshot_bounds(DAY_SEP))
    assert (s.t >= lo).all() and (s.t < hi).all()
    assert s.t.min() < lo + pd.Timedelta(minutes=20) and s.t.max() > hi - pd.Timedelta(minutes=20)
    assert s.t.nunique() > 60                         # ~70 commits en 20 h
    assert s.short_name.str.fullmatch(C.SHORT_NAME_RE).all()


@needs_cache
def test_snapshot_cache_files():
    """Un archivo por ventana 2025-08-01..2025-11-29, cada uno con renglones solo
    de su ventana [d 05:00, d+1 01:00); nada del 30-nov ni de diciembre."""
    files = sorted(C.SNAPSHOT_DIR.glob("*.parquet"))
    names = [f.stem for f in files]
    assert names[0] == "2025-08-01" and names[-1] == "2025-11-29" and len(names) == 121
    for f in files:
        t = pd.read_parquet(f, columns=["t"])["t"]
        lo, hi = (pd.Timestamp(x) for x in C.snapshot_bounds(f.stem))
        assert t.min() >= lo and t.max() < hi, f.stem
        assert t.max() < pd.Timestamp("2025-11-30 01:00")
    assert not list(C.SNAPSHOT_DIR.glob("*.tmp"))


@needs_cache
def test_snapshots_utc_to_local():
    raw = data._raw_snapshots(date(2025, 9, 3))
    utc = raw["committed_at_utc"].dt.tz_convert("UTC").dt.tz_localize(None)
    assert ((utc - raw["t"]) == pd.Timedelta(hours=6)).all()


def _match_rate(day: str, shift_min: float) -> float:
    """Fracción de intervalos (estación, snapshot→snapshot) donde el cambio de
    stock = llegadas − salidas de los viajes, con los viajes corridos `shift_min`."""
    sn = data.snapshots(day)
    sn = sn[~sn.blank].assign(stock=lambda x: x.bikes + x.disabled).sort_values(["short_name", "t"])
    tr = data.trips_range(day, day)
    sh = pd.Timedelta(minutes=shift_min)
    ev = pd.concat([
        pd.DataFrame({"s": tr.o, "tt": tr.t_dep + sh, "v": -1}),
        pd.DataFrame({"s": tr.d, "tt": tr.t_arr + sh, "v": 1}),
    ])
    ok = tot = 0
    evg = dict(tuple(ev.sort_values("tt").groupby("s")))
    for s, g in sn.groupby("short_name"):
        e = evg.get(s)
        tt = e.tt.to_numpy() if e is not None else np.array([], dtype="datetime64[us]")
        cs = np.concatenate([[0], np.cumsum(e.v.to_numpy())]) if e is not None else np.array([0])
        t = g.t.to_numpy()
        net = cs[np.searchsorted(tt, t[1:], "right")] - cs[np.searchsorted(tt, t[:-1], "right")]
        d = np.diff(g.stock.to_numpy()) - net
        ok += (d == 0).sum()
        tot += len(d)
    return ok / tot


@needs_cache
def test_snapshot_clock_aligns_with_trip_clock():
    """Si la conversión UTC→local estuviera mal (p. ej. ±1 h o ±6 h), el
    stock GBFS dejaría de cuadrar con los viajes. El mejor ajuste es sin
    corrimiento (±30 s)."""
    r = {s: _match_rate(DAY_OCT, s) for s in (-360, -60, -15, 0, 0.5, 15, 60, 360)}
    best = max(r, key=r.get)
    assert best in (0, 0.5), r
    assert r[0] > 0.7 and r[0] > r[-15] + 0.2 and r[0] > r[15] + 0.2, r


@needs_cache
def test_coverage_real_day_and_synthetic(monkeypatch):
    assert 0.9 <= data.coverage(DAY_SEP) <= 1.0
    # bloques de 60 min en [05:30, 00:30): 19. Uno a las 05:31 y otro a las 05:50
    # cuentan una vez; 00:10 del día siguiente es el bloque 18; 00:40 y 05:10 no cuentan.
    ts = [datetime(2025, 9, 3, 5, 31) + timedelta(minutes=60 * k) for k in range(9)]
    ts += [datetime(2025, 9, 3, 5, 50), datetime(2025, 9, 4, 0, 10), datetime(2025, 9, 4, 0, 40),
           datetime(2025, 9, 3, 5, 10)]
    fake = pd.DataFrame({"short_name": "001", "t": pd.to_datetime(ts)})
    monkeypatch.setattr(data, "snapshots", lambda day: fake)
    assert data.coverage(DAY_SEP) == 10 / 19
    assert data.coverage(DAY_SEP, block_min=15) == 11 / 76      # 05:31 y 05:50: dos bloques de 15


# ============================================================================
# data: estaciones, vecinos, estado inicial
# ============================================================================

def _fake_raw(rows):
    cols = ["short_name", "station_id", "name", "lat", "lon", "cap", "bikes", "disabled", "docks",
            "docks_disabled", "is_installed", "is_renting", "is_returning", "t"]
    df = pd.DataFrame(rows, columns=cols)
    df["t"] = pd.to_datetime(df["t"])
    return df


def test_stations_flags_invalid_and_duplicate_coords(monkeypatch):
    t = "2025-09-03 05:31"
    raw = _fake_raw([
        ("001", "1", "a", 19.43, -99.16, 10, 5, 0, 5, 0, True, True, True, t),
        ("002", "2", "b", 19.43, -99.16, 10, 5, 0, 5, 0, True, True, True, t),   # mismas coords que 001
        ("003", "3", "c", 0.0, 0.0, 10, 5, 0, 5, 0, True, True, True, t),        # ~10,900 km
        ("004", "4", "d", 45.53, -73.62, 10, 5, 0, 5, 0, True, True, True, t),   # Montreal
        ("005", "5", "e", 19.44, -99.16, 10, 5, 0, 5, 0, True, True, True, t),
    ])
    monkeypatch.setattr(data, "_raw_snapshots", lambda d: raw)
    s = data.stations(DAY_SEP).set_index("short_name")
    assert s.loc["003", "coord_invalid"] and s.loc["004", "coord_invalid"]
    assert not s.loc[["001", "002", "005"], "coord_invalid"].any()
    assert s.loc["001", "coord_dup"] and s.loc["002", "coord_dup"] and not s.loc["005", "coord_dup"]
    n = data.neighbors(DAY_SEP)
    assert set(n.short_name) == {"001", "002", "005"} and set(n.nbr) == {"001", "002", "005"}
    assert n[(n.short_name == "001") & (n["rank"] == 1)].nbr.item() == "002"
    assert n[(n.short_name == "001") & (n.nbr == "002")].dist_m.item() == 0


def test_haversine():
    assert data.haversine_m(19.0, -99.0, 20.0, -99.0) == pytest.approx(111_195, rel=1e-3)
    assert data.haversine_m(19.4, -99.1, 19.4, -99.1) == 0


@needs_cache
def test_stations_and_neighbors_real_day():
    s = data.stations(DAY_SEP)
    assert 650 <= len(s) <= 700 and not s.short_name.duplicated().any()
    assert (s.cap >= 0).all()
    n = data.neighbors(DAY_SEP)
    valid = (~s.coord_invalid).sum()
    assert len(n) == valid * (valid - 1)
    assert (n.groupby("short_name").dist_m.apply(lambda x: x.is_monotonic_increasing)).all()
    first = n[n["rank"] == 1].set_index("short_name").dist_m
    assert (first == n.groupby("short_name").dist_m.min()).all()
    ab = n.set_index(["short_name", "nbr"]).dist_m
    ba = ab.copy()
    ba.index = ba.index.swaplevel()
    assert np.allclose(ab.sort_index(), ba.sort_index())


@needs_cache
def test_initial_state_real_day():
    i = data.initial_state(DAY_SEP)
    K.validate_initial_state(i)
    assert 650 <= len(i) <= 700
    snap = data.snapshots(DAY_SEP)
    start = pd.Timestamp(C.day_bounds(DAY_SEP)[0])
    best = (snap.t - start).abs().groupby(snap.short_name).min() / pd.Timedelta(minutes=1)
    assert np.allclose(i.set_index("short_name").offset_min.abs().sort_index(), best.sort_index())
    assert (i.stale == (i.offset_min.abs() > C.INITIAL_STALE_MIN)).all()
    assert ((i.bikes + i.disabled + i.docks + i.docks_disabled) == i.cap).all()
    room = i.cap - i.disabled - i.docks_disabled
    assert (i.bikes == (i.bikes_snap + i.roll_adj).clip(lower=0).clip(upper=room)).all()
    assert i.attrs["n_roll_clipped"] == i.roll_clipped.sum()
    assert (i.roll_adj != 0).sum() > 10     # el snapshot es de ~05:34: hay viajes que descontar
    assert set(i[i.blank].short_name) == {"091", "168"} and (i.out_of_service >= i.blank).all()
    assert (i.t_snap < pd.Timestamp("2025-09-03 06:00")).all()   # la ventana de 24 h no cambia la elección


@needs_cache
@pytest.mark.parametrize("day", [DAY_SEP, "2025-10-25"])
def test_stations_and_initial_state_same_capacity(day):
    """Una sola regla de capacidad (091 está en blanco el 09-03; 698 tiene cap 0 el 10-25)."""
    s = data.stations(day).set_index("short_name").sort_index()
    i = data.initial_state(day).set_index("short_name").sort_index()
    assert (s.index == i.index).all()
    assert (s.cap == i.cap).all() and (s.out_of_service == i.out_of_service).all()
    if day == DAY_SEP:
        assert s.loc["091", "cap"] == 18 and s.loc["091", "out_of_service"]
    else:
        # en blanco con cap 0 a las 05:30; reaparece a las 16:35 con cap 15. La
        # regla (cap máxima reportada en la ventana) da 15 con la ventana de 24 h
        # (en el run 1, con 05:00–13:00, daba 0). Sigue fuera de servicio.
        assert s.loc["698", "cap"] == 15 and s.loc["698", "out_of_service"]
        assert i.loc["698", "bikes"] == 0 and i.loc["698", "docks"] == 15 and i.loc["698", "blank"]


def test_initial_state_rolls_back_to_0530(tmp_path, monkeypatch):
    """stock_0530 = stock_snap + salidas − llegadas en [05:30, t_snap] (snapshot
    posterior) o − salidas + llegadas en (t_snap, 05:30) (snapshot anterior),
    recortado a [0, cap − dañadas − docks_disabled]."""
    monkeypatch.setattr(data, "_raw_snapshots", lambda d: _fake_raw([
        ("001", "1", "a", 19.43, -99.16, 10, 5, 1, 4, 0, True, True, True, "2025-09-03 05:34"),  # posterior
        ("002", "2", "b", 19.44, -99.16, 10, 5, 0, 5, 0, True, True, True, "2025-09-03 05:27"),  # anterior
        ("003", "3", "c", 19.45, -99.16, 10, 9, 1, 0, 0, True, True, True, "2025-09-03 05:34"),  # llena
        ("004", "4", "d", 19.46, -99.16, 10, 0, 0, 10, 0, True, True, True, "2025-09-03 05:34"),  # vacía
    ]))
    _fake_trips(tmp_path, monkeypatch, [
        # 001 (snap 05:34): 2 salidas y 1 llegada en [05:30, 05:34] → +1; las de fuera no cuentan
        ("a", "001", "009", "2025-09-03 05:30:00", "2025-09-03 05:50:00"),
        ("b", "001", "009", "2025-09-03 05:33:00", "2025-09-03 05:50:00"),
        ("c", "009", "001", "2025-09-03 05:10:00", "2025-09-03 05:32:00"),
        ("d", "001", "009", "2025-09-03 05:29:00", "2025-09-03 05:50:00"),   # antes de 05:30
        ("e", "001", "009", "2025-09-03 05:35:00", "2025-09-03 05:50:00"),   # después del snapshot
        # 002 (snap 05:27): 1 llegada y 2 salidas en (05:27, 05:30) → −1
        ("f", "009", "002", "2025-09-03 05:00:00", "2025-09-03 05:28:00"),
        ("g", "002", "009", "2025-09-03 05:28:30", "2025-09-03 05:50:00"),
        ("h", "002", "009", "2025-09-03 05:29:59", "2025-09-03 05:50:00"),
        ("i", "002", "009", "2025-09-03 05:30:00", "2025-09-03 05:50:00"),   # ya en la ventana
        # 003 llena (9 + 1 dañada = 10): salida en [05:30, 05:34] → pediría 10 → recorte a 9
        ("j", "003", "009", "2025-09-03 05:31:00", "2025-09-03 05:50:00"),
        # 004 vacía: llegada en [05:30, 05:34] → pediría −1 → recorte a 0
        ("k", "009", "004", "2025-09-03 05:20:00", "2025-09-03 05:31:00"),
    ])
    i = data.initial_state(DAY_SEP).set_index("short_name")
    assert list(i.roll_adj) == [1, -1, 1, -1]
    assert list(i.bikes_snap) == [5, 5, 9, 0]
    assert list(i.bikes) == [6, 4, 9, 0]
    assert list(i.docks) == [3, 6, 0, 10]
    assert list(i.roll_clipped) == [False, False, True, True]
    assert i.attrs["n_roll_clipped"] == 2
    K.validate_initial_state(i.reset_index())


def test_initial_state_rolls_with_corrected_state_time(tmp_path, monkeypatch):
    """El ajuste a 05:30 usa la hora del estado del feed, commit −
    GBFS_COMMIT_LAG_S (como `medicion`), no el commit crudo: un viaje en los
    últimos 30 s antes del commit todavía no está en el snapshot."""
    assert C.GBFS_COMMIT_LAG_S == 30
    monkeypatch.setattr(data, "_raw_snapshots", lambda d: _fake_raw([
        ("001", "1", "a", 19.43, -99.16, 10, 5, 0, 5, 0, True, True, True, "2025-09-03 05:34:00"),  # estado 05:33:30
        ("002", "2", "b", 19.44, -99.16, 10, 5, 0, 5, 0, True, True, True, "2025-09-03 05:30:10"),  # estado 05:29:40
    ]))
    _fake_trips(tmp_path, monkeypatch, [
        ("a", "001", "009", "2025-09-03 05:33:20", "2025-09-03 05:50:00"),   # en el snapshot → +1
        ("b", "001", "009", "2025-09-03 05:33:45", "2025-09-03 05:50:00"),   # después del estado → no
        # 002: estado anterior a 05:30 → la salida de 05:29:50 ya no está en el
        # snapshot pero ocurre antes de 05:30 → −1; la de 05:30:05 la entrega el simulador
        ("c", "002", "009", "2025-09-03 05:29:50", "2025-09-03 05:50:00"),
        ("d", "002", "009", "2025-09-03 05:30:05", "2025-09-03 05:50:00"),
    ])
    i = data.initial_state(DAY_SEP).set_index("short_name")
    assert list(i.roll_adj) == [1, -1]
    assert list(i.bikes) == [6, 4]
    # la elección y offset_min siguen con el commit crudo
    assert i.loc["001", "offset_min"] == 4 and i.loc["002", "t_snap"] == pd.Timestamp("2025-09-03 05:30:10")


def test_initial_state_blank_and_stale(tmp_path, monkeypatch):
    raw = _fake_raw([
        # 001: en blanco a las 05:30 y surtida después → no se salta al snapshot surtido
        ("001", "1", "a", 19.43, -99.16, 0, 0, 0, 0, 0, True, True, True, "2025-09-03 05:31"),
        ("001", "1", "a", 19.43, -99.16, 20, 20, 0, 0, 0, True, True, True, "2025-09-03 10:00"),
        # 002: solo reporta a las 05:50 → stale (20 min)
        ("002", "2", "b", 19.44, -99.16, 10, 4, 1, 5, 0, True, True, True, "2025-09-03 05:50"),
        # 003: antes y después; gana el más cercano (05:28)
        ("003", "3", "c", 19.45, -99.16, 10, 1, 0, 9, 0, True, True, True, "2025-09-03 05:28"),
        ("003", "3", "c", 19.45, -99.16, 10, 7, 0, 3, 0, True, True, True, "2025-09-03 05:35"),
    ])
    raw["committed_at_utc"] = raw["t"]
    monkeypatch.setattr(data, "_raw_snapshots", lambda d: raw)
    _fake_trips(tmp_path, monkeypatch, [("z", "009", "008", "2025-09-03 05:31", "2025-09-03 05:40")])
    i = data.initial_state(DAY_SEP).set_index("short_name")
    a = i.loc["001"]
    assert a.blank and a.bikes == 0 and a.docks == 20 and a.cap == 20 and a.offset_min == 1
    assert a.out_of_service and not i.loc["002", "out_of_service"]
    assert i.loc["002", "stale"] and i.loc["002", "offset_min"] == 20
    assert i.loc["003", "offset_min"] == -2 and i.loc["003", "bikes"] == 1 and not i.loc["003", "stale"]


# ============================================================================
# días
# ============================================================================

@needs_cache
def test_days_json():
    js = json.loads(C.DAYS_JSON.read_text())
    for kind in ("evaluacion", "seleccion"):
        ds = js[kind]
        assert len(ds) == 15
        assert sum(d["type"] == "weekday" for d in ds) == 11
        assert sum(d["type"] in ("weekend", "holiday") for d in ds) == 4
        assert all(d["coverage"] >= 0.9 for d in ds)
        assert all(C.EVAL_START.isoformat() <= d["day"] <= "2025-11-29" for d in ds)
        assert [d["day"] for d in ds] == sorted(d["day"] for d in ds) == days.load_days(kind)
        for d in ds:
            assert d["type"] == days.day_type(date.fromisoformat(d["day"]))
    ev, sel = days.load_days("evaluacion"), days.load_days("seleccion")
    assert not set(ev) & set(sel)
    assert days.load_days() == ev and js["days"] == js["evaluacion"]     # alias del run 1
    assert days.load_days("todos") == sorted(ev + sel)
    # los 15 del run 1 cumplen la cobertura nueva: sin reemplazos
    assert ev == list(C.RUN1_EVAL_DAYS) and js["reemplazos_evaluacion"] == []
    # coberturas guardadas = recalculadas; reproducible con las semillas
    cov = {c["day"]: c["coverage"] for c in js["all_candidates"]}
    for d in ev + sel:
        assert cov[d] == round(data.coverage(d), 4)
    assert "2025-11-30" not in cov and min(cov) == "2025-09-01" and max(cov) == "2025-11-29"
    assert days.evaluation_days(js["all_candidates"]) == (js["evaluacion"], [])
    assert days.selection_days(js["all_candidates"], ev) == js["seleccion"]
    with pytest.raises(ValueError, match="kind"):
        days.load_days("otra")


def _fake_cands():
    from datetime import date as _d
    out, d = [], C.EVAL_START
    while d <= C.EVAL_END:
        out.append({"day": d.isoformat(), "type": days.day_type(d), "coverage": 1.0})
        d += timedelta(days=1)
    return out


def test_evaluation_days_replacement():
    """Un día del run 1 que no cumple la cobertura se reemplaza por otro del
    mismo estrato (semilla EVAL_SEED), que no sea del run 1."""
    cands = _fake_cands()
    ev, repl = days.evaluation_days(cands)
    assert [c["day"] for c in ev] == list(C.RUN1_EVAL_DAYS) and repl == []
    for c in cands:
        if c["day"] in ("2025-09-03", "2025-09-28"):      # un día entre semana y un domingo
            c["coverage"] = 0.5
        if c["day"] == "2025-09-04":                     # tampoco es elegible como reemplazo
            c["coverage"] = 0.8
    ev, repl = days.evaluation_days(cands)
    days_ev = [c["day"] for c in ev]
    assert len(ev) == 15 and "2025-09-03" not in days_ev and "2025-09-28" not in days_ev
    assert [r["run1"] for r in repl] == ["2025-09-03", "2025-09-28"]
    new = {r["run1"]: r["nuevo"] for r in repl}
    assert days.day_type(date.fromisoformat(new["2025-09-03"])) == "weekday"
    assert days.day_type(date.fromisoformat(new["2025-09-28"])) != "weekday"
    assert not set(new.values()) & set(C.RUN1_EVAL_DAYS) and "2025-09-04" not in new.values()
    assert sum(c["type"] == "weekday" for c in ev) == 11
    assert days.evaluation_days(cands) == (ev, repl)      # determinista


def test_selection_days_disjoint_and_stratified():
    cands = _fake_cands()
    ev = list(C.RUN1_EVAL_DAYS)
    sel = days.selection_days(cands, ev)
    d = [c["day"] for c in sel]
    assert len(sel) == 15 and not set(d) & set(ev) and d == sorted(d)
    assert sum(c["type"] == "weekday" for c in sel) == 11
    assert days.selection_days(cands, ev) == sel


def test_day_type():
    assert days.day_type(date(2025, 9, 16)) == "holiday"   # Independencia
    assert days.day_type(date(2025, 11, 17)) == "holiday"  # Revolución
    assert days.day_type(date(2025, 9, 13)) == "weekend"
    assert days.day_type(date(2025, 9, 15)) == "weekday"


# ============================================================================
# auditoría de la noche
# ============================================================================

@needs_cache
def test_night_audit_feed_open_until_0030():
    """El feed es confiable hasta las 00:30 y cierra después: en 00:00–00:30
    casi todas las estaciones rentan y el stock cuadra con los viajes; en
    00:30–01:00 casi ninguna renta y las disponibles pasan a dañadas."""
    a = data.night_audit([DAY_SEP, DAY_OCT])["por_franja_30min"]
    assert len(a) == 40 and list(a)[0] == "05:00" and list(a)[-1] == "00:30"
    n, c = a["00:00"], a["00:30"]
    assert n["commits_por_dia"] >= 1 and n["no_renta"] < 0.01 and n["cuadre"] > 0.9
    assert n["salidas_por_dia"] > 100
    assert c["no_renta"] > 0.9 and c["disponibles"] < 0.1 * n["disponibles"] and c["danadas"] > 3 * n["danadas"]
    assert c["salidas_por_dia"] < 5
