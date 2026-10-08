"""El replay de la app refleja run 3 (siete brazos, 4 días por mes, E+F exacto)
y cada foto cuadra: bicis por estación y del sistema contra viajes, órdenes y
cambios de etiqueta."""

from __future__ import annotations

import json

import numpy as np
import pytest

from ecosim import replay

SNAP_KEYS = {"min_desde_anterior", "salidas", "llegadas", "desvios_salida", "desvios_llegada", "emitidas",
             "recogidas", "entregadas", "a_rentable", "a_no_rentable", "E", "F", "EF", "EF_acum",
             "cuadre_ok", "descuadre_estaciones", "bicis_sistema"}


@pytest.fixture(scope="module")
def fz():
    return replay.load_frozen()


def _precalculados():
    idx = replay.OUT_DIR / "index.json"
    if not idx.exists():
        pytest.skip("replay no precalculado")
    ix = json.loads(idx.read_text())
    if ix.get("frozen_sha") != replay.frozen_sha():
        pytest.skip("replay precalculado de otro frozen")
    return ix


def _archivos():
    ix = _precalculados()
    for day, info in ix["days"].items():
        dia = json.loads((replay.OUT_DIR / day / "dia.json").read_text())
        for arm in info["arms"]:
            yield day, arm, dia, json.loads((replay.OUT_DIR / day / f"{arm}.json").read_text())


def test_brazos_run3(fz):
    assert replay.brazos(fz) == [
        "sin_rebalanceo", "ecobici", "oraculo_diario", "ma_diaria", "lgbm_diario", "oraculo_directo", "lgbm_directo"
    ]
    assert fz["mejor_real"] == "ma_diaria"


def test_replay_cuadra_cada_foto(fz):
    """Cuadre por foto con el simulador actual (greedy y reubicación junto al destino)."""
    day = replay.default_days()[0]
    for arm in ("sin_rebalanceo", "ecobici", "ma_diaria"):
        res, rec, _ = replay.run_real(day, arm, fz)
        out = replay.frames(res, rec)
        assert all(s["cuadre_ok"] for s in out["snap"])
        for key in ("bikes", "dis", "decision", "applied", "snap", "est", "desvios"):
            assert len(out[key]) == replay.N_FRAMES, key
        assert all(SNAP_KEYS <= set(s) for s in out["snap"])
        assert out["snap"][-1]["EF_acum"] == res.EF
        assert len(out["pares"]) == replay.N_FRAMES
        if arm != "ma_diaria":
            assert not any(out["pares"])  # sin camioneta no hay pares
        if arm == "ma_diaria":
            # Cada orden emitida trae su paquete; los pares ejecutados cuadran con lo recogido y con las reubicaciones.
            assert all(len(o) == 3 and o[2] > 0 for d in out["decision"] if d for o in d["orders"])
            pares = [p for fr in out["pares"] for p in fr]
            assert pares and all(len(p) == 9 for p in pares)
            assert sum(p[3] for p in pares if not p[4]) == sum(s["recogidas"] for s in out["snap"])
            assert sum(p[3] for p in pares if p[4]) == sum(s["devueltas"] for s in out["snap"])
            assert any(d and d["orders"] for d in out["decision"])
            assert all(d["plan"]["status"] == "greedy" for d in out["decision"] if d and d["orders"])
            # `devueltas` = bicis dejadas junto al destino cuando no cupieron.
            assert sum(s["recogidas"] for s in out["snap"]) == sum(s["entregadas"] + s["devueltas"] for s in out["snap"])
            assert sum(s["devueltas"] for s in out["snap"]) == res.recortes["reubicacion_destino"]


def test_replay_day_arm_ef_igual_resultados_y_cuadre(fz):
    from ecosim import run
    if fz["topes_base"] != run.cap():
        pytest.skip("resultados.csv y frozen.json son de una corrida anterior (otros topes); los regenera `runs`")
    day = replay.default_days()[0]
    for arm in ("sin_rebalanceo", "ecobici", "ma_diaria"):
        out = replay.replay_day_arm(day, arm, fz, write=False)
        row = replay.resultados_row(replay.spec(fz, arm, day))
        assert row is not None
        assert out["final"]["EF"] == int(row["EF"])
        assert out["check"]["igual"] is True
        assert out["check"]["cuadre_todas_las_fotos"] is True
        assert out["snap"][-1]["EF_acum"] == out["final"]["EF"]


