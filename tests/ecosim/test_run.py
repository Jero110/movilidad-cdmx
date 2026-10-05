"""Selección y ejecución del run 3; sin hacer un experimento largo durante pytest."""
import json

import pandas as pd
import pytest

from ecosim import config as C, run, sim


def test_seven_arms_and_splits():
    assert len(run.ARMS) == 7
    assert len(run.days("seleccion")) == 15
    assert len(run.days("curva")) == 32
    assert len(set(run.days("seleccion")) & set(run.days("prueba"))) == 0
    assert all(not "2026-03-23" <= d <= "2026-03-31" for d in run.days("prod_2026"))


def test_unique_resume_keys_and_limits():
    a = run.spec("2025-08-01", "oraculo_directo", "n", 2, 30)
    assert run.key(a) != run.key({**a, "lam": 60})
    assert run.cap() == {"visitas_por_decision": 67, "bicis_por_visita": 14}
    assert run.cap("adelante") == {"visitas_por_decision": 47, "bicis_por_visita": 19}
    assert run.cap("p99") == {"visitas_por_decision": 83, "bicis_por_visita": 24}
    with pytest.raises(ValueError, match="dos procesos"):
        run.execute([a], procesos=8, threads=2)


def test_paired_confidence_interval_ties():
    a = pd.Series([10., 10., 30.], index=["a", "b", "c"])
    b = pd.Series([10., 20., 20.], index=["a", "b", "c"])
    center, lo, hi, wins, ties = run.paired(a, b)
    assert center == 0 and lo < 0 < hi and (wins, ties) == (1, 1)


def test_n_selection_never_reads_test_days(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    monkeypatch.setattr(run, "N_FILE", tmp_path / "n.csv")
    monkeypatch.setattr(run, "FROZEN", tmp_path / "frozen.json")
    calls = []

    def fake_execute(specs, *_):
        calls.extend(specs)
        return pd.DataFrame([{**s, "run": run.key(s), "E": 1000 if s["n"] == 0 else 100,
                              "F": 0, "EF": 1000 if s["n"] == 0 else 100,
                              "visitas": 0 if s["n"] == 0 else 10} for s in specs])

    monkeypatch.setattr(run, "execute", fake_execute)
    t = run.paso1(1, 1)
    assert len(t) == 2*3*6 and t[t.n == 1].n1_sin_resolver.all()
    assert {s["day"] for s in calls} == set(run.days("seleccion"))
    frozen = json.loads((tmp_path / "frozen.json").read_text())
    assert frozen["n"] == {"diaria": 2, "directa": 2}
    assert frozen["n_ci_promedio_lambda"]["oraculo_diario"]["2"]["IC95_inf"] == 0


@pytest.mark.skipif(not C.TRIPS_PARQUET.exists() or not sim.MOVES_FILE.exists(), reason="sin datos derivados")
def test_n1_equals_no_rebalancing_real_day():
    d = run.days("seleccion")[0]
    base = run.one(run.spec(d, "sin_rebalanceo", "smoke"))
    for arm in ("oraculo_diario", "oraculo_directo"):
        policy = run.one(run.spec(d, arm, "smoke", n=1, lam=30))
        assert (policy["E"], policy["F"], policy["visitas"]) == (base["E"], base["F"], 0)
        assert policy["truck_end"] == 0


@pytest.mark.skipif(not C.TRIPS_PARQUET.exists() or not sim.MOVES_FILE.exists(), reason="sin datos derivados")
def test_seven_arms_execute_and_resume_real_day(tmp_path, monkeypatch):
    """Prueba el camino real pool → CSV → reanudación en los siete brazos."""
    monkeypatch.setattr(run, "RUNS", tmp_path / "corridas.csv")
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    day = run.days("seleccion")[0]
    specs = [run.spec(day, arm, "smoke_run3", n=2, lam=30) for arm in run.ARMS]
    first = run.execute(specs, procesos=2, threads=1)
    assert set(first.arm) == set(run.ARMS)
    assert len(run.saved()) == 7
    assert run.execute(specs, procesos=2, threads=1).run.tolist() == first.run.tolist()
    for row in first[first.arm.isin(run.POLICIES)].itertuples():
        assert row.recogidas_aplicadas == row.entregadas_aplicadas
        assert row.truck_end == 0
        assert row.max_bicis_visita <= 14 and row.max_visitas_decision <= 67


def test_tables_with_synthetic_test_days(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    monkeypatch.setattr(run, "RUNS", tmp_path / "runs.csv")
    monkeypatch.setattr(run, "FINAL", tmp_path / "resultados.csv")
    monkeypatch.setattr(run, "FROZEN", tmp_path / "frozen.json")
    frozen = {"mejor_real": "ma_diaria", "lambda_por_brazo": {a: {"lambda": 30,
              "visitas_menos_igual_resultado_pct": 10} for a in run.POLICIES}}
    run.save_frozen(frozen)
    rows = []
    for day in ("2025-09-01", "2025-09-02"):
        for arm in run.ARMS:
            ef = 200 if arm == "sin_rebalanceo" else 100 if arm == "ecobici" else 70 if arm.startswith("oraculo") else 80
            rows.append({"day":day, "arm":arm, "tag":"prueba", "E":ef, "F":0, "EF":ef,
                         "visitas":50, "bicis_movidas":100, "desvios_salida":1,
                         "desvios_llegada":2, "km_desvio_medio":.1, "replay_neto":0,
                         "decision_mediana_s":.2, "decision_p95_s":.4,
                         "decision_max_s":.5, "decisiones_limite_10s":0,
                         "tiempos_decision":"[0.2, 0.4]", "damage_external":0,
                         "lam":30, "run":day+arm})
    pd.DataFrame(rows).to_csv(run.FINAL, index=False)
    prod = [{**r, "day":"2026-02-01", "tag":"prod_2026", "run":"prod"+r["arm"]}
            for r in rows[:7] if r["arm"] != "ecobici"]
    pd.DataFrame(rows + prod).to_csv(run.RUNS, index=False)
    run.tablas()
    assert "Siete brazos" in (tmp_path / "tablas.md").read_text()
    assert "ma_diaria" in (tmp_path / "CONCLUSIONES.md").read_text()


def test_benchmark_pair_rules_do_not_influence_policy():
    a = run.spec("2025-09-03", "oraculo_directo", "sens_pares", 3, 30)
    assert a["pairs"] == "todas"
    assert run.key(run.spec("2025-09-03", "ecobici", "sens_pares", pairs="sin_regla")) != run.key(a)
