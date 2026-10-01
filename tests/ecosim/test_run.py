"""Tests de ecosim/run.py (integración, run 2): tabla estación × bloque con
dañadas dinámicas, reglas de selección (cota de retiro, λ, h, grid), que nada
se elija en días de evaluación, resumen y humo con datos reales."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from ecosim import config as C
from ecosim import run, sim


def _toy():
    # A: 1 bici, sale a los 70 min y vuelve a los 200. B: fuera de servicio, 0 disponibles y 1 dañada.
    # D: cap 2, 1 disponible + 1 dañada → llena hasta que el taller retira la dañada (minuto 120).
    init = pd.DataFrame({
        "short_name": ["A", "B", "D"], "bikes": [1, 0, 1], "disabled": [0, 1, 1], "docks": [1, 1, 0],
        "docks_disabled": [0, 0, 0], "cap": [2, 2, 2], "out_of_service": [False, True, False],
    })
    nbrs = pd.DataFrame({"short_name": ["A", "B", "A", "D", "B", "D"], "nbr": ["B", "A", "D", "A", "D", "B"],
                         "dist_m": [100.0] * 6})
    start, end = C.day_bounds("2025-09-03")
    s = pd.Timestamp(start)
    trips = pd.DataFrame({
        "bike": ["x"], "o": ["A"], "d": ["A"],
        "t_dep": [s + pd.Timedelta(minutes=70)], "t_arr": [s + pd.Timedelta(minutes=200)],
    })
    ev = pd.DataFrame({"short_name": ["D"], "t": [s + pd.Timedelta(minutes=120)], "kind": ["taller_retiro"],
                       "n": [1]})
    return init, trips, nbrs, start, end, ev


def test_station_blocks_19_blocks_and_dynamic_room():
    init, trips, nbrs, start, end, ev = _toy()
    r = sim.simulate(init, trips, nbrs, start, end, damage_events=ev)
    names, E, F = run.station_blocks(r, init)
    assert E.shape == (19, 3) and F.shape == (19, 3)
    e = pd.DataFrame(E, columns=names)
    f = pd.DataFrame(F, columns=names)
    # A vacía de 06:40 (min 70) a 08:50 (min 200); bloques anclados a 05:30
    assert list(e["A"][:5]) == [0, 50, 60, 20, 0]
    # B: vacía todo el día (1140 min), nunca llena
    assert e["B"].sum() == C.WINDOW_MIN and f["B"].sum() == 0
    # D: llena mientras la dañada ocupa el anclaje (min 0–120); después hay lugar
    assert list(f["D"][:3]) == [60, 60, 0] and f["D"].sum() == 120
    assert int(E.sum()) == r.metrics["E"] and int(F.sum()) == r.metrics["F"]


def test_kneedle_lambda_picks_knee():
    # curva convexa decreciente con codo claro en λ = 30; λ = 120 y 240 no mueven nada (mismo punto)
    curve = {0: (3000, 8000), 5: (2500, 8100), 15: (1800, 8300), 30: (1000, 9000),
             60: (500, 14000), 120: (0, 30000), 240: (0, 30000)}
    c = pd.DataFrame([{"lam": k, "moves": m, "EF": ef} for k, (m, ef) in curve.items()])
    lam, info = run.kneedle_lambda(c)
    assert lam == 30 and info["knee_moves"] == 1000


def test_choose_retiro_lowest_mean_over_grid():
    rows = pd.DataFrame([
        {"retiro": r, "lam": lam, "day": d, "EF": 100 + lam + (-5 if r == "sigma" else 0) + (10 if d == "b" and r == "sigma" and lam == 0 else 0)}
        for r in run.RETIROS for lam in (0, 5) for d in ("a", "b")])
    pick, info = run.choose_retiro(rows)
    # floor: 100, 100, 105, 105 (media 102.5); sigma: 95, 105, 100, 100 (media 100)
    assert pick == "sigma"
    assert info["de"] == 4 and info["gana_en"] == 3


def test_h_stop_rule():
    assert run.h_stop({1: 100.0, 2: 80.0}) is None
    assert run.h_stop({1: 100.0, 2: 80.0, 3: 79.5}) == 3          # 0.6% < 1%
    assert run.h_stop({1: 100.0, 2: 80.0, 3: 79.0}) is None       # 1.25% ≥ 1%
    assert run.h_stop({1: 100.0, 2: 101.0}) == 2                  # empeora


def test_grid_combos_skip_rule():
    c = run.grid_combos(3)
    assert ("ma", 180, 1) not in c and ("model", 180, 1) not in c       # 180 > 60·1 + 60
    assert ("ma", 180, 2) in c                                           # 180 ≤ 60·2 + 60
    assert [x for x in c if x[0] == "daily"] == [("daily", 0, 1), ("daily", 0, 2), ("daily", 0, 3)]
    assert len(c) == 2 * (3 + 3 + 2) + 3


def test_best_configs_tie_break():
    t = pd.DataFrame([
        {"arm": "ma", "f": 15, "H": 3, "EF": 10.0}, {"arm": "ma", "f": 60, "H": 3, "EF": 10.0},
        {"arm": "ma", "f": 60, "H": 2, "EF": 10.0}, {"arm": "ma", "f": 180, "H": 2, "EF": 10.0},
        {"arm": "model", "f": 15, "H": 1, "EF": 9.0}, {"arm": "model", "f": 60, "H": 1, "EF": 12.0},
    ])
    b = run.best_configs(t).set_index("arm")
    assert (b.loc["ma", "f"], b.loc["ma", "H"]) == (180, 2)            # empate: menor H, luego mayor f
    assert (b.loc["model", "f"], b.loc["model", "H"]) == (15, 1)


def test_base_params_use_published_integers(monkeypatch):
    monkeypatch.setattr(run, "stats", lambda: {"tope_hora": 246, "tope_bodega": 632,
                                               "mean_abs_stock_warehouse": 73.958})
    assert run.base_params() == {"tope_hora": 246, "tope_bodega": 632, "max_move": 22}
    assert run.tope_bodega_taller() == 74


def test_summary_counts_wins_vs_ecobici():
    fixed = dict(split="evaluacion", damage="auto", f=np.nan, H=np.nan, L=np.nan, lam=np.nan, mu=np.nan,
                 tope_hora=np.nan, tope_bodega=np.nan, max_move=np.nan, retiro="")
    pol = dict(split="evaluacion", damage="auto", f=60, H=2, L=60, lam=5.0, mu=1.0, tope_hora=246,
               tope_bodega=632, max_move=22, retiro="floor")
    zero = {c: 0 for c in run.SUM_COLS if c != "EF"}
    rows = []
    for d, eco, p in [("d1", 100, 90), ("d2", 100, 120), ("d3", 50, 40)]:
        rows.append({**fixed, "tag": "eval", "arm": "ecobici", "day": d, **zero, "EF": eco})
        rows.append({**pol, "tag": "eval", "arm": "oracle", "day": d, **zero, "EF": p})
    s = run.summary(pd.DataFrame(rows)).set_index("arm")
    assert s.loc["oracle", "gana_a_ecobici"] == 2 and s.loc["ecobici", "gana_a_ecobici"] == 0
    assert s.loc["oracle", "EF"] == pytest.approx(250 / 3) and s.loc["oracle", "n_dias"] == 3


# ----------------------------------------------------------------------
# Orquestación con corridas falsas: nada se elige en días de evaluación
# ----------------------------------------------------------------------

def _fake_ef(s: dict) -> tuple[float, float]:
    """(E+F, movimientos) sintéticos: λ con codo, h que deja de mejorar en 5."""
    if s["arm"] not in run.POLICY_ARMS:
        return 30000.0, 2000.0
    lam = s["lam"]
    moves = 3000 * math.exp(-lam / 40)
    ef = 5000 + 20000 * math.exp(-moves / 600)
    ef *= (1 + 1 / s["H"] ** 3) / 1.25
    ef *= {"oracle": 1.0, "ma": 1.3, "model": 1.25, "daily": 1.28}[s["arm"]]
    ef *= 1 + 0.001 * {0: 0, 15: 1, 60: 0, 180: 2}[s["f"]]
    ef *= 0.97 if s["retiro"] == "sigma" else 1.0
    return ef, moves


def test_selection_only_on_selection_days(tmp_path, monkeypatch):
    calls = []

    def fake_run_many(specs, jobs=1, label="", blocks=False):
        calls.append((label, specs))
        rows = []
        for s in specs:
            ef, mv = _fake_ef(s)
            rows.append({**s, "run": run.spec_id(s), "EF": ef, "E": ef, "F": 0.0, "moves": mv,
                         "bikes_moved": 2 * mv, "recorte_bodega": 0, "fallbacks": 0})
        return pd.DataFrame(rows)

    monkeypatch.setattr(run, "run_many", fake_run_many)
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    monkeypatch.setattr(run, "FROZEN_JSON", tmp_path / "frozen.json")
    monkeypatch.setattr(run, "stats", lambda: {"tope_hora": 246, "tope_bodega": 632, "tope_hora_con_undo": 474,
                                               "mean_abs_stock_warehouse": 73.958})
    monkeypatch.setattr(run, "forecast_horizon_h", lambda: 6)
    sel, ev = set(run.sel_days()), set(run.eval_days())
    assert len(sel) == 15 and len(ev) == 15 and not sel & ev

    fz = run.stage_lambda(jobs=1)
    assert fz["retiro"] == "sigma" and fz["lam"] in run.LAMBDA_GRID
    assert fz["tope_hora"] == 246 and fz["tope_bodega"] == 632 and fz["max_move"] == 22
    fz = run.stage_h(jobs=1)
    assert fz["h_max"] == 5 and fz["h_busqueda"]["para_en"] == {"oracle": 5, "ma": 5}
    fz = run.stage_grid(jobs=1)
    assert fz["best"]["oracle"]["H"] == 5 and fz["best"]["daily"]["f"] == 0
    assert fz["best"]["ma"]["f"] == 60 and fz["best_real"] == "model"
    n_sel = len(calls)
    for label, specs in calls:
        assert {s["day"] for s in specs} <= sel, label
        assert all(s["split"] == "seleccion" for s in specs)
        assert all(s["lam"] == fz["lam"] and s["retiro"] == fz["retiro"] for s in specs if label != "lambda")
    # el grid no pide combinaciones con f > 60·h + L y reutiliza `ma` f60 de la búsqueda de h
    grid = [s for lab, sp in calls if lab == "grid" for s in sp]
    assert all(s["f"] <= 60 * s["H"] + 60 for s in grid if s["arm"] != "daily")
    assert {s["tag"] for s in grid if s["arm"] == "ma" and s["f"] == 60} == {"sel_h"}

    run.stage_eval(jobs=1)
    run.stage_sens(jobs=1)
    for label, specs in calls[n_sel:]:
        assert {s["day"] for s in specs} <= ev, label
        assert all(s["split"] == "evaluacion" for s in specs)
    pol = [s for _, sp in calls[n_sel:] for s in sp if s["arm"] in run.POLICY_ARMS]
    for s in pol:
        b = fz["best"][s["arm"]]
        assert (s["f"], s["H"]) == (b["f"], b["H"])
        if s["tag"] in ("eval", "eval_L"):
            assert (s["lam"], s["mu"], s["retiro"], s["tope_hora"], s["tope_bodega"], s["max_move"]) == \
                (fz["lam"], 1.0, fz["retiro"], 246, 632, 22)
    sens = {s["tag"] for s in pol if s["tag"].startswith(("sens", "diag"))}
    assert {"sens_mm14", "sens_mm42", "sens_tope_con_undo", "sens_danadas_fijas", "sens_bodega_ilimitada",
            "sens_bodega_taller", "sens_mu0", "sens_retiro_floor"} <= sens
    assert {s["arm"] for s in pol if s["tag"] == "sens_mu0"} == {"oracle", "model"}
    assert {s["tope_bodega"] for s in pol if s["tag"] == "sens_bodega_taller"} == {74}
    # λ alternativo: el de menor E+F en la curva de SELECCIÓN de la cota congelada
    c = pd.DataFrame(fz["curva"]).query("retiro == @fz['retiro']")
    assert {s["lam"] for s in pol if s["tag"] == "sens_lam_min_seleccion"} == {float(c.loc[c["EF"].idxmin(), "lam"])}
    assert json.loads((tmp_path / "frozen.json").read_text())["best_real"] == "model"


# ----------------------------------------------------------------------
# Humo con datos reales
# ----------------------------------------------------------------------

DAY = "2025-09-03"
needs_data = pytest.mark.skipif(
    not (C.TRIPS_PARQUET.exists() and (C.SNAPSHOT_DIR / f"{DAY}.parquet").exists()
         and run.P.forecast_path(DAY, "ma", 60).exists() and sim.MOVES_FILES["rebal"].exists()
         and sim.DAMAGE_EVENTS_FILE.exists() and run.OBS_CSV.exists()),
    reason="faltan cachés de ecosim (viajes, snapshots, pronósticos, movimientos o dañadas)",
)


@needs_data
def test_forecast_errors_smoke():
    o = run.forecast_errors("oracle", 60, days=[DAY])
    assert (o["abs_err_net"] == 0).all() and (o["sal_pron"] == o["sal_real"]).all()
    assert set(o.index.get_level_values("block")) == set(range(19))
    m = run.forecast_errors("ma", 60, days=[DAY])
    d = run.forecast_errors("daily", 0, days=[DAY])
    for x in (m, d):
        assert (x["sal_real"].to_numpy() == o["sal_real"].to_numpy()).all()
        assert (x["abs_err_net"] >= 0).all() and x["abs_err_net"].sum() > 0
        assert x["dias_censurado"].between(0, 1).all() and x["dias_censurado"].sum() > 0


@needs_data
def test_v1_day_smoke():
    """Brazos de V1; lo observado + fuera de servicio = ecobici_observado.csv; franjas suman."""
    rows, per = run.v1_day(DAY)
    df = pd.DataFrame(rows).set_index("label")
    assert list(df.index) == [run.v1_label(a, d) for a, d in run.V1_ARMS]
    obs = pd.read_csv(run.OBS_CSV, dtype={"day": str}).set_index("day")
    from ecosim import data, medicion
    init = data.initial_state(DAY)
    oos = set(init.loc[init["out_of_service"], "short_name"])
    s = medicion.state_time(data.snapshots(DAY))
    o_oos = medicion.observed_ef(s[s["short_name"].isin(oos)], DAY)
    main = df.loc[run.MAIN_REPLAY]
    assert main["E_obs"] + o_oos["E"].iloc[0] == pytest.approx(obs.loc[DAY, "E"], abs=0.01)
    assert main["F_obs"] + o_oos["F"].iloc[0] == pytest.approx(obs.loc[DAY, "F"], abs=0.01)
    for c in "EF":
        assert sum(main[f"{c}_obs_{fr}"] for fr in run.FRANJAS) == pytest.approx(main[f"{c}_obs"])
        assert sum(main[f"{c}_{fr}"] for fr in run.FRANJAS) == main[c]
    assert df.loc["baseline|auto", "moves"] == 0 and main["moves"] > 0
    assert main["stock_mae"] < df.loc["baseline|auto", "stock_mae"]
    # dañadas dinámicas siguen a GBFS mejor que las fijas
    assert main["danadas_mae"] < df.loc["ecobici_stock|fixed", "danadas_mae"]
    assert main["danos_aplicados"] > 0 and df.loc["ecobici_stock|fixed", "danos_aplicados"] == 0
    assert (df["E"] <= df["E_all"]).all() and (df["F"] <= df["F_all"]).all()
    assert len(per) == len(run.V1_ARMS) and all(p["block"].nunique() == 19 for p in per)


@needs_data
def test_run_one_policy_smoke():
    """Una corrida de política de punta a punta: reporta fallbacks y recortes, respeta topes."""
    spec = run._pspec(DAY, "evaluacion", "smoke", "ma", 60, 1, 60, 60.0, 1.0, 246, 632, 22, "floor")
    # En un proceso nuevo, como en `run_many`: HiGHS fija su planificador de threads
    # global con el primer solve del proceso, y otros tests ya resolvieron aquí con
    # 4 threads (con threads=1 HiGHS no resolvería y cada decisión sería fallback).
    ex = run.pool(1)
    try:
        row, sb = ex.submit(run.run_one, spec, None, False, True).result()
    finally:
        ex.shutdown()
    assert row["fallbacks"] == 0 and row["non_optimal"] == 0
    for k in ("fallbacks", "recorte_bodega", "recorte_por_movimiento", "non_optimal", "n_decisions"):
        assert k in row and row[k] >= 0
    assert row["n_decisions"] == 72 and row["recorte_por_movimiento"] == 0
    assert -632 <= row["bodega_min"] <= row["bodega_max"] <= 632
    assert sb["block"].nunique() == 19 and int(sb["E"].sum()) == row["E_all"]
    assert row["EF_manana"] + row["EF_tarde"] + row["EF_noche"] == row["EF"]
    assert all(row[k] == round(row[k], 3) for k in run.WALK_COLS)


def test_policy_is_deterministic_config():
    """Sin corte por reloj de pared: el límite de tiempo de HiGHS no debe decidir la solución."""
    a = run.make_policy("sigma")
    assert a.threads == 1 and a.retiro == "sigma"
    # la decisión más lenta del run 2 tardó 361 s (λ = 5): el límite deja margen
    assert a.time_limit == run.HIGHS_TIME_LIMIT >= 600


def test_non_optimal_fails_loudly():
    run.check_optimal({"run": "x", "non_optimal": 0})
    with pytest.raises(RuntimeError, match="no óptimas"):
        run.check_optimal({"run": "x", "non_optimal": 2})


def test_pool_children_single_thread(monkeypatch):
    import os
    for k in run.CHILD_THREAD_ENV:
        monkeypatch.setenv(k, "4")
    ex = run.pool(1)
    try:
        assert all(os.environ[k] == "1" for k in run.CHILD_THREAD_ENV)
        assert ex.submit(os.getenv, "OMP_NUM_THREADS").result() == "1"
    finally:
        ex.shutdown()
