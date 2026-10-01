"""Tests de ecosim/pronostico (run 2): oracle, causalidad por emisión, corrección, daily y contrato."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ecosim import config as C
from ecosim import contracts as K
from ecosim import pronostico as P

UNI = ["001", "002", "003"]


def _t(day, hm, plus_day=0):
    return datetime.combine(day + timedelta(days=plus_day), datetime.strptime(hm, "%H:%M").time())


def _bin(hm, plus_day=0):
    """Bin de 15 min de una hora dentro de la ventana."""
    base = _t(date(2025, 9, 3), "05:30")
    t = _t(date(2025, 9, 3), hm, plus_day)
    return int((t - base) / pd.Timedelta(minutes=15))


def test_oracle_reproduce_fixture():
    d = date(2025, 9, 3)
    rows = [  # bike, o, d, t_dep, t_arr
        ("b1", "001", "002", _t(d, "05:30"), _t(d, "05:50")),
        ("b2", "001", "003", _t(d, "06:10"), _t(d, "06:40")),
        ("b3", "002", "001", _t(d, "05:00"), _t(d, "07:05")),        # salió antes: solo llegada
        ("b4", "003", "001", _t(d, "23:50"), _t(d, "00:10", 1)),     # cruza medianoche: ambos en la ventana de d
        ("b5", "003", "999", _t(d, "08:00"), _t(d, "08:10")),        # llegada a desconocida: no cuenta
        ("b6", "003", "001", _t(d, "05:29"), _t(d, "05:59")),        # salida antes de 05:30 no cuenta
        ("b7", "002", "001", _t(d, "00:20", 1), _t(d, "00:40", 1)),  # sale 00:20 (ventana d), llega 00:40 (fuera)
        ("b8", "002", "003", _t(d, "00:45", 1), _t(d, "01:00", 1)),  # fuera
    ]
    tr = pd.DataFrame(rows, columns=["bike", "o", "d", "t_dep", "t_arr"])
    c = P.count_trips(tr, UNI, [d])[0]                               # [estación, 76, tipo]
    assert c.shape == (3, P.NB15, 2)
    assert c[0, _bin("05:30"), P.DEP] == 1 and c[0, _bin("06:10"), P.DEP] == 1
    assert c[1, _bin("05:45"), P.ARR] == 1
    assert c[2, _bin("06:30"), P.ARR] == 1
    assert c[2, _bin("23:45"), P.DEP] == 1                           # b4 sale 23:50
    assert c[0, _bin("00:00", 1), P.ARR] == 1                        # b4 llega 00:10 → bin 00:00, ventana de d
    assert c[1, _bin("00:15", 1), P.DEP] == 1                        # b7 sale 00:20
    assert c[0, _bin("07:00"), P.ARR] == 1 and c[0, _bin("05:45"), P.ARR] == 1
    assert c[:, :, P.DEP].sum() == 5 and c[:, :, P.ARR].sum() == 5
    # días contiguos: los viajes de 00:00–00:30 van a la ventana anterior
    c2 = P.count_trips(tr, UNI, [d, d + timedelta(days=1)])
    assert c2[1].sum() == 0
    # frame oracle validado
    out = P.build_day(d, UNI, np.repeat(c[None], 40, 0), [d - timedelta(days=39 - k) for k in range(40)],
                      _StubModel(), c, write=False)
    df = out[("oracle", 60)]
    r = df[(df.short_name == "001") & (df.block_start == _t(d, "05:30"))].iloc[0]
    assert r.departures == 2 and r.arrivals == 1                     # b1 05:30 + b2 06:10; b6 llega 05:59


class _StubModel:
    S = len(UNI)

    def predict(self, counts, days, i, valid=None):
        return np.full((self.S, P.N60, 2), 2.0)


def _synth(n=80, seed=0):
    rng = np.random.default_rng(seed)
    days = [date(2025, 1, 1) + timedelta(days=k) for k in range(n)]
    counts = rng.poisson(1.0, size=(n, len(UNI), P.NB15, 2)).astype(np.float32)
    return days, counts


def _emissions(variant, model, counts, days, i, sb):
    base = P.base15(variant, counts, days, i, model)
    return P.emit_day(variant, base, counts[i], sb)


def _assert_causal(fn, days, counts, i, sb):
    """La emisión en el bin `sb` no cambia si se alteran viajes del día en o
    después de s ni los de días posteriores. Falla si `fn` filtra el futuro."""
    ref = fn(counts, days, i, sb)
    c2 = counts.copy()
    c2[i, :, sb:] = np.random.default_rng(9).poisson(40, size=c2[i, :, sb:].shape)
    c2[i + 1:] = np.random.default_rng(10).poisson(40, size=c2[i + 1:].shape)
    np.testing.assert_array_equal(ref, fn(c2, days, i, sb))


def test_causalidad_por_emision_ma_y_model():
    days, counts = _synth()
    i = 60
    model = P.Model(counts, days, days[40], len(UNI))
    for sb in (0, 1, 8, 40, 75):
        for v in ("ma", "model"):
            _assert_causal(lambda c, d, ii, s, v=v: _emissions(v, model, c, d, ii, s), days, counts, i, sb)
    # no vacua: depende de días previos y de lo observado antes de s
    ref = _emissions("ma", model, counts, days, i, 20)
    c3 = counts.copy(); c3[i - 7] += 20
    assert not np.allclose(ref, _emissions("ma", model, c3, days, i, 20))
    c4 = counts.copy(); c4[i, :, :10] += 30
    assert not np.allclose(ref, _emissions("ma", model, c4, days, i, 20))
    # el modelo se entrena solo con datos ≤ train_end: alterar el futuro de test no lo cambia
    c5 = counts.copy(); c5[i:] = 50
    m5 = P.Model(c5, days, days[40], len(UNI))
    np.testing.assert_allclose(model.predict(counts, days, i), m5.predict(c5, days, i))


def test_el_test_de_causalidad_detecta_fuga():
    days, counts = _synth()

    def leaky(c, d, i, sb):          # mete fuga a propósito: mira un bin de s en adelante
        base = P.base15("ma", c, d, i)
        return P.emit_day("ma", base, c[i], sb + 1)

    with pytest.raises(AssertionError):
        _assert_causal(leaky, days, counts, 60, 20)


def test_correccion_cambia_con_lo_observado():
    days, counts = _synth()
    i, sb = 60, 20
    base = P.base15("ma", counts, days, i)
    e0 = P.emit_day("ma", base, counts[i], sb)
    hi = counts[i].copy(); hi[:, :sb] = base[:, :sb] * 3 + 10          # observado muy por encima
    lo = counts[i].copy(); lo[:, :sb] = 0                              # muy por debajo
    assert (P.emit_day("ma", base, hi, sb) > e0).any()
    assert (P.emit_day("ma", base, lo, sb) < e0).any()
    f = P.correction_factor(base, hi, sb)
    assert f.min() >= 0.5 and f.max() <= 2.0
    # observado == pronosticado → factor 1; en sb=0 no hay corrección
    same = counts[i].copy(); same[:, :sb] = base[:, :sb]
    np.testing.assert_allclose(P.correction_factor(base, same, sb), 1.0)
    np.testing.assert_allclose(P.emit_day("ma", base, hi, 0), P.emit_day("ma", base, lo, 0))
    # salidas y llegadas se corrigen por separado
    only_dep = counts[i].copy(); only_dep[:, :sb, P.DEP] = base[:, :sb, P.DEP] * 3 + 10
    only_dep[:, :sb, P.ARR] = base[:, :sb, P.ARR]
    fd = P.correction_factor(base, only_dep, sb)
    assert (fd[:, 0, P.DEP] > 1).all() and np.allclose(fd[:, 0, P.ARR], 1.0)


def test_ma_mismo_tipo_de_dia_y_daily_una_emision():
    days, counts = _synth()
    i = days.index(date(2025, 3, 12))   # miércoles
    ma = P.ma_counts(counts, days, i)
    idx = [j for j in range(i - 28, i) if days[j].weekday() < 5]
    np.testing.assert_allclose(ma, counts[idx].mean(axis=0), rtol=1e-6)
    out = P.build_day(days[i], UNI, counts, days, _StubModel(), counts[i], write=False)
    d = out[("daily", 0)]
    assert d["issued_at"].nunique() == 1 and d["issued_at"].iat[0] == _t(days[i], "05:30")
    assert d["block_start"].nunique() == P.N60 == 19
    assert (d["refresh_min"] == 0).all()
    # daily = ma sin corrección: total de bloques = ma
    np.testing.assert_allclose(d["departures"].sum(), ma[:, :, P.DEP].sum(), rtol=1e-6)


def test_calendario_de_emisiones_y_cobertura():
    days, counts = _synth()
    i = 60
    out = P.build_day(days[i], UNI, counts, days, _StubModel(), counts[i], write=False)
    start = _t(days[i], "05:30")
    for f, n in ((15, 76), (60, 19), (180, 7)):
        for v in ("oracle", "ma", "model"):
            df = out[(v, f)]
            assert df["issued_at"].nunique() == n
            assert (df["refresh_min"] == f).all() and (df["horizon_h"] == 6).all()
        df = out[("ma", f)]
        for s, g in df.groupby("issued_at"):
            smin = int((s - start) / pd.Timedelta(minutes=1))
            end = min(smin + f + 60 + 360, C.WINDOW_MIN)
            first, last = g["block_start"].min(), g["block_start"].max()
            assert first <= s < first + timedelta(hours=1)          # incluye el bloque que contiene a s
            assert last < start + timedelta(minutes=end) <= last + timedelta(hours=1)
    # oracle: contenido idéntico en cada emisión
    o = out[("oracle", 60)]
    ref = o[o.issued_at == start].set_index(["short_name", "block_start"])
    late = o[o.issued_at == start + timedelta(hours=3)].set_index(["short_name", "block_start"])
    j = late.index.intersection(ref.index)
    np.testing.assert_array_equal(late.loc[j, "departures"], ref.loc[j, "departures"])


def test_archivos_validan_contrato_y_oracle_exacto():
    days, counts = _synth()
    i = 60
    model = P.Model(counts, days, days[40], len(UNI))
    out = P.build_day(days[i], UNI, counts, days, model, counts[i], write=False)
    assert len(out) == 10                                            # daily + 3 variantes × 3 f
    truth60 = P.to60(counts[i].astype(np.float64), axis=1)
    store: dict = {}
    cens = np.zeros((len(UNI), P.N60), bool); cens[0, 3] = True
    for df in out.values():
        K.validate_forecast(df)
        assert df[list(K.FORECAST_OPTIONAL)].isna().all().all()
        P.accumulate(store, "evaluacion", df, UNI, truth60, cens, days[i])
    acc = P.accuracy_table(store)
    assert (acc[acc.variant == "oracle"]["mae"] == 0).all()
    assert (acc[acc.variant != "oracle"]["mae"] > 0).any()
    assert set(acc.subset) == {"all", "censurado", "no_censurado"}


def test_dep_known_ignora_salidas_pero_cuenta_llegadas():
    d = date(2025, 9, 3)
    tr = pd.DataFrame([("b1", "001", "002", _t(d, "06:00"), _t(d, "06:20")),
                       ("b2", "003", "001", _t(d, "06:00"), _t(d, "06:20"))],
                      columns=["bike", "o", "d", "t_dep", "t_arr"])
    c = P.count_trips(tr, UNI, [d], dep_known=np.array([True, False]))[0]
    assert c[:, :, P.DEP].sum() == 1 and c[0, :, P.DEP].sum() == 1
    assert c[:, :, P.ARR].sum() == 2                       # la llegada del viaje con origen ignorado sí cuenta


def test_bloque_en_curso_es_observado_mas_resto_corregido():
    days, counts = _synth()
    i, sb = 60, 6                                          # s = 07:00; bloque 06:30–07:30 = bins 4..7
    base = P.base15("ma", counts, days, i)
    out = P.emit_day("ma", base, counts[i], sb)
    f = P.correction_factor(base, counts[i], sb)[:, 0, :]
    obs = counts[i, :, 4:6, :].sum(axis=1)
    rest = base[:, 6:8, :].sum(axis=1)
    np.testing.assert_allclose(out[:, 1, :], obs + f * rest, rtol=1e-5)
    np.testing.assert_allclose(out[:, 3, :], f * base[:, 12:16, :].sum(axis=1), rtol=1e-5)   # bloque futuro


def _frames_at(out, variant, f, s):
    df = out[(variant, f)]
    return df[df["issued_at"] == s].reset_index(drop=True)


def test_causalidad_por_emision_via_build_day(monkeypatch=None):
    """(b) por el camino de producción: alterar el día en o después de s y todos
    los días posteriores no cambia las emisiones de s, para ma y model."""
    days, counts = _synth()
    i = 60
    model = P.Model(counts, days, days[40], len(UNI))
    ref = P.build_day(days[i], UNI, counts, days, model, counts[i], write=False)
    checked = 0
    for s_hm, sb in (("05:30", 0), ("06:30", 4), ("10:15", 19), ("00:15", 75)):
        s = _t(days[i], s_hm, 1 if s_hm == "00:15" else 0)
        c2 = counts.copy()
        c2[i, :, sb:] = np.random.default_rng(9).poisson(40, size=c2[i, :, sb:].shape)
        c2[i + 1:] = np.random.default_rng(10).poisson(40, size=c2[i + 1:].shape)
        new = P.build_day(days[i], UNI, c2, days, model, counts[i], write=False)
        for v in ("ma", "model"):
            for f in (15, 60):
                a, b = _frames_at(ref, v, f, s), _frames_at(new, v, f, s)
                if not len(a):                                 # s no está en el calendario de esta f
                    continue
                checked += 1
                pd.testing.assert_frame_equal(a, b)
        # control positivo: la emisión siguiente (s+15) de ma_f15 sí cambia
        if sb < 75:
            a = _frames_at(ref, "ma", 15, s + timedelta(minutes=15))
            b = _frames_at(new, "ma", 15, s + timedelta(minutes=15))
            assert not a["departures"].equals(b["departures"])
    assert checked == 12                                   # 4 instantes × {ma, model} × f que los incluye


def test_build_day_detecta_fuga(monkeypatch):
    days, counts = _synth()
    i = 60
    model = P.Model(counts, days, days[40], len(UNI))
    real = P.emit_day
    monkeypatch.setattr(P, "emit_day", lambda v, base, today, sb: real(v, base, today, sb + 1))   # fuga a propósito
    ref = P.build_day(days[i], UNI, counts, days, model, counts[i], write=False)
    s = _t(days[i], "06:30")            # sb = 4 → la versión con fuga mira el bin 4 (≥ s)
    c3 = counts.copy(); c3[i, :, 4] += 40
    new = P.build_day(days[i], UNI, c3, days, model, counts[i], write=False)
    with pytest.raises(AssertionError):
        pd.testing.assert_frame_equal(_frames_at(ref, "ma", 60, s), _frames_at(new, "ma", 60, s))


real_files = sorted(P.OUT_FORECASTS.glob("*_f*.parquet")) if P.OUT_FORECASTS.exists() else []


@pytest.mark.skipif(not real_files, reason="aún no se corrió `python -m ecosim.pronostico`")
def test_archivos_reales_validan():
    import json
    cfg = json.loads(C.DAYS_JSON.read_text())
    days_ = [d["day"] for d in cfg["evaluacion"] + cfg["seleccion"]]
    expected = {f"{d}_daily.parquet" for d in days_}
    expected |= {f"{d}_{v}_f{f}.parquet" for d in days_ for v in ("oracle", "ma", "model") for f in P.REFRESH_MIN}
    have = {f.name for f in P.OUT_FORECASTS.glob("*.parquet")}
    assert len(days_) == 30 and len(expected) == 300 and expected <= have
    for n in sorted(expected):
        K.validate_forecast(pd.read_parquet(P.OUT_FORECASTS / n))
