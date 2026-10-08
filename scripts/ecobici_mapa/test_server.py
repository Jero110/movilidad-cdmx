"""Tests del servidor: contratos JSON de Ahora, Replay y Predicción en vivo.

Predicción se prueba con un feed y un reloj simulados inyectados en
`ecosim.live` (no hay modo histórico en la API)."""

import json
import time
from datetime import datetime, timedelta

import urllib.parse

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import server
from server import app, parse_station_numbers, short_name_keys

INFO = {"last_updated": 1, "data": {"stations": [
    {"station_id": "1", "name": "Río Sena-Río Balsas", "short_name": "001", "lat": 19.43, "lon": -99.17,
     "capacity": 20, "address": "Río Sena", "rental_methods": ["KEY", "APPLEPAY"], "is_charging_station": False},
    {"station_id": "2", "name": "Reforma", "short_name": "002", "lat": 19.42, "lon": -99.16, "capacity": 15}]}}
STATUS = {"last_updated": 1759690000, "data": {"stations": [
    {"station_id": "1", "num_bikes_available": 4, "num_bikes_disabled": 1, "num_docks_available": 15,
     "num_docks_disabled": 0, "is_installed": 1, "is_renting": 1, "is_returning": 1, "last_reported": 1759689990,
     "num_ebikes_available": 0, "vehicle_types_available": [{"vehicle_type_id": "1", "count": 4}]},
    {"station_id": "2", "num_bikes_available": 0, "num_bikes_disabled": 0, "num_docks_available": 15,
     "is_installed": 1, "is_renting": 1, "is_returning": 1, "last_reported": 1759689980}]}}


def _req(**q):
    """Request de prueba con esos parámetros de consulta."""
    return Request({"type": "http", "method": "POST", "path": "/api/live/forecast", "headers": [],
                    "query_string": urllib.parse.urlencode(q).encode()})


def _forecast(**q):
    return server.api_live_forecast(_req(**q), **{k: v for k, v in q.items() if k in ("model", "horizonte")})


@pytest.fixture
def feed(monkeypatch):
    payloads = {"station_information": INFO, "station_status": STATUS, "system_alerts": {"data": {"alerts": []}}}
    monkeypatch.setattr(server.CACHE, "get", lambda feed, ttl: (json.dumps(payloads[feed]).encode(), False))
    return payloads


def test_parse_station_numbers_expands_ranges_and_singles():
    text = "Cuauhtémoc: 176, 264 a 269 y 271 a 275"
    assert parse_station_numbers(text) == {"176", "264", "265", "266", "267", "268", "269", "271", "272", "273", "274", "275"}


def test_parse_station_numbers_ignores_date_ranges():
    text = "Cuauhtémoc: 176, 264 a 269 y 271 a 275 estarán fuera de servicio del 13 al 16 de septiembre."
    found = parse_station_numbers(text)
    assert not ({"13", "14", "15", "16"} & found)
    assert {"176", "264", "269", "271", "275"} <= found


def test_short_name_keys_variants():
    assert short_name_keys("033") == {"33"}
    assert short_name_keys("264-275") == {str(n) for n in range(264, 276)}
    assert short_name_keys("268-269") == {"268", "269"}


def test_rutas_nuevas_y_viejas():
    paths = {(r.path, tuple(sorted(getattr(r, "methods", []) or []))) for r in app.routes}
    only = {p for p, _ in paths}
    for p in ("/", "/api/snapshot", "/api/replay/index", "/api/replay/{day}/dia", "/api/replay/{day}/{arm}",
              "/api/live/models", "/api/live/forecast", "/api/live/assign/start", "/api/live/assign",
              "/api/live/assign/{sid}", "/api/live/assign/{sid}/stop", "/api/zonas", "/app.js", "/app.css"):
        assert p in only, p
    for p in ("/api/live/status", "/api/live/run", "/api/live/latest", "/api/live/eval", "/api/live/orders.csv",
              "/api/rebalance", "/forecast/{short_name}", "/api/snapshots/dates"):
        assert p not in only, p
    assert ("/api/live/forecast", ("POST",)) in paths
    assert ("/api/live/assign/start", ("POST",)) in paths
    assert ("/api/live/assign/{sid}/stop", ("POST",)) in paths


