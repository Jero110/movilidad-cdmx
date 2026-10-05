"""Predicción en vivo: misma construcción de variables que `ecosim.pronostico`,
rezagos sin ceros, viajes del día inferidos del feed y asignación en tiempo real
con feed y reloj simulados (sin modo histórico)."""
import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ecosim import actualizar as A, config as C, live, pronostico as P

UNIVERSE = ["001", "002", "003"]
D = date(2025, 9, 20)
FZ = {"n": {"diaria": 2, "directa": 2},
      "lambda_por_brazo": {"ma_diaria": {"lambda": 1.0}, "lgbm_diario": {"lambda": 1.0}, "lgbm_directo": {"lambda": 1.0}},
      "topes_base": {"visitas_por_decision": 4, "bicis_por_visita": 5}, "retiro": 0.0}


def serie(start=date(2025, 6, 1), end=date(2025, 9, 30), seed=0):
    days = [start + timedelta(days=k) for k in range((end - start).days + 1)]
    rng = np.random.default_rng(seed)
    base = rng.uniform(0.2, 2.0, size=(len(UNIVERSE), P.NB15, 2))
    counts = np.stack([rng.poisson(base * (1.5 if d.weekday() < 5 else 0.7)) for d in days]).astype(np.float32)
    return days, counts


@pytest.fixture(scope="module")
def prod():
    days, counts = serie()
    models = {"lgbm_diario": P.train(counts, days, days[0], days[-1], False),
              "lgbm_directo": P.train(counts, days, days[0], days[-1], True)}
    return live.Produccion.from_arrays(days, UNIVERSE, counts, models)


# --- variables: misma función que el experimento ---------------------------------

@pytest.mark.parametrize("modelo,direct", [("lgbm_diario", False), ("lgbm_directo", True)])
@pytest.mark.parametrize("kind", [P.DEP, P.ARR])
def test_variables_en_vivo_iguales_a_pronostico_con_rezagos_completos(prod, modelo, direct, kind):
    i = prod.days.index(D)
    ticks = np.arange(P.NB15) if direct else np.array([0])
    expected, _ = P._features(prod.counts, prod.days, i, kind, ticks, direct, labels=False)
    got = live.variables(prod, modelo, D, kind, ticks, hoy=prod.counts[i])
    assert live.dia_base(prod, D, modelo)[1]["rezagos"] == "reales"
    pd.testing.assert_frame_equal(got.reset_index(drop=True), expected.reset_index(drop=True))
    assert not got[["lag7", "lag14", "mean28"]].isna().any().any()


def test_variables_en_vivo_iguales_con_datos_de_produccion():
    if not A.MANIFIESTO.exists():
        pytest.skip("sin datos de producción: corre `uv run python -m ecosim.actualizar`")
    prod = live.produccion()
    day = date(2026, 9, 15)
    i = prod.days.index(day)
    for modelo, direct in (("lgbm_diario", False), ("lgbm_directo", True)):
        ticks = np.arange(0, P.NB15, 7) if direct else np.array([0])
        for kind in (P.DEP, P.ARR):
            expected, _ = P._features(prod.counts, prod.days, i, kind, ticks, direct, labels=False)
            got = live.variables(prod, modelo, day, kind, ticks, hoy=prod.counts[i])
            pd.testing.assert_frame_equal(got.reset_index(drop=True), expected.reset_index(drop=True))
    # Y el pronóstico completo es el mismo objeto que construiría el experimento.
    fc, ref = live.construir(prod, "lgbm_diario", day)
    full = P.forecast("lgbm_diario", prod.counts, prod.days, day, prod.models["lgbm_diario"])
    t = datetime.combine(day, C.DAY_START) + timedelta(hours=3)
    np.testing.assert_allclose(fc.table(t, 4), full.table(t, 4), rtol=1e-6)
    assert ref["rezagos"] == "reales"


def test_media_movil_en_vivo_igual_a_la_del_experimento(prod):
    fc, ref = live.construir(prod, "ma_diaria", D)
    full = P.forecast("ma_diaria", prod.counts, prod.days, D)
    t = datetime.combine(D, C.DAY_START) + timedelta(hours=2)
    np.testing.assert_array_equal(fc.table(t, 4), full.table(t, 4))
    assert ref["rezagos"] == "reales"


