"""Contrato run 3: cuartos, bordes, bloques gemelos, causalidad y cortes."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from ecosim import config as C, contracts as K, pronostico as P

UNI = ["001", "002", "003"]


def fixture():
    days = [date(2025, 1, 1) + timedelta(days=j) for j in range(80)]
    counts = np.random.default_rng(42).poisson(1.2, (80, 3, P.NB15, 2)).astype(np.float32)
    return days, counts, 65


def t(day, slot):
    return C.day_bounds(day)[0] + timedelta(minutes=15*slot)


@pytest.fixture(scope="module")
def trained():
    days, counts, i = fixture()
    return days, counts, i, P.train(counts, days, days[0], days[45], False), P.train(counts, days, days[0], days[45], True)


def models(trained, name, counts=None):
    days, old, i, daily, direct = trained
    return P.forecast(name, old if counts is None else counts, days, days[i],
                      daily if name == "lgbm_diario" else direct if name == "lgbm_directo" else None)


@pytest.mark.parametrize("name", ["ma_diaria", "lgbm_diario", "oraculo_diario", "lgbm_directo", "oraculo_directo"])
def test_forma_y_borde(trained, name):
    days, _, i, _, _ = trained
    f = models(trained, name)
    assert isinstance(f, K.ForecastTable)
    assert f.forma == ("directa" if name.endswith("directo") else "diaria")
    for slot in (0, 1, 44, P.NB15-2, P.NB15-1):
        table = K.validate_forecast_table(f, t(days[i], slot), 6, 3)
        assert table.shape == (3, 24, 2)
        assert (table[:, P.NB15-slot:] == 0).all()
    with pytest.raises(ValueError):
        f.table(t(days[i], P.NB15), 6)


@pytest.mark.parametrize("name", ["ma_diaria", "lgbm_diario", "oraculo_diario", "lgbm_directo", "oraculo_directo"])
def test_causalidad_cinco_variantes(trained, name):
    """El oráculo es copia congelada: no puede ser causal si se recalcula la verdad."""
    days, counts, i, _, _ = trained
    f = models(trained, name)
    slot = 15
    before = f.table(t(days[i], slot), 4)
    changed = counts.copy()
    changed[i, :, slot:] += 100
    changed[i+1:] += 100
    if name.startswith("oraculo"):
        # En producción la verdad del oráculo es un objeto sellado al crear el brazo.
        f.counts = changed
        after = f.table(t(days[i], slot), 4)
    else:
        after = models(trained, name, changed).table(t(days[i], slot), 4)
    np.testing.assert_array_equal(before, after)


def test_se_detecta_fuga_y_se_usa_pasado(trained):
    days, counts, i, _, _ = trained
    f = models(trained, "lgbm_directo")
    a = f.table(t(days[i], 24), 2)
    changed = counts.copy()
    changed[i, :, 23] += 90
    b = models(trained, "lgbm_directo", changed).table(t(days[i], 24), 2)
    assert not np.array_equal(a, b)
    changed2 = counts.copy()
    changed2[i-7] += 90
    assert not np.array_equal(models(trained, "ma_diaria").table(t(days[i], 0), 2),
                              models(trained, "ma_diaria", changed2).table(t(days[i], 0), 2))


@pytest.mark.parametrize("shape", ["diario", "directo"])
def test_oraculo_gemelo_mismos_bloques(trained, shape):
    days, _, i, _, _ = trained
    oracle = models(trained, "oraculo_" + shape)
    model = models(trained, "lgbm_" + shape)
    for slot in (0, 1, 7, P.NB15 - 5):
        a, b = oracle.table(t(days[i], slot), 6), model.table(t(days[i], slot), 6)
        assert a.shape == b.shape == (3, 24, 2)
        assert np.array_equal(np.flatnonzero(a.sum((0, 2)) > 0), np.flatnonzero(b.sum((0, 2)) > 0))
        if shape == "directo":
            for k in range(6):
                lo, hi = slot+4*k, min(slot+4*k+4, P.NB15)
                if lo < hi:
                    np.testing.assert_allclose(a[:,4*k:4*k+hi-lo].sum(axis=1),
                                               trained[1][i, :, lo:hi].sum(axis=1))


def test_ultima_media_hora_y_reloj(trained):
    days, counts, i, _, _ = trained
    f = models(trained, "oraculo_diario")
    actual = counts[i, :, 76:78].sum(axis=1)
    np.testing.assert_allclose(f.table(t(days[i], 76), 1)[:, 0], actual/2)
    np.testing.assert_allclose(f.table(t(days[i], 77), 1)[:, 0], actual/2)
    assert (f.table(t(days[i], 77), 1)[:, 1:] == 0).all()


def test_rotacion_cubre_todos_los_ticks():
    inicio = date(2025, 1, 1)
    sampled = [P._ticks(True, inicio + timedelta(days=i)) for i in range(24)]
    assert all(len(np.flatnonzero([t in ticks for ticks in sampled])) == 2
               for t in range(P.NB15))
    assert all(np.all(np.diff(ticks) == 12) for ticks in sampled)
    assert np.array_equal(P._ticks(True, inicio), P._ticks(True, inicio + timedelta(days=12)))
    assert np.array_equal(P._ticks(False), [0])


def test_load_counts_rango_2025_sin_marzo_2026(tmp_path, monkeypatch):
    d = date(2025, 9, 3)
    start = C.day_bounds(d)[0]
    parquet = tmp_path / "trips.parquet"
    pd.DataFrame([{"o": "001", "d": "002", "t_dep": start,
                   "t_arr": start + timedelta(minutes=30)}]).to_parquet(parquet)
    monkeypatch.setattr(C, "TRIPS_PARQUET", parquet)
    days, universe, counts = P.load_counts(d, d)
    assert days == [d] and universe == ["001", "002"]
    assert counts.shape == (1, 2, P.NB15, 2)
    assert counts[0, 0, 0, P.DEP] == 1
    assert counts[0, 1, 2, P.ARR] == 1


def test_conteos_llegadas_cruzan_medianoche():
    d = date(2025, 9, 3)
    start = C.day_bounds(d)[0]
    tr = pd.DataFrame([("a", "001", "002", start, start+timedelta(minutes=30)),
                       ("b", "002", "001", start+timedelta(hours=18, minutes=55),
                        start+timedelta(hours=19, minutes=10)),
                       ("c", "003", "001", start+timedelta(hours=19,minutes=25),
                        start+timedelta(hours=19,minutes=40))],
                      columns=["bike", "o", "d", "t_dep", "t_arr"])
    counts = P.count_trips(tr, UNI, [d], np.array([True, False, True]))[0]
    assert counts[:, :, P.DEP].sum() == 2
    assert counts[:, :, P.ARR].sum() == 2
    assert counts[0, 76, P.ARR] == 1


def test_entrenamiento_solo_corte(monkeypatch):
    days, counts, i = fixture()
    got = []
    original = P._features
    def spy(c, ds, j, kind, ticks, direct, labels=True):
        if labels:
            got.append(j)
        return original(c, ds, j, kind, ticks, direct, labels)
    monkeypatch.setattr(P, "_features", spy)
    P.train(counts, days, days[0], days[35], False)
    assert min(got) == 28 and max(got) == 35