def test_snapshot_trae_raw_con_todas_las_llaves(feed):
    body = json.loads(server.api_snapshot().body)
    st = {s["short_name"]: s for s in body["stations"]}
    for info, status in zip(INFO["data"]["stations"], STATUS["data"]["stations"]):
        raw = st[info["short_name"]]["raw"]
        assert set(raw) == set(info) | set(status)
        assert all(raw[k] == v for k, v in {**info, **status}.items())
    assert st["001"]["bikes"] == 4 and st["001"]["bikes_disabled"] == 1


def test_replay_endpoints_contrato():
    if not server.REPLAY_DIR.joinpath("index.json").exists():
        pytest.skip("replay no precalculado")
    idx = server.api_replay_index()
    day = sorted(idx["days"])[0]
    dia = json.loads(server.REPLAY_DIR.joinpath(day, "dia.json").read_text())
    assert str(server.api_replay_day(day).path).endswith("dia.json")
    assert {"day", "start", "step_min", "n_frames", "times", "stations", "trips"} <= set(dia)
    assert {"id", "o", "d", "dep", "arr"} <= set(dia["trips"])
    for arm in idx["days"][day]["arms"]:
        a = json.loads(open(server.api_replay_arm(day, arm).path).read())
        assert {"bikes", "dis", "cum", "applied", "decision", "final", "check", "spec",
                "snap", "est", "desvios", "inicial"} <= set(a)
        assert len(a["snap"]) == len(a["est"]) == len(a["desvios"]) == dia["n_frames"]
        assert {"min_desde_anterior", "salidas", "llegadas", "desvios_salida", "desvios_llegada", "emitidas",
                "recogidas", "entregadas", "a_rentable", "a_no_rentable", "E", "F", "EF", "EF_acum",
                "cuadre_ok", "descuadre_estaciones", "bicis_sistema"} <= set(a["snap"][0])
    with pytest.raises(HTTPException):
        server.api_replay_arm("2025-09-01", "../x")
    # Archivos viejos de la carpeta (sin snap) y carpetas mal nombradas no se sirven ni se listan.
    for arm in ("baseline", "daily", "oracle"):
        with pytest.raises(HTTPException) as exc:
            server.api_replay_arm(day, arm)
        assert exc.value.status_code == 400
    for bad_day in ("2025-09-01 2025-09-11 2025-09-20 2025-09-30", "2025-09-01x", "../2025-09-01"):
        for call in (lambda: server.api_replay_day(bad_day), lambda: server.api_replay_arm(bad_day, "ma_diaria")):
            with pytest.raises(HTTPException) as exc:
                call()
            assert exc.value.status_code == 400
    assert all(len(d) == 10 for d in idx["days"])
    assert {a for v in idx["days"].values() for a in v["arms"]} <= set(server._arms())


def test_replay_variantes_de_alfa_y_alfa_en_vivo():
    for bad in ("ma_diaria@ax", "ma_diaria@a", "nada@a10", "../x@a10", "ma_diaria@a-1"):
        with pytest.raises(HTTPException) as exc:
            server.api_replay_arm("2025-09-01", bad)
        assert exc.value.status_code == 400, bad
    idx = server.api_replay_index() if server.REPLAY_DIR.joinpath("index.json").exists() else None
    for day, v in (idx or {"days": {}})["days"].items():
        for arm, por_alfa in v.get("alfas", {}).items():
            assert arm in v["arms"] and arm in server._arms()
            for alfa in por_alfa:
                a = json.loads(open(server.api_replay_arm(day, f"{arm}@a{alfa}").path).read())
                assert a["alfa"] == float(alfa) and a["check"]["cuadre_todas_las_fotos"] and a["check"]["igual"] is None
    assert idx is None or 0 in idx.get("alfas", [0])
    with pytest.raises(HTTPException) as exc:
        server.api_live_assign_alfa("no-existe", 3.0)
    assert exc.value.status_code == 404


def test_live_models_con_datos_de_produccion():
    from ecosim import actualizar as A
    if not A.MANIFIESTO.exists():
        pytest.skip("sin datos de producción: corre `uv run python -m ecosim.actualizar`")
    out = server.api_live_models()
    assert set(out["datos"]) >= {"ultimo_dia_publicado", "actualizado"}
    by = {m["key"]: m for m in out["modelos"]}
    assert set(by) == {"ma_diaria", "lgbm_diario", "lgbm_directo"}
    for key, m in by.items():
        assert m["disponible"] is True and m["motivo"] is None, (key, m["motivo"])
        assert {"key", "forma", "label", "disponible", "motivo", "corte", "n", "lambda", "nota", "horizontes"} <= set(m)
        assert m["horizontes"] == (["dia"] if m["forma"] == "diaria" else [1, 2, 3, 4])
        assert set(m["corte"]) >= {"train_start", "train_end"}
    assert by["lgbm_directo"]["forma"] == "directa" and by["lgbm_diario"]["forma"] == "diaria"