# --- rezagos faltantes: día de referencia, nunca ceros ---------------------------

def test_rezago_faltante_usa_dia_de_referencia_y_no_ceros(prod):
    counts = prod.counts.copy()
    counts[prod.days.index(D - timedelta(days=7))] = 0  # lag7 no publicado
    p2 = live.Produccion.from_arrays(prod.days, UNIVERSE, counts, prod.models)
    base, ref = live.dia_base(p2, D, "lgbm_diario")
    assert ref["rezagos"] == "dia_referencia" and base != D
    assert any("lag7" in f for f in ref["faltan"])
    assert live.day_type(base) == live.day_type(D)
    X = live.variables(p2, "lgbm_diario", D, P.DEP, [0])
    j = prod.days.index(base)
    expected, _ = P._features(counts, prod.days, j, P.DEP, np.array([0]), False, labels=False)
    pd.testing.assert_frame_equal(X.reset_index(drop=True), expected.reset_index(drop=True))
    assert not X[["lag7", "lag14", "mean28"]].isna().any().any()
    # La media móvil solo necesita mean28: sigue con rezagos reales.
    assert live.dia_base(p2, D, "ma_diaria")[1]["rezagos"] == "reales"


def test_hoy_despues_del_ultimo_dia_publicado(prod):
    # Con viajes hasta el 30-sep: el 5-oct tiene lag7 (28-sep) y lag14 (21-sep).
    assert live.dia_base(prod, date(2025, 10, 5), "lgbm_diario")[1]["rezagos"] == "reales"
    base, ref = live.dia_base(prod, date(2025, 10, 15), "lgbm_diario")  # lag7 = 8-oct, no publicado
    assert ref["rezagos"] == "dia_referencia" and base <= prod.ultimo
    X = live.variables(prod, "lgbm_diario", date(2025, 10, 15), P.ARR, [0])
    assert not X[["lag7", "lag14", "mean28"]].isna().any().any()


def test_sin_dia_con_rezagos_completos_falla_sin_ceros(prod):
    counts = prod.counts.copy()
    counts[[i for i, d in enumerate(prod.days) if d.weekday() >= 5]] = 0  # ningún fin de semana publicado
    p2 = live.Produccion.from_arrays(prod.days, UNIVERSE, counts, prod.models)
    with pytest.raises(ValueError, match="no se rellenan con ceros"):
        live.construir(p2, "lgbm_diario", D)


# --- viajes del día inferidos del feed: misma función, otra fuente ----------------

TRIPS = [  # (origen, destino, salida, llegada) el día D
    ("001", "002", "05:07:30", "05:12:30"),
    ("001", "002", "05:20:30", "05:44:30"),
    ("003", "001", "05:59:30", "06:14:30"),
    ("001", "003", "06:01:30", "06:40:30"),
    ("002", "003", "06:22:30", "06:31:30"),
]


def _trips():
    at = lambda s: datetime.combine(D, datetime.strptime(s, "%H:%M:%S").time())  # noqa: E731
    return pd.DataFrame([(o, d, at(a), at(b)) for o, d, a, b in TRIPS], columns=["o", "d", "t_dep", "t_arr"])


def _feed_from_trips(trips, t0, t1, base=8, camioneta=None):
    """Una lectura por minuto; inventario = base + llegadas − salidas hasta ese minuto."""
    rows = []
    t = t0
    while t <= t1:
        for sn in UNIVERSE:
            stock = base + int(((trips.d == sn) & (trips.t_arr <= t)).sum()) - int(((trips.o == sn) & (trips.t_dep <= t)).sum())
            if camioneta and sn == camioneta[0] and t >= camioneta[1]:
                stock += camioneta[2]
            rows.append((t, sn, stock, 0, 20 - stock, 20, 1))
        t += timedelta(minutes=1)
    return pd.DataFrame(rows, columns=["t", "short_name", "bikes", "disabled", "docks", "cap", "ok"])


