"""Contratos y datos base del run 3."""

import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ecosim import config as C
from ecosim import contracts as K
from ecosim import data, days


def test_window_and_folds():
    start, end = C.day_bounds("2025-09-03")
    assert (start, end) == (datetime(2025, 9, 3, 5), datetime(2025, 9, 4, 0, 30))
    ts = C.decision_times("2025-09-03")
    assert len(ts) == 78 and ts[0] == start and ts[-1] == end - timedelta(minutes=15)
    assert C.window_day(ts[-1]) == date(2025, 9, 3)
    assert C.PICKUP_MIN == 15 and C.DELIVERY_SENS_MIN == (45, 60, 75)
    assert (C.VISITS_PER_DECISION, C.MAX_BIKES_PER_VISIT) == (67, 14)
    assert (C.VISITS_PER_DECISION_SENS, C.MAX_BIKES_PER_VISIT_SENS) == (83, 24)
    assert C.N_GRID == (1, 2, 3, 4, 5, 6)
    assert len(C.FOLDS) == 13
    assert C.FOLDS[0]["test_month"] == "2025-08"
    assert C.FOLDS[5] == {"name": "prueba_5", "train_start": date(2025, 5, 1),
                          "train_end": date(2025, 12, 31), "test_month": "2026-01"}
    assert C.FOLDS[-1]["train_start"] == date(2025, 12, 1)
    assert C.FOLDS[-1]["test_month"] == "2026-08"


def test_orders_and_params():
    t = datetime(2025, 9, 3, 5)
    o = K.Order(t, t + timedelta(minutes=15), t + timedelta(minutes=60), "001", np.int64(-3))
    assert o.delta == -3
    frame = K.orders_to_frame([o])
    assert K.frame_to_orders(frame) == [o]
    with pytest.raises(ValueError):
        K.Order(t, t, t, "001", 0)
    p = K.PolicyParams(n_hours=np.int64(3))
    assert p.n_hours == 3 and p.max_bikes_per_visit == 14
    with pytest.raises(ValueError):
        K.PolicyParams(pickup_min=60, delivery_min=60)


def test_damage_and_moves_contracts():
    t = pd.Timestamp("2025-09-03 05:15")
    damage = pd.DataFrame({"short_name": ["001", "001"], "t": [t, t],
                           "kind": ["sube", "baja"], "n": [1, 2]})
    K.validate_damage_events(damage)
    with pytest.raises(ValueError):
        K.validate_damage_events(damage.assign(kind=["daño", "baja"]))
    moves = pd.DataFrame({"short_name": ["001"], "t0": [t], "t1": [t + pd.Timedelta(minutes=15)],
                          "delta": [-2], "arrivals": [1], "departures": [3], "par": [False]})
    K.validate_ecobici_moves(moves)


def test_day_result_contract():
    r = K.DayResult(day="2025-09-03", arm="oracle", E=4, F=2, EF=6,
                    visitas=2, visitas_recoger=1, visitas_entregar=1,
                    bicis_movidas=6, en_transito_max=3, recortes={"capacidad": 0},
                    desvios_salida=1, desvios_llegada=0, km_desvio_medio=0.2,
                    danadas_no_aplicables=0, tiempos_decision=[0.1])
    assert K.validate_day_result(r).metrics["EF"] == 6
    r.visitas = 3
    with pytest.raises(ValueError):
        K.validate_day_result(r)


def test_forecast_table_protocol():
    class Fake:
        forma = "directa"
        modelo = "oracle"

        def table(self, t, n_hours):
            return np.zeros((2, 4 * n_hours, 2))

    f = Fake()
    assert isinstance(f, K.ForecastTable)
    assert K.validate_forecast_table(f, datetime(2025, 9, 3, 5), 2, 2).shape == (2, 8, 2)
    f.forma = "otra"
    with pytest.raises(ValueError):
        K.validate_forecast_table(f, datetime(2025, 9, 3, 5), 2, 2)


def test_coverage_has_partial_last_block(monkeypatch):
    start = pd.Timestamp("2025-09-03 05:00")
    times = pd.Series([start + pd.Timedelta(hours=k, minutes=5) for k in range(19)]
                      + [pd.Timestamp("2025-09-04 00:10")])
    monkeypatch.setattr(data, "snapshot_times", lambda day: times)
    assert data.coverage("2025-09-03") == 1.0
    monkeypatch.setattr(data, "snapshot_times", lambda day: times.iloc[:-1])
    assert data.coverage("2025-09-03") == 19 / 20