def test_default_days_four_per_month():
    days = replay.default_days()
    assert len(days) == 16
    assert {d[:7] for d in days} == {"2025-09", "2025-10", "2025-11", "2025-12"}
    assert all(sum(d.startswith(m) for d in days) == 4 for m in {"2025-09", "2025-10", "2025-11", "2025-12"})


def test_dias_precalculados_empiezan_a_las_0500_y_cierran_0030():
    ix = _precalculados()
    for day in ix["days"]:
        payload = json.loads((replay.OUT_DIR / day / "dia.json").read_text())
        assert payload["start"] == f"{day}T05:00:00", day
        assert payload["times"][0] == "05:00" and payload["times"][-1] == "00:30", day
        assert payload["n_frames"] == replay.N_FRAMES, day
        tr = payload["trips"]
        assert len(tr["id"]) == len(tr["o"]) == len(tr["dep"]) and tr["id"] == sorted(set(tr["id"])), day


def test_repara_dia_obsoleto_sin_recalcular_brazos(tmp_path):
    day = replay.default_days()[0]
    path = tmp_path / day / "dia.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"start": f"{day}T05:30:00", "n_frames": 77}))
    replay.ensure_day_payload(day, tmp_path)
    repaired = json.loads(path.read_text())
    assert repaired["start"] == f"{day}T05:00:00"
    assert repaired["times"][0] == "05:00"
    assert repaired["n_frames"] == replay.N_FRAMES
    assert "id" in repaired["trips"]


def test_archivos_precalculados_ef_igual_resultados():
    ix = _precalculados()
    assert set(ix["arms"]) == set(replay.ARMS)
    for day, info in ix["days"].items():
        assert set(info["arms"]) == set(replay.ARMS), day
        for arm, a in info["arms"].items():
            assert a["check"]["igual"] is True, (day, arm)
            row = replay.resultados_row(replay.spec(replay.load_frozen(), arm, day))
            assert row is not None
            assert a["final"]["EF"] == int(row["EF"]), (day, arm)
            assert a["cum"]["EF"][-1] == int(row["EF"]), (day, arm)


def test_cuadre_en_todas_las_fotos_de_todos_los_replays():
    """Rehace la cuenta desde el payload publicado, sin confiar en `cuadre_ok`."""
    n = 0
    for day, arm, dia, a in _archivos():
        assert all(SNAP_KEYS <= set(s) for s in a["snap"]), (day, arm)
        bikes, dis = np.array(a["bikes"]), np.array(a["dis"])
        prev_b, prev_d = np.array(a["inicial"]["bikes"]), np.array(a["inicial"]["dis"])
        prev_sys = a["inicial"]["bicis_sistema"]
        assert len(prev_b) == len(dia["stations"]["short_name"]) == bikes.shape[1]
        for k, s in enumerate(a["snap"]):
            delta_b, delta_d = np.zeros_like(prev_b), np.zeros_like(prev_d)
            tot = dict.fromkeys(("entregadas", "recogidas", "a_rentable", "a_no_rentable", "salidas", "llegadas", "devueltas"), 0)
            for i, ent, rec, ar, anr, sal, lle, dev in a["est"][k]:
                delta_b[i] += -sal + lle + ent - rec + dev + ar - anr
                delta_d[i] += anr - ar
                for key, v in zip(tot, (ent, rec, ar, anr, sal, lle, dev)):
                    tot[key] += v
            assert s["cuadre_ok"] is True and s["descuadre_estaciones"] == 0, (day, arm, k)
            assert (prev_b + delta_b == bikes[k]).all(), (day, arm, k)
            assert (prev_d + delta_d == dis[k]).all(), (day, arm, k)
            assert all(s[key] == v for key, v in tot.items()), (day, arm, k)
            assert s["bicis_sistema"] == bikes[k].sum() + dis[k].sum() + s["en_camioneta"] + s["en_viaje"]
            assert s["bicis_sistema"] == prev_sys + s["entran_externas"] - s["salen_externas"] + s["externo_ecobici"]
            assert s["EF"] == s["E"] + s["F"]
            assert s["min_desde_anterior"] == (0 if k == 0 else replay.STEP)
            prev_b, prev_d, prev_sys = bikes[k], dis[k], s["bicis_sistema"]
            n += 1
        if arm != "ecobici":
            assert a["snap"][-1]["en_camioneta"] == 0, (day, arm)
            assert all(s["externo_ecobici"] == 0 for s in a["snap"])
        if arm == "sin_rebalanceo":
            assert all(s["recogidas"] == s["entregadas"] == s["emitidas"] == 0 for s in a["snap"])
        assert a["snap"][-1]["EF_acum"] == a["final"]["EF"], (day, arm)
    assert n > 0