def test_conteos_del_feed_iguales_a_los_de_viajes(prod):
    trips = _trips()
    day0 = datetime.combine(D, C.DAY_START)
    upto = day0 + timedelta(hours=2)
    # Un salto de +6 (camioneta) en 002 a las 06:05: no es viaje.
    log = _feed_from_trips(trips, day0 - timedelta(minutes=30), upto, camioneta=("002", day0 + timedelta(minutes=65), 6))
    from_trips = P.count_trips(trips, UNIVERSE, [D])[0]
    from_feed, info = live.infer_flows(log, UNIVERSE, day0, upto)
    q = 8  # cuartos completos hasta las 07:00
    np.testing.assert_array_equal(from_feed[:, :q], from_trips[:, :q])
    assert info["camion_n"] == 1 and info["camion_bicis"] == 6
    ticks = np.arange(q + 1)
    for kind in (P.DEP, P.ARR):
        a = live.variables(prod, "lgbm_directo", D, kind, ticks, hoy=from_trips)
        b = live.variables(prod, "lgbm_directo", D, kind, ticks, hoy=from_feed)
        pd.testing.assert_frame_equal(a, b)
        assert a.last60.sum() > 0


def test_feed_hoy_sin_lecturas_lo_dice():
    day0 = datetime.combine(D, C.DAY_START)
    empty = pd.DataFrame(columns=["t", "short_name", "bikes", "disabled", "docks", "cap", "ok"])
    out = live.feed_hoy(empty, D, day0 + timedelta(hours=3))
    assert out["completo"] is False and out["fotos"] == 0 and out["desde"] is None and "nota" in out
    log = _feed_from_trips(_trips(), day0 + timedelta(hours=1), day0 + timedelta(hours=2))
    late = live.feed_hoy(log, D, day0 + timedelta(hours=2))
    assert late["completo"] is False and late["desde"] == "2025-09-20T06:00:00"
    full = live.feed_hoy(_feed_from_trips(_trips(), day0 - timedelta(minutes=5), day0 + timedelta(hours=2)),
                         D, day0 + timedelta(hours=2))
    assert full["completo"] is True


# --- pronóstico ------------------------------------------------------------------

FORECAST_KEYS = {"id", "issued_at", "t_feed", "model", "referencia", "minutes", "stations", "bikes_now",
                 "proyeccion", "salidas", "llegadas", "riesgos"}


VALIDOS = [("ma_diaria", "dia"), ("lgbm_diario", "dia"), ("lgbm_directo", "1"), ("lgbm_directo", "2"),
           ("lgbm_directo", "3"), ("lgbm_directo", "4")]


@pytest.mark.parametrize("modelo,horizonte", VALIDOS)
def test_pronostico_contrato(prod, modelo, horizonte):
    day0 = datetime.combine(D, C.DAY_START)
    t_feed = day0 + timedelta(hours=1, minutes=7)
    log = _feed_from_trips(_trips(), day0 - timedelta(minutes=30), t_feed)
    fc = live.pronosticar(modelo, horizonte, log, t_feed, prod=prod)
    assert FORECAST_KEYS <= set(fc)
    assert set(fc["referencia"]) >= {"rezagos", "dia_referencia", "feed_hoy", "viajes_del_dia"}
    assert set(fc["stations"]) == {"short_name", "name", "lat", "lon", "cap"}
    q = len(fc["minutes"])
    expected = P.NB15 - 4 if horizonte == "dia" else 4 * int(horizonte)
    if modelo == "lgbm_directo":
        assert fc["referencia"]["viajes_del_dia"] == "inferidos_feed"
        assert fc["referencia"]["feed_hoy"]["completo"] is True
    else:
        assert fc["referencia"]["viajes_del_dia"] is None
    assert q == expected and fc["minutes"][0] == 15
    assert len(fc["proyeccion"]) == len(fc["salidas"]) == len(fc["llegadas"]) == q
    assert all(len(row) == len(UNIVERSE) for row in fc["proyeccion"])
    assert fc["t"] == "2025-09-20T06:00" and fc["t_feed"] == "2025-09-20T06:07:00"
    for r in fc["riesgos"]:
        assert r["tipo"] in ("vacia", "llena") and r["minutos"] % 15 == 0
    for bad in ("5", "0", "x", "1" if horizonte == "dia" else "dia"):
        with pytest.raises(ValueError, match="horizonte inválido"):
            live.pronosticar(modelo, bad, log, t_feed, prod=prod)


@pytest.mark.parametrize("modelo,bad,pista", [
    ("ma_diaria", "1", "horizonte=dia"), ("ma_diaria", "4", "horizonte=dia"),
    ("lgbm_diario", "2", "horizonte=dia"), ("lgbm_directo", "dia", "1, 2, 3 o 4")])
