"""Selección y ejecución del run 3; sin hacer un experimento largo durante pytest."""
import json

import pandas as pd
import pytest

from ecosim import config as C, run, sim


def test_seven_arms_and_splits():
    assert len(run.ARMS) == 7
    sel, test = run.days("seleccion"), run.days("prueba")
    # Validación = todo agosto 2025 válido; prueba = septiembre–diciembre 2025, sin 2026.
    assert sel == sorted(sel) and all(d.startswith("2025-08") for d in sel) and len(sel) == 31
    assert len(test) == 121 and (test[0], test[-1]) == ("2025-09-01", "2025-12-31")
    assert len(run.days("curva")) == 32
    assert len(set(sel) & set(test)) == 0
    with pytest.raises(ValueError):
        run.days("prod_2026")
    assert not hasattr(run, "paso6")
    with pytest.raises(SystemExit):  # sin paso 6 (producción 2026)
        run.main(["--paso", "6"])


def test_unique_resume_keys_and_limits():
    a = run.spec("2025-08-01", "oraculo_directo", "n", 2, 30)
    assert run.key(a) != run.key({**a, "lam": 60})
    # Topes duros: p95 de Ecobici en todo 2025; sin sensibilidades de topes.
    assert run.cap() == {"visitas_por_decision": 55, "bicis_por_visita": 17}
    for source in ("adelante", "p99"):
        with pytest.raises(ValueError):
            run.cap(source)
    with pytest.raises(ValueError, match="cinco procesos"):
        run.execute([a], procesos=6, threads=1)
    with pytest.raises(SystemExit):
        run.main(["--paso", "1", "--procesos", "6"])


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
        assert row.max_bicis_visita <= 17 and row.max_visitas_decision <= 55


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
                         "decision_max_s":.5,
                         "tiempos_decision":"[0.2, 0.4]", "damage_external":0,
                         "lam":30, "run":day+arm})
    pd.DataFrame(rows).to_csv(run.FINAL, index=False)
    pd.DataFrame(rows).to_csv(run.RUNS, index=False)
    run.tablas()
    tables = (tmp_path / "tablas.md").read_text()
    conclusions = (tmp_path / "CONCLUSIONES.md").read_text()
    assert "Siete brazos" in tables and "ma_diaria" in conclusions
    assert "2026" not in tables + conclusions and "limite_10s" not in tables


def test_benchmark_pair_rules_do_not_influence_policy():
    a = run.spec("2025-09-03", "oraculo_directo", "sens_pares", 3, 30)
    assert a["pairs"] == "todas"
    assert run.key(run.spec("2025-09-03", "ecobici", "sens_pares", pairs="sin_regla")) != run.key(a)