def test_desvios_por_foto_suman_los_del_dia():
    for day, arm, dia, a in _archivos():
        ids = set(dia["trips"]["id"])
        sal = sum(s["desvios_salida"] for s in a["snap"])
        lle = sum(s["desvios_llegada"] for s in a["snap"])
        assert (sal, lle) == (a["final"]["desvios_salida"], a["final"]["desvios_llegada"]), (day, arm)
        for k, rows in enumerate(a["desvios"]):
            assert sum(r[1] == "salida" for r in rows) == a["snap"][k]["desvios_salida"], (day, arm, k)
            assert sum(r[1] == "llegada" for r in rows) == a["snap"][k]["desvios_llegada"], (day, arm, k)
            for tid, kind, i0, i1, metros in rows:
                assert tid in ids and kind in ("salida", "llegada") and i0 != i1 and i1 >= 0, (day, arm, k)
                assert metros is None or metros >= 0


def _km_traslados(out, dia):
    """(km de tramos, bici-km) de los traslados ejecutados de un replay, con las coordenadas del día."""
    st = dia["stations"]
    lat, lon = np.radians(np.array(st["lat"], float)), np.radians(np.array(st["lon"], float))
    km = bkm = 0.0
    for fr in out["pares"]:
        for _, o, d, bicis, tipo, *_ in fr:
            if tipo:
                continue
            h = np.sin((lat[d] - lat[o]) / 2) ** 2 + np.cos(lat[o]) * np.cos(lat[d]) * np.sin((lon[d] - lon[o]) / 2) ** 2
            x = 2 * 6371 * np.arcsin(np.sqrt(h))
            km += x
            bkm += x * bicis
    return km, bkm


def test_variante_de_alfa_cuadra_y_baja_los_km(fz, tmp_path):
    """α > 0: sin comparar con resultados.csv (es de α = 0), cuadra cada foto y los tramos son más cortos."""
    day = replay.default_days()[0]
    assert replay.arm_file("ma_diaria") == "ma_diaria.json" and replay.arm_file("ma_diaria", 10) == "ma_diaria@a10.json"
    with pytest.raises(ValueError):
        replay.replay_day_arm(day, "ecobici", fz, out_dir=tmp_path, alfa=5)
    base = replay.replay_day_arm(day, "ma_diaria", fz, out_dir=tmp_path)
    alto = replay.replay_day_arm(day, "ma_diaria", fz, out_dir=tmp_path, alfa=10)
    assert (tmp_path / day / "ma_diaria.json").exists() and (tmp_path / day / "ma_diaria@a10.json").exists()
    assert base["alfa"] == 0.0 and alto["alfa"] == 10.0 and alto["spec"]["alfa"] == 10.0
    assert alto["check"]["cuadre_todas_las_fotos"] and alto["check"]["igual"] is None and base["check"]["igual"] is True
    dia = json.loads((tmp_path / day / "dia.json").read_text())
    km0, bkm0 = _km_traslados(base, dia)
    km1, bkm1 = _km_traslados(alto, dia)
    assert 0 < km1 < km0 and 0 < bkm1 < bkm0
    # El índice lista las variantes sin tocar el brazo de α = 0.
    ix = replay.write_index(tmp_path)
    assert ix["alfas"] == [0, 10] and list(ix["days"][day]["alfas"]["ma_diaria"]) == ["10"]
    assert ix["days"][day]["arms"]["ma_diaria"]["final"]["EF"] == base["final"]["EF"]