def test_opening_photo_and_rollback(monkeypatch):
    d = date(2025, 9, 3)
    rows = []
    for tt, bikes, disabled, renting in (("05:06:00", 0, 5, False),
                                          ("05:23:00", 5, 0, True)):
        for s in ("001", "002"):
            rows.append({"short_name": s, "station_id": s, "name": s,
                         "lat": 19.4, "lon": -99.17, "cap": 10, "bikes": bikes,
                         "disabled": disabled, "docks": 5, "docks_disabled": 0,
                         "is_installed": True, "is_renting": renting,
                         "is_returning": True, "t": pd.Timestamp(f"2025-09-03 {tt}")})
    monkeypatch.setattr(data, "_raw_snapshots", lambda day: pd.DataFrame(rows))
    tr = pd.DataFrame({"o": ["001"], "d": ["002"],
                       "t_dep": [pd.Timestamp("2025-09-03 05:10")],
                       "t_arr": [pd.Timestamp("2025-09-03 05:20")]})
    monkeypatch.setattr(data, "_query_trips", lambda where: tr)
    s = data.initial_state(d).set_index("short_name")
    assert (s.t_snap == pd.Timestamp("2025-09-03 05:23")).all()
    assert s.loc["001", "bikes"] == 6 and s.loc["002", "bikes"] == 4
    assert s.disabled.sum() == 0


def test_opening_rejects_sparse_commit(monkeypatch):
    d = date(2025, 9, 3)
    rows = []
    for minute, stations in ((5, ["001"]), (20, [f"{i:03d}" for i in range(1, 21)])):
        for short_name in stations:
            rows.append({"short_name": short_name, "station_id": short_name, "name": short_name,
                         "lat": 19.4, "lon": -99.17, "cap": 10, "bikes": 5,
                         "disabled": 0, "docks": 5, "docks_disabled": 0,
                         "is_installed": True, "is_renting": True, "is_returning": True,
                         "t": pd.Timestamp(f"2025-09-03 05:{minute:02d}")})
    monkeypatch.setattr(data, "_raw_snapshots", lambda day: pd.DataFrame(rows))
    monkeypatch.setattr(data, "_query_trips", lambda where: pd.DataFrame({
        "o": pd.Series(dtype=str), "d": pd.Series(dtype=str),
        "t_dep": pd.Series(dtype="datetime64[ns]"),
        "t_arr": pd.Series(dtype="datetime64[ns]")}))
    assert days._initial_photo_offset(d) == 20
    state = data.initial_state(d)
    assert len(state) == 20 and (state.t_snap == pd.Timestamp("2025-09-03 05:20")).all()


def test_real_december_state_and_trip_span():
    state = data.initial_state("2025-12-10")
    K.validate_initial_state(state)
    assert 4000 < state.bikes.sum() < 7000
    assert C.TRIPS_PARQUET.exists()
    assert (C.RAW_TRIPS_DIR / "2024-01.csv").exists()
    assert (C.RAW_TRIPS_DIR / "public_data_web_2026-08_2.csv").exists()


@pytest.mark.parametrize("day, detail", [("2025-05-24", "0.617"),
                                          ("2025-05-26", "08:38")])
def test_unusable_opening_raises(day, detail):
    with pytest.raises(ValueError, match="sin foto abierta utilizable") as exc:
        data.initial_state(day)
    assert detail in str(exc.value)


def test_coverage_audit_and_march_cutoff():
    audit = json.loads(C.COVERAGE_AUDIT.read_text())
    assert len(audit["months"]) == 20
    assert audit["days"]["2025-07-17"]["coverage"] == 0
    assert not audit["days"]["2026-08-31"]["complete"]
    assert data.coverage("2026-08-31") == 0
    trips_audit = json.loads(C.TRIPS_AUDIT.read_text())
    assert trips_audit["raw_rows"] == 54_287_936 and trips_audit["kept"] == 54_287_935


def test_days_json():
    js = json.loads(C.DAYS_JSON.read_text())
    sel = js["seleccion"]
    assert len(sel) == 15 and sum(x["type"] == "weekday" for x in sel) == 11
    assert all(x["day"].startswith("2025-08") and x["coverage"] >= .9 for x in sel)
    assert len(js["prueba"]) == 5 and all(v for v in js["prueba"].values())
    assert len(js["curva"]) == 32
    for month in ("2025-09", "2025-10", "2025-11", "2025-12"):
        picked = [x for x in js["curva"] if x["day"].startswith(month)]
        assert len(picked) == 8 and sum(x["type"] == "weekday" for x in picked) == 6
    assert len(js["prod_2026"]) == 7
    assert all(len(v) <= 8 for v in js["prod_2026"].values())
    assert all(x["day"] <= "2026-03-22" for x in js["prod_2026"]["2026-03"])
    assert all(x["coverage"] >= C.COVERAGE_MIN for rows in js["prod_2026"].values() for x in rows)
    assert js["prod_2026"]["2026-07"] == []
    assert len(js["prod_2026"]["2026-08"]) == 3
    assert days.load_days("seleccion") == [x["day"] for x in sel]