class Reloj:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


def test_pronostico_y_asignacion_en_vivo_con_feed_y_reloj_simulados(tmp_path, monkeypatch):
    """Contrato de forecast y assign; el reloj simulado corre 1 h en milisegundos."""
    import numpy as np
    from datetime import date
    from ecosim import config as C, live, pronostico as P
    days = [date(2025, 7, 1) + timedelta(days=k) for k in range(92)]
    rng = np.random.default_rng(1)
    counts = rng.poisson(1.0, size=(len(days), 2, P.NB15, 2)).astype(np.float32)
    prod = live.Produccion.from_arrays(days, ["001", "002"], counts)
    reloj = Reloj(datetime(2025, 10, 1, 10, 3))

    def snap():
        epoch = (reloj.t - timedelta(hours=C.UTC_OFFSET_HOURS) - datetime(1970, 1, 1)).total_seconds()
        return {"last_updated": epoch, "stations": [
            {"short_name": "001", "name": "Uno", "lat": 19.4, "lon": -99.1, "capacity": 20, "bikes": 20, "docks": 0,
             "bikes_disabled": 0, "renting": True, "installed": True},
            {"short_name": "002", "name": "Dos", "lat": 19.5, "lon": -99.2, "capacity": 20, "bikes": 0, "docks": 20,
             "bikes_disabled": 0, "renting": True, "installed": True}]}
    loop = live.LiveLoop(snapshot_fn=snap, log_dir=tmp_path / "gbfs", clock=reloj)
    fz = {"n": {"diaria": 2, "directa": 2}, "lambda_por_brazo": {"ma_diaria": {"lambda": 1.0}},
          "topes_base": {"visitas_por_decision": 4, "bicis_por_visita": 5}, "retiro": 0.0}
    monkeypatch.setattr(live, "produccion", lambda prod_dir=None: prod)
    monkeypatch.setattr(live, "FORECAST_DIR", tmp_path / "fc")
    monkeypatch.setattr(server, "LIVE", loop)
    monkeypatch.setattr(server, "ASIGNACIONES", live.Asignaciones(loop, tmp_path / "ses", tmp_path / "fc", clock=reloj,
                                                                  sleep=reloj.sleep, prod=prod, fz=fz))
    fc = _forecast(model="ma_diaria", horizonte="dia")
    assert {"id", "issued_at", "t_feed", "model", "referencia", "minutes", "stations", "bikes_now",
            "proyeccion", "salidas", "llegadas", "riesgos"} <= set(fc)
    assert fc["minutes"] == [15 * k for k in range(1, 4 * 14 + 3)]  # 10:00 → 00:30
    assert fc["stations"]["name"] == ["Uno", "Dos"]
    assert fc["referencia"]["viajes_del_dia"] is None
    for bad in (dict(model="oraculo_diario", horizonte="dia"), dict(model="ma_diaria", horizonte="2"),
                dict(model="lgbm_diario", horizonte="1"), dict(model="lgbm_directo", horizonte="dia"),
                dict(model="lgbm_directo", horizonte="6")):
        with pytest.raises(HTTPException) as exc:
            _forecast(**bad)
        assert exc.value.status_code == 400, bad
        assert "horizonte" in exc.value.detail or "modelo" in exc.value.detail
    sid = server.api_live_assign_start(forecast_id=fc["id"], horas=1)["session_id"]
    for _ in range(200):
        s = server.api_live_assign_get(sid)
        if s["estado"] != "corriendo":
            break
        time.sleep(0.05)
    assert s["estado"] == "terminada", s.get("error")
    assert {"estado", "model", "inicio", "horas", "pasos", "totales", "siguiente_paso"} <= set(s)
    assert len(s["pasos"]) >= 4 and s["inicio"] == "2025-10-01T10:00"
    for p in s["pasos"]:
        assert {"t", "t_feed", "emitidas", "aplicadas", "bicis_a_mover", "visitas", "salidas_est", "llegadas_est"} <= set(p)
        for o in p["emitidas"] + p["aplicadas"]:
            assert set(o) >= {"short_name", "accion", "n", "emitida", "recoge", "entrega"}
    assert s["totales"]["recogidas"] == s["totales"]["entregadas"] == s["totales"]["bicis_a_mover"] > 0
    assert sid in [x["id"] for x in server.api_live_assign_list()["sesiones"]]
    with pytest.raises(HTTPException) as exc:
        server.api_live_assign_get("no-existe")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        server.api_live_assign_start(forecast_id="no-existe", horas=1)
    assert exc.value.status_code == 404