def test_combinaciones_sin_sentido_se_rechazan(modelo, bad, pista):
    with pytest.raises(ValueError, match="horizonte inválido") as exc:
        live.validar_horizonte(modelo, bad)
    assert pista in str(exc.value)
    assert live.validar_horizonte("lgbm_directo", 3) == 3 and live.validar_horizonte("ma_diaria", "dia") is None


def test_pronostico_fuera_de_horario(prod):
    t_feed = datetime.combine(D, C.DAY_START) - timedelta(hours=2)
    log = _feed_from_trips(_trips(), t_feed - timedelta(minutes=5), t_feed)
    with pytest.raises(LookupError, match="fuera del horario"):
        live.pronosticar("ma_diaria", "dia", log, t_feed, prod=prod)


# --- asignación en tiempo real con feed y reloj simulados -------------------------

PASO_KEYS = {"t", "t_feed", "emitidas", "aplicadas", "bicis_a_mover", "visitas", "salidas_est", "llegadas_est"}
ORDEN_KEYS = {"short_name", "accion", "n", "emitida", "recoge", "entrega"}


class Reloj:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


def _snapshot_fn(reloj):
    def snap():
        k = int((reloj.t - datetime(2025, 9, 20, 9)).total_seconds() // 900)
        bikes = {"001": 20, "002": 0, "003": 10 + (k % 3)}
        epoch = (reloj.t - timedelta(hours=C.UTC_OFFSET_HOURS) - datetime(1970, 1, 1)).total_seconds()
        return {"last_updated": epoch, "stations": [
            {"short_name": sn, "name": f"Estación {sn}", "lat": 19.4, "lon": -99.1, "capacity": 20,
             "bikes": b, "docks": 20 - b, "bikes_disabled": 0, "renting": True, "installed": True}
            for sn, b in bikes.items()]}
    return snap


def test_sesion_de_dos_horas_cierra_entregas(prod, tmp_path):
    reloj = Reloj(datetime(2025, 9, 20, 9, 7))
    loop = live.LiveLoop(snapshot_fn=_snapshot_fn(reloj), log_dir=tmp_path / "gbfs", clock=reloj)
    t = loop.poll()
    fc = live.pronosticar("ma_diaria", "dia", loop.log_today(t), t, loop.meta, prod=prod)
    live.guardar_pronostico(fc, tmp_path / "fc")
    asig = live.Asignaciones(loop, tmp_path / "ses", tmp_path / "fc", clock=reloj, sleep=reloj.sleep, prod=prod, fz=FZ)
    sid = asig.start(fc["id"], 2, thread=False)
    s = asig.get(sid)
    assert s["estado"] == "terminada" and s["siguiente_paso"] is None and s["pendientes"] == []
    assert s["inicio"] == "2025-09-20T09:00" and s["horas"] == 2
    # 8 pasos que emiten (09:00–10:45) y 4 que solo cierran entregas (11:00–11:45).
    assert [p["t"] for p in s["pasos"]] == [f"2025-09-20T{9 + k // 4:02d}:{15 * (k % 4):02d}" for k in range(12)]
    assert all(PASO_KEYS <= set(p) for p in s["pasos"])
    assert all(p["visitas"] == 0 for p in s["pasos"][8:])
    emitidas = [o for p in s["pasos"] for o in p["emitidas"]]
    aplicadas = [o for p in s["pasos"] for o in p["aplicadas"]]
    assert emitidas, "el asignador no emitió órdenes con una estación llena y otra vacía"
    assert all(ORDEN_KEYS <= set(o) for o in emitidas)
    assert sorted(map(json.dumps, emitidas)) == sorted(map(json.dumps, aplicadas))
    for p in s["pasos"]:
        for o in p["aplicadas"]:
            assert p["t"] == (o["recoge"] if o["accion"] == "recoger" else o["entrega"])
        for o in p["emitidas"]:
            t0 = datetime.fromisoformat(o["emitida"])
            assert o["emitida"] == p["t"]
            assert datetime.fromisoformat(o["recoge"]) == t0 + timedelta(minutes=15)
            assert datetime.fromisoformat(o["entrega"]) == t0 + timedelta(minutes=60)
    tot = s["totales"]
    assert tot["recogidas"] == tot["entregadas"] == tot["bicis_a_mover"] > 0
    assert tot["visitas"] == len(emitidas)
    sin_metricas = {"E", "F", "EF", "EF_acum", "ahorro"}
    assert not sin_metricas & (set(s) | set(tot) | {k for p in s["pasos"] for k in p})
    # Sobrevive a recargar: otra instancia lee la sesión del disco.
    assert live.Asignaciones(loop, tmp_path / "ses", tmp_path / "fc").get(sid)["estado"] == "terminada"


def test_sesion_detenida_y_reinicio(prod, tmp_path):
    reloj = Reloj(datetime(2025, 9, 20, 9, 7))
    loop = live.LiveLoop(snapshot_fn=_snapshot_fn(reloj), log_dir=tmp_path / "gbfs", clock=reloj)
    t = loop.poll()
    fc = live.pronosticar("ma_diaria", "dia", loop.log_today(t), t, loop.meta, prod=prod)
    live.guardar_pronostico(fc, tmp_path / "fc")
    s = live.Sesion("x", fc, 1, loop, tmp_path / "ses", clock=reloj, sleep=reloj.sleep, prod=prod, fz=FZ)
    s.stop_event.set()
    s.run()
    assert s.state["estado"] == "detenida"
    # Un JSON "corriendo" de otro proceso se marca como error al arrancar.
    p = tmp_path / "ses" / "viejo.json"
    p.write_text(json.dumps({"id": "viejo", "estado": "corriendo", "siguiente_paso": "2025-09-20T08:00"}))
    # Otra sesión cuyo siguiente paso aún no llega: la corre otro proceso, no se toca.
    vivo = tmp_path / "ses" / "vivo.json"
    vivo.write_text(json.dumps({"id": "vivo", "estado": "corriendo", "siguiente_paso": "2025-09-20T09:15"}))
    asig = live.Asignaciones(loop, tmp_path / "ses", tmp_path / "fc", clock=reloj, sleep=reloj.sleep, prod=prod, fz=FZ)
    assert json.loads(p.read_text())["estado"] == "error"
    assert json.loads(vivo.read_text())["estado"] == "corriendo"
    with pytest.raises(ValueError, match="horas"):
        asig.start(fc["id"], 0, thread=False)
    with pytest.raises(KeyError):
        asig.start("no-existe", 1, thread=False)


def test_modelos_disponibles_y_oraculos_fuera(prod, monkeypatch):
    monkeypatch.setattr(live, "produccion", lambda prod_dir=None: prod)
    info = live.models_info(now=datetime.combine(D, C.DAY_START) + timedelta(hours=3), fz=FZ)
    keys = [m["key"] for m in info["modelos"]]
    assert keys == ["ma_diaria", "lgbm_diario", "lgbm_directo"]
    assert all(m["disponible"] and m["motivo"] is None for m in info["modelos"])
    assert set(info["datos"]) >= {"ultimo_dia_publicado", "actualizado"}
    for m in info["modelos"]:
        assert {"key", "forma", "label", "disponible", "motivo", "corte", "n", "lambda", "nota", "horizontes"} <= set(m)
    assert {m["key"]: m["horizontes"] for m in info["modelos"]} == {
        "ma_diaria": ["dia"], "lgbm_diario": ["dia"], "lgbm_directo": [1, 2, 3, 4]}


# --- watcher del feed: lecturas solo cuando cambia, pasos disparados por el feed ---

class FeedSim:
    """Feed que solo cambia en los instantes `cambios` (hora local); entre cambios
    devuelve exactamente lo mismo (misma last_updated y mismo contenido)."""

    def __init__(self, reloj, cambios):
        self.reloj, self.cambios = reloj, sorted(cambios)

    def __call__(self):
        hechos = [c for c in self.cambios if c <= self.reloj.t]
        k = len(hechos)
        t = hechos[-1] if hechos else self.cambios[0] - timedelta(minutes=1)
        epoch = (t - timedelta(hours=C.UTC_OFFSET_HOURS) - datetime(1970, 1, 1)).total_seconds()
        bikes = {"001": 20, "002": max(0, 2 - k % 3), "003": 10 + (k % 4)}  # cambios chicos: viajes, no camioneta
        return {"last_updated": epoch, "stations": [
            {"short_name": sn, "name": f"Estación {sn}", "lat": 19.4, "lon": -99.1, "capacity": 20,
             "bikes": b, "docks": 20 - b, "bikes_disabled": 0, "renting": True, "installed": True}
            for sn, b in bikes.items()]}


def test_lecturas_iguales_no_generan_evento(tmp_path):
    reloj = Reloj(datetime(2025, 9, 20, 9, 7))
    feed = FeedSim(reloj, [datetime(2025, 9, 20, 9, 0), datetime(2025, 9, 20, 9, 8)])
    loop = live.LiveLoop(snapshot_fn=feed, log_dir=tmp_path, clock=reloj)
    loop.poll()
    reloj.sleep(15)
    loop.poll()  # mismo feed: no es lectura
    assert len(loop.lecturas) == 1 and loop.eventos.n == 1
    assert loop.lecturas[0]["primera"] is True and loop.lecturas[0]["estaciones_cambiaron"] is None
    reloj.sleep(60)  # 09:08:15: el feed cambió a las 09:08
    loop.poll()
    loop.poll()
    assert len(loop.lecturas) == 2 and loop.eventos.n == 2
    lect = loop.lecturas[-1]
    assert lect["t_feed"] == "2025-09-20T09:08:00" and lect["t_detectado"] == "2025-09-20T09:08:15"
    assert lect["estaciones_cambiaron"] == 2
    assert (lect["salidas_est"], lect["llegadas_est"], lect["saltos_camioneta"]) == (1, 1, 0)  # 002: 2→1, 003: 10→11
    for k in ("bicis_disponibles", "no_rentables", "anclajes_libres", "estaciones_vacias", "estaciones_llenas"):
        assert k in lect
    assert lect["estaciones_llenas"] == 1 and lect["bicis_disponibles"] == 20 + 1 + 11
    info = loop.feed_info()
    assert info["lecturas_hoy"] == 2 and info["ultimo_cambio"] == "2025-09-20T09:08:15"
    assert info["desde"] == "2025-09-20T09:00:00" and info["poll_s"] == live.POLL_S
    assert set(info) >= {"ultima_lectura", "ultimo_cambio", "lecturas_hoy", "desde", "poll_s", "lecturas"}


def test_sse_emite_eventos():
    ev = live.Eventos(clock=lambda: datetime(2025, 9, 20, 9, 0))
    ev.publish("feed", {"t_feed": "2025-09-20T08:59:50"})
    gen = ev.sse(latido_s=0.01, max_eventos=3)
    assert next(gen).startswith("retry:")
    first = next(gen)  # la última lectura del feed al conectarse
    assert first.startswith("id: 1\nevent: feed\ndata: ") and first.endswith("\n\n")
    ev.publish("paso", {"sesion": "s1", "t": "2025-09-20T09:00"})
    second = next(gen)
    data = json.loads(second.split("data: ", 1)[1])
    assert "event: paso" in second and data["id"] == 2 and data["hora"] == "2025-09-20T09:00:00"
    assert next(gen) == ": latido\n\n"  # sin eventos: latido
    ev.publish("sesion", {"sesion": "s1", "estado": "terminada"})
    assert "event: sesion" in next(gen)
    with pytest.raises(StopIteration):
        next(gen)
    assert ev.subs == []


def _sesion(prod, tmp_path, cambios, modelo="ma_diaria", horizonte="dia", horas=1):
    reloj = Reloj(datetime(2025, 9, 20, 9, 7))
    loop = live.LiveLoop(snapshot_fn=FeedSim(reloj, cambios), log_dir=tmp_path / "gbfs", clock=reloj)
    t = loop.poll()
    fc = live.pronosticar(modelo, horizonte, loop.log_today(t), t, loop.meta, prod=prod)
    live.guardar_pronostico(fc, tmp_path / "fc")
    asig = live.Asignaciones(loop, tmp_path / "ses", tmp_path / "fc", clock=reloj, sleep=reloj.sleep, prod=prod, fz=FZ)
    sid = asig.start(fc["id"], horas, thread=False)
    return asig.get(sid), loop


def test_paso_espera_el_cambio_del_feed_despues_de_la_marca(prod, tmp_path):
    marcas = [datetime(2025, 9, 20, 9, 0) + timedelta(minutes=15 * k) for k in range(12)]
    cambios = [datetime(2025, 9, 20, 9, 5)] + [m + timedelta(seconds=70) for m in marcas[1:]]
    s, loop = _sesion(prod, tmp_path, cambios)
    assert s["estado"] == "terminada"
    assert set(s["inicio_estado"]) == set(live.ESTADO_KEYS) and s["inicio_estado"]["t_feed"] == "2025-09-20T09:05:00"
    p0 = s["pasos"][0]
    assert p0["disparo"] == "inicio" and p0["espera_s"] == 0 and p0["estado_feed"] == s["inicio_estado"]
    for p in s["pasos"][1:]:
        t = datetime.fromisoformat(p["t"])
        assert p["disparo"] == "cambio_feed" and p["feed_atrasado"] is False
        assert datetime.fromisoformat(p["t_feed"]) == t + timedelta(seconds=70)  # la lectura de después de la marca
        assert 70 <= p["espera_s"] <= 70 + live.POLL_S
        assert p["estado_feed"]["t_feed"] == p["t_feed"][:19]
        assert [x["t_feed"] for x in p["lecturas_desde_paso"]] == [p["t_feed"]]
    tipos = [e["evento"] for e in loop.eventos.recientes]
    assert tipos.count("paso") == len(s["pasos"]) and tipos[-1] == "sesion"
    assert loop.eventos.ultimo["sesion"]["estado"] == "terminada"


def test_feed_sin_cambios_corre_a_los_cinco_minutos(prod, tmp_path):
    s, _ = _sesion(prod, tmp_path, [datetime(2025, 9, 20, 9, 5)])  # el feed nunca vuelve a cambiar
    assert s["estado"] == "terminada"
    for p in s["pasos"][1:]:
        assert p["disparo"] == "sin_cambio_feed" and p["feed_atrasado"] is True
        assert live.ESPERA_MAX_S <= p["espera_s"] < live.ESPERA_MAX_S + 6
        assert p["t_feed"] == "2025-09-20T09:05:00" and p["lecturas_desde_paso"] == []
    # Con el feed 25 min atrás ya no se emiten órdenes; se dice por qué.
    assert any("feed desfasado" in (p["nota"] or "") for p in s["pasos"])


def test_stop_se_atiende_mientras_espera_la_marca(prod, tmp_path):
    reloj = Reloj(datetime(2025, 9, 20, 9, 7))
    loop = live.LiveLoop(snapshot_fn=FeedSim(reloj, [datetime(2025, 9, 20, 9, 5)]), log_dir=tmp_path / "gbfs", clock=reloj)
    t = loop.poll()
    fc = live.pronosticar("ma_diaria", "dia", loop.log_today(t), t, loop.meta, prod=prod)
    s = live.Sesion("s", fc, 2, loop, tmp_path / "ses", clock=reloj, prod=prod, fz=FZ)

    def sleep(seg):  # el usuario pulsa Detener a los 2 min, antes de la marca de 09:15
        reloj.sleep(seg)
        if reloj.t >= datetime(2025, 9, 20, 9, 9):
            s.stop_event.set()
    s.sleep = sleep
    s.run()
    assert s.state["estado"] == "detenida" and len(s.state["pasos"]) == 1
    assert reloj.t < datetime(2025, 9, 20, 9, 15)
    assert loop.eventos.ultimo["sesion"]["estado"] == "detenida"


def test_sesion_corta_con_lgbm_directo(prod, tmp_path):
    marcas = [datetime(2025, 9, 20, 9, 0) + timedelta(minutes=15 * k) for k in range(12)]
    s, _ = _sesion(prod, tmp_path, [datetime(2025, 9, 20, 9, 5)] + [m + timedelta(seconds=20) for m in marcas[1:]],
                   modelo="lgbm_directo", horizonte="1")
    assert s["estado"] == "terminada" and s["model"] == "lgbm_directo", s["error"]
    assert len(s["pasos"]) == 8 and s["pendientes"] == []
    assert s["totales"]["recogidas"] == s["totales"]["entregadas"] == s["totales"]["bicis_a_mover"]
    assert all(PASO_KEYS <= set(p) for p in s["pasos"])
