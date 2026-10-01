"""Tests de ecosim.live con datos sintéticos (sin red ni datos reales)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ecosim import config as C
from ecosim import contracts as K
from ecosim import live

DAY = date(2026, 9, 29)                      # martes
DAY0 = datetime.combine(DAY, C.DAY_START)    # 05:30
NAMES = ["001", "002", "003", "004"]
CAP = {"001": 10, "002": 12, "003": 8, "004": 15}

FZ = {"best": {"ma": {"f": 15, "H": 3}, "daily": {"f": 0, "H": 5}}, "L": 60, "lam": 5.0, "mu": 1.0,
      "retiro": "floor", "tope_hora": 50, "tope_bodega": 100, "max_move": 22, "best_real": "daily"}


def _profile():
    """Perfil `ma` sintético: 002 pierde bicis, 004 gana, por bin de 15 min."""
    base = np.zeros((len(NAMES), live.NB15, 2))
    base[1, :, 0] = 1.5      # 002: salidas
    base[3, :, 1] = 1.5      # 004: llegadas
    base[0, :, :] = 0.3
    meta = {"tipo_dia": "entre semana", "rango": ["2026-08-03", "2026-08-30"],
            "dias_usados": ["2026-08-28"], "ultimo_dia_publicado": "2026-08-30", "fallback": True,
            "nota": "sintético", "archivos": []}
    return base, meta


def _log(t_end: datetime, every_min: int = 1, start: datetime | None = None, rng_seed: int = 0):
    """Registro sintético minuto a minuto: 002 va perdiendo bicis, 004 ganando."""
    rng = np.random.default_rng(rng_seed)
    rows = []
    t = start or DAY0 - timedelta(minutes=5)
    b = {"001": 5, "002": 9, "003": 0, "004": 3}
    while t <= t_end:
        for sn in NAMES:
            if sn == "002" and rng.random() < 0.2 and b[sn] > 0:
                b[sn] -= 1
            if sn == "004" and rng.random() < 0.2 and b[sn] < CAP[sn]:
                b[sn] += 1
            rows.append((t, sn, b[sn], 0, CAP[sn] - b[sn], CAP[sn], 1))
        t += timedelta(minutes=every_min)
    return pd.DataFrame(rows, columns=["t", "short_name", "bikes", "disabled", "docks", "cap", "ok"])


def _same_run(a: dict, b: dict):
    def clean(v, k):
        return {x: y for x, y in v.items() if x != "solve_s"} if k == "plan" and v else v   # tiempo de reloj
    for k in ("forecast", "orders", "projection", "state", "state_now", "plan"):
        assert (json.dumps(clean(a.get(k), k), sort_keys=True, default=str)
                == json.dumps(clean(b.get(k), k), sort_keys=True, default=str)), k


def test_causalidad_recomendacion_solo_usa_datos_hasta_t():
    """La corrida en t da lo mismo aunque cambie todo lo que el feed diga después de t."""
    t = DAY0 + timedelta(hours=3)                          # 08:30
    full = _log(t + timedelta(hours=4))
    future = full["t"] > t
    other = full.copy()
    other.loc[future, "bikes"] = 0                         # el futuro cambia por completo
    other.loc[future, "docks"] = other.loc[future, "cap"]
    other.loc[future, "ok"] = 0
    extra = _log(t + timedelta(hours=4), rng_seed=99)
    extra = extra[extra["t"] > t]
    other = pd.concat([other, extra.assign(short_name="002")], ignore_index=True)
    r1 = live.run_at(t, "ma", log=full, fz=FZ, profile=_profile())
    r2 = live.run_at(t, "ma", log=other, fz=FZ, profile=_profile())
    r0 = live.run_at(t, "ma", log=full[~future], fz=FZ, profile=_profile())
    _same_run(r1, r2)
    _same_run(r1, r0)
    assert r1["orders"], "el caso sintético debería recomendar algo"
    # el estado es la última lectura ≤ t
    assert pd.Timestamp(r1["state"]["t_feed"]) <= pd.Timestamp(t)
    # las órdenes se hacen efectivas en t + L
    assert r1["plan"]["effective_at"] == (t + timedelta(minutes=60)).isoformat(timespec="minutes")


def test_historia_anterior_al_dia_de_la_corrida():
    t = DAY0 + timedelta(hours=1)
    base, meta = _profile()
    meta = dict(meta, dias_usados=[DAY.isoformat()])       # historia con el propio día: prohibido
    with pytest.raises(AssertionError):
        live.run_at(t, "ma", log=_log(t), fz=FZ, profile=(base, meta))


def test_estado_sin_lecturas_previas_falla():
    t = DAY0 + timedelta(hours=1)
    lg = _log(t + timedelta(hours=1), start=t + timedelta(minutes=1))
    with pytest.raises(ValueError):
        live.run_at(t, "ma", log=lg, fz=FZ, profile=_profile())


def test_model_no_disponible_en_vivo():
    t = DAY0 + timedelta(hours=1)
    with pytest.raises(NotImplementedError):
        live.run_at(t, "model", log=_log(t), fz=FZ, profile=_profile())


def test_fuera_de_horario_no_recomienda():
    t = datetime.combine(DAY, datetime.min.time()) + timedelta(hours=3)      # 03:00
    lg = _log(t, start=t - timedelta(minutes=10))
    r = live.run_at(t, "ma", log=lg, fz=FZ, profile=_profile())
    assert "forecast" not in r and "nota" in r


def test_infer_flows_y_camion():
    t0 = DAY0 + timedelta(minutes=1)
    rows = [(t0, "001", 5), (t0 + timedelta(minutes=1), "001", 3),         # −2 → 2 salidas
            (t0 + timedelta(minutes=2), "001", 4),                          # +1 → 1 llegada
            (t0 + timedelta(minutes=3), "001", 12),                         # +8 → camión
            (t0 + timedelta(minutes=20), "001", 11)]                        # −1 en el bin 1
    lg = pd.DataFrame([(t, s, b, 0, 20 - b, 20, 1) for t, s, b in rows],
                      columns=["t", "short_name", "bikes", "disabled", "docks", "cap", "ok"])
    c, info = live.infer_flows(lg, ["001"], DAY0, DAY0 + timedelta(hours=1))
    assert c[0, 0, 0] == 2 and c[0, 0, 1] == 1
    assert c[0, 1, 0] == 1
    assert info["camion_n"] == 1 and info["camion_bicis"] == 8
    assert c.sum() == 4


def test_correccion_sin_cobertura_es_daily():
    """Sin registro de la mañana no hay corrección: `ma` = `daily` (factor 1)."""
    t = DAY0 + timedelta(hours=4)
    lg = _log(t, start=t - timedelta(minutes=3))           # solo 3 min de registro
    r_ma = live.run_at(t, "ma", log=lg, fz=FZ, profile=_profile())
    r_d = live.run_at(t, "daily", log=lg, fz=FZ, profile=_profile())
    assert r_ma["fuente"]["inferidos_hoy"]["bins_cubiertos"] == 0
    np.testing.assert_allclose(np.array(r_ma["forecast"]["dep"])[:, 1:], np.array(r_d["forecast"]["dep"])[:, 1:])


def test_correccion_con_cobertura_cambia_el_pronostico():
    t = DAY0 + timedelta(hours=4)
    lg = _log(t)
    r = live.run_at(t, "ma", log=lg, fz=FZ, profile=_profile())
    assert r["fuente"]["inferidos_hoy"]["bins_cubiertos"] == 16
    base = _profile()[0]
    # 001: el perfil dice 0.3 por bin y el registro no ve viajes → factor < 1
    assert np.array(r["forecast"]["dep"])[0, 2] < base[0, 0, 0] * 4


def test_forecast_frame_cumple_contrato():
    t = DAY0 + timedelta(hours=2, minutes=7)
    full = np.ones((len(NAMES), live.N60, 2))
    df = live.forecast_frame(t, NAMES, full, "ma", 15)
    K.validate_forecast(df)
    assert df["block_start"].min() == pd.Timestamp(DAY0 + timedelta(hours=2))
    assert (df["issued_at"] == pd.Timestamp(t)).all()


def test_trip_files_excluye_diciembre_sellado(tmp_path):
    for n in ["2025-11.csv", "2025-12.csv", "2026-01.csv", "public_data_web_2026-08_2.csv", "notas.csv"]:
        (tmp_path / n).write_text("x\n")
    names = [p.name for _, p in live.trip_files(tmp_path)]
    assert "2025-12.csv" not in names
    assert names == ["2025-11.csv", "2026-01.csv", "public_data_web_2026-08_2.csv"]


def _write_trips_csv(path, days, per_day=3):
    lines = ["Genero_Usuario,Edad_Usuario,Bici,Ciclo_Estacion_Retiro,Fecha_Retiro,Hora_Retiro,"
             "Ciclo_EstacionArribo,Fecha_Arribo,Hora_Arribo"]
    for d in days:
        for k in range(per_day):
            dep = datetime.combine(d, datetime.min.time()) + timedelta(hours=8, minutes=5 * k)
            arr = dep + timedelta(minutes=10)
            lines.append(f"M,30,{k},001,{dep:%d/%m/%Y},{dep:%H:%M:%S},002,{arr:%d/%m/%Y},{arr:%H:%M:%S}")
    last = datetime.combine(days[-1] + timedelta(days=1), datetime.min.time()) + timedelta(hours=1)
    lines.append(f"M,30,99,003,{last - timedelta(minutes=5):%d/%m/%Y},{last - timedelta(minutes=5):%H:%M:%S},"
                 f"003,{last:%d/%m/%Y},{last:%H:%M:%S}")
    path.write_text("\n".join(lines) + "\n")


def test_history_profile_fallback_y_normal(tmp_path):
    days = [date(2026, 8, 1) + timedelta(days=k) for k in range(31)]
    _write_trips_csv(tmp_path / "public_data_web_2026-08.csv", days)
    # corrida el 29-sep: no hay las 4 semanas previas → fallback a las que terminan el 31-ago
    prof, meta = live.history_profile(date(2026, 9, 29), NAMES, raw_dir=tmp_path, cache_dir=tmp_path / "c")
    assert meta["fallback"] is True
    assert meta["rango"][1] == "2026-08-31"
    assert all(date.fromisoformat(d) < date(2026, 9, 29) for d in meta["dias_usados"])
    assert all(date.fromisoformat(d).weekday() < 5 for d in meta["dias_usados"])     # martes → entre semana
    # 3 salidas de 001 por día a las 08:00–08:15 (bin 10)
    assert prof[0, 10, 0] == pytest.approx(3.0)
    # corrida dentro de los datos: 4 semanas previas, sin fallback, nunca el propio día
    prof2, meta2 = live.history_profile(date(2026, 8, 30), NAMES, raw_dir=tmp_path, cache_dir=tmp_path / "c")
    assert meta2["fallback"] is False and meta2["rango"][1] == "2026-08-29"
    assert all(d < "2026-08-30" for d in meta2["dias_usados"])


def test_evaluate_compara_contra_lo_inferido(tmp_path):
    t = DAY0 + timedelta(hours=3)
    lg = _log(t + timedelta(hours=5))
    run = live.run_at(t, "ma", log=lg, fz=FZ, profile=_profile())
    live.save_run(run, tmp_path / "runs")
    logdir = tmp_path / "gbfs"
    logdir.mkdir()
    with (logdir / f"{DAY.isoformat()}.jsonl").open("w") as fh:
        for tt, g in lg.groupby("t"):
            fh.write(json.dumps({"t": tt.isoformat(timespec="seconds"),
                                 "s": {r.short_name: [int(r.bikes), int(r.disabled), int(r.docks), int(r.cap), int(r.ok)]
                                       for r in g.itertuples()}}) + "\n")
    ev = live.evaluate(tmp_path / "runs", logdir, now=t + timedelta(hours=5))
    assert ev["corridas_evaluadas"] == 1
    leads = {x["lead_h"]: x for x in ev["por_antelacion"]}
    assert set(leads) == {0, 1, 2, 3, 4}                     # bloques 08:30 … 12:30, ya terminados a las 13:30
    # MAE recalculado a mano para el bloque de antelación 0 (08:30–09:30 = bloque 3 desde 05:30)
    obs, _ = live.infer_flows(lg, NAMES, DAY0, t + timedelta(hours=5))
    k = 3
    o = obs[:, 4 * k:4 * k + 4, 0].sum(axis=1)
    j = run["forecast"]["block_start"].index((DAY0 + timedelta(hours=k)).isoformat())
    p = np.array([row[j] for row in run["forecast"]["dep"]])
    assert leads[0]["mae_salidas"] == pytest.approx(np.abs(p - o).mean())
    assert ev["stock"] and ev["EF"]


def test_diciembre_2025_se_excluye_explicito_y_con_log(tmp_path, caplog):
    """Una ventana del `ma` que toca diciembre 2025 no lo usa: el archivo no se
    lee, los días quedan en `excluidos_sellados` y se registra en el log."""
    live._logged_sealed.clear()
    nov = [date(2025, 11, 1) + timedelta(days=k) for k in range(30)]
    _write_trips_csv(tmp_path / "2025-11.csv", nov)
    (tmp_path / "2025-12.csv").write_text("esto no se debe leer\n")
    jan = [date(2026, 1, 1) + timedelta(days=k) for k in range(20)]
    _write_trips_csv(tmp_path / "2026-01.csv", jan)
    with caplog.at_level("WARNING", logger="ecosim.live"):
        prof, meta = live.history_profile(date(2026, 1, 10), NAMES, raw_dir=tmp_path, cache_dir=tmp_path / "c")
    assert "2025-12.csv" in caplog.text and "sellado" in caplog.text
    assert meta["excluidos_sellados"] and all(d.startswith("2025-12") for d in meta["excluidos_sellados"])
    assert not any(d.startswith("2025-12") for d in meta["dias_usados"])
    assert "2025-12.csv" not in meta["archivos"]


def test_default_es_daily_y_poll_toma_el_lock(tmp_path, monkeypatch):
    assert live.DEFAULT_MODEL == "daily"
    snaps = []

    def fake():
        snaps.append(1)
        return {"last_updated": None, "stations": []}
    loop = live.LiveLoop(snapshot_fn=fake)
    assert loop.model == "daily"
    monkeypatch.setattr(live, "LOG_DIR", tmp_path)
    with loop.lock:                     # run_now toma el lock y llama a poll: debe ser reentrante
        loop.poll()
    assert snaps == [1] and len(list(tmp_path.glob("*.jsonl"))) == 1