def test_replay_no_reutiliza_cache_del_navegador(tmp_path):
    day_file = tmp_path / "dia.json"
    day_file.write_text('{"start":"2025-09-01T05:00:00"}')
    assert server._json_file(day_file).headers["cache-control"] == "no-store"


def test_zonas_ageb_cada_estacion_en_una_zona(feed):
    if not server.ZONAS_FILE.exists():
        pytest.skip("sin zonas_ageb.geojson")
    resp = server.api_zonas()
    assert resp.status_code == 200 and resp.media_type == "application/geo+json"
    gj = json.loads(resp.body)
    assert gj["type"] == "FeatureCollection" and gj["features"]
    snap = json.loads(server.api_snapshot().body)["stations"]
    vistos = [sn for f in gj["features"] for sn in f["properties"]["estaciones"]]
    assert sorted(vistos) == sorted(s["short_name"] for s in snap)  # cada una exactamente una vez
    for f in gj["features"]:
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")
        assert set(f["properties"]) == {"cvegeo", "alcaldia", "estaciones"}
        assert len(f["properties"]["cvegeo"]) == 13 and f["properties"]["cvegeo"].startswith("09")


def test_zonas_archivo_cubre_todas_las_estaciones_con_que_se_genero():
    if not server.ZONAS_FILE.exists():
        pytest.skip("sin zonas_ageb.geojson")
    gj = json.loads(server.ZONAS_FILE.read_text())
    todas = [sn for f in gj["features"] for sn in f["properties"]["estaciones"]]
    assert len(todas) == len(set(todas)) == gj["metadata"]["estaciones"]
    assert all(f["properties"]["estaciones"] for f in gj["features"])
    assert server.ZONAS_FILE.stat().st_size < 2_000_000
    # Con estaciones reales del archivo y una inventada lejos: la lejana va a la más cercana.
    from shapely.geometry import shape
    f0 = gj["features"][0]
    c = shape(f0["geometry"]).representative_point()
    out = server.zonas_con_estaciones(gj, [{"short_name": "A", "lat": c.y, "lon": c.x},
                                           {"short_name": "Z", "lat": 19.0, "lon": -99.0}])
    assert [sn for f in out["features"] for sn in f["properties"]["estaciones"]].count("Z") == 1
    assert out["metadata"]["cercanas_snapshot"][0]["short_name"] == "Z"
    assert "A" in next(f for f in out["features"] if f["properties"]["cvegeo"] == f0["properties"]["cvegeo"])["properties"]["estaciones"]


def test_forecast_rechaza_parametros_desconocidos():
    for q in (dict(model="ma_diaria", horizonte="dia", at="2026-10-05T10:00"), dict(model="lgbm_directo", horizonte="2", x="1")):
        with pytest.raises(HTTPException) as exc:
            _forecast(**q)
        assert exc.value.status_code == 400 and "no aceptados" in exc.value.detail


def test_feed_y_eventos(tmp_path, monkeypatch):
    from ecosim import live
    reloj = Reloj(datetime(2025, 10, 1, 10, 3))

    def snap():
        epoch = (reloj.t - timedelta(hours=-6) - datetime(1970, 1, 1)).total_seconds()
        return {"last_updated": epoch, "stations": [
            {"short_name": "001", "name": "Uno", "lat": 19.4, "lon": -99.1, "capacity": 20, "bikes": 5, "docks": 15,
             "bikes_disabled": 0, "renting": True, "installed": True}]}
    loop = live.LiveLoop(snapshot_fn=snap, log_dir=tmp_path, clock=reloj)
    monkeypatch.setattr(server, "LIVE", loop)
    loop.poll()
    info = server.api_live_feed()
    assert info["ultima_lectura"]["bicis_disponibles"] == 5 and info["lecturas_hoy"] == 1
    resp = server.api_live_eventos()
    assert resp.media_type == "text/event-stream"
