"""Servidor local del mapa Ecobici en tiempo real.

El feed GBFS de Ecobici no manda header Access-Control-Allow-Origin, asi que
el navegador bloquea el fetch directo. Este servidor resuelve eso: sirve el
mapa estatico y actua de proxy hacia GBFS, cacheando segun el ttl del feed.
Tambien expone /forecast/{short_name}, que junta la prediccion de bicis (un
parquet, ver PREDICTIONS_PATH) con el nivel y capacidad en vivo de esa
estacion.

    uv run python3 server.py                       # http://localhost:8000 (abre el navegador)
    uv run python3 server.py --no-browser --port 9000
    uv run python3 server.py --no-live             # sin el loop de prediccion en vivo

Pestañas del producto final (ecosim, run 2):
- Replay: sirve los frames precalculados por `ecosim/replay.py` desde
  data/derived/ecosim/replay/ (/api/replay/...).
- En vivo: un hilo (`ecosim.live.LiveLoop`) lee el feed cada 60 s, lo
  guarda en data/derived/ecosim/live/gbfs/ y cada 15 min corre pronostico +
  asignador (/api/live/...). Ver README.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from threading import Lock

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

GBFS_BASE = "https://gbfs.mex.lyftbikes.com/gbfs/es"

# station_information es estatico (lat/lon, capacidad): TTL largo.
# station_status declara ttl:10 en el feed; 10s es innecesariamente agresivo
# para un mapa que un humano mira, 15s mantiene el dato fresco sin martillar.
FEEDS = {
    "station_information": 3600,
    "station_status": 15,
    "system_alerts": 300,
}

HERE = Path(__file__).parent
MODELS_DIR = HERE.parent.parent / "models"
PREDICTIONS_PATH = MODELS_DIR / "predictions.parquet"
PREDICTIONS_FIXTURE_PATH = MODELS_DIR / "predictions_fixture.parquet"
DERIVED_DIR = HERE.parent.parent / "data" / "derived"
REBALANCE_DAY = "2025-09-17"
REBALANCE_ROUTES_PATH = DERIVED_DIR / f"rebalance_routes_{REBALANCE_DAY}.parquet"
REBALANCE_MOVES_PATH = DERIVED_DIR / f"rebalance_moves_{REBALANCE_DAY}.parquet"
DAILY_SNAPSHOTS_PATH = DERIVED_DIR / "daily_snapshots.parquet"
REPO_ROOT = HERE.parent.parent
REPLAY_DIR = DERIVED_DIR / "ecosim" / "replay"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ARM_RE = re.compile(r"^[a-z_]+$")

# ecosim vive en la raiz del repo; server.py corre desde scripts/ecobici_mapa.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FAVICON = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<rect width="32" height="32" rx="7" fill="#0b0e11"/>'
    b'<circle cx="10" cy="20" r="5.5" fill="none" stroke="#33d17a" stroke-width="2"/>'
    b'<circle cx="22" cy="20" r="5.5" fill="none" stroke="#33d17a" stroke-width="2"/>'
    b'<path d="M10 20 L15 11 L22 20 M15 11 L19 11" fill="none" stroke="#33d17a" '
    b'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    b"</svg>"
)


class Cache:
    """Cache en memoria con TTL por feed, compartido entre threads.

    Si el fetch al feed falla y ya hay un valor cacheado (aunque este vencido),
    se devuelve ese valor viejo en vez de propagar el error: un corte de red
    momentaneo no debe vaciar el mapa, solo dejar de refrescarlo.
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, bytes]] = {}
        self._lock = Lock()

    def get(self, feed: str, ttl: int) -> tuple[bytes, bool]:
        """Devuelve (payload, is_stale_fallback)."""
        with self._lock:
            hit = self._data.get(feed)
            if hit and time.time() - hit[0] < ttl:
                return hit[1], False

        # El fetch va fuera del lock: si dos requests llegan juntas con el
        # cache frio, ambas bajan el feed. Es desperdicio menor y aceptable;
        # lo caro seria serializar todas las requests detras de una red lenta.
        try:
            req = urllib.request.Request(
                f"{GBFS_BASE}/{feed}.json",
                headers={"User-Agent": "movilidad-cdmx/mapa-ecobici"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = resp.read()
        except Exception:
            with self._lock:
                hit = self._data.get(feed)
            if hit:
                return hit[1], True
            raise

        with self._lock:
            self._data[feed] = (time.time(), payload)
        return payload, False


CACHE = Cache()


def parse_station_numbers(text: str) -> set[str]:
    """Extrae numeros de estacion del texto libre de una alerta.

    Ecobici no llena `station_ids`; escribe los numeros en prosa, con rangos
    ("264 a 269", "271 a 275"). El texto tambien trae fechas ("del 13 al 16
    de septiembre") que NO son estaciones, asi que se recortan los tramos con
    marcas de fecha antes de buscar numeros.
    """
    if not text:
        return set()

    # Quita fragmentos de fecha: "del 13 al 16 de septiembre", "5 de enero".
    cleaned = re.sub(
        r"\bdel?\s+\d{1,2}\s+(?:al?\s+\d{1,2}\s+)?de\s+\w+", " ", text, flags=re.I
    )
    cleaned = re.sub(r"\b\d{1,2}\s+de\s+\w+", " ", cleaned, flags=re.I)

    found: set[int] = set()
    for chunk in re.split(r"[.;]", cleaned):
        # Rangos "264 a 269" / "264 al 269".
        for lo, hi in re.findall(r"\b(\d{1,4})\s+al?\s+(\d{1,4})\b", chunk):
            lo_i, hi_i = int(lo), int(hi)
            # Un rango invertido o absurdamente ancho es señal de que no era
            # una lista de estaciones; se ignora en vez de marcar media red.
            if 0 < hi_i - lo_i <= 60:
                found.update(range(lo_i, hi_i + 1))
        for num in re.findall(r"\b(\d{1,4})\b", chunk):
            found.add(int(num))

    return {str(n) for n in found}


def short_name_keys(short_name: str) -> set[str]:
    """Numeros de estacion que cubre un `short_name` del feed.

    Casi siempre es un solo numero con ceros a la izquierda ("033" -> {"33"}),
    pero el feed tambien trae estaciones compuestas, que son una estacion
    fisica agrupando varios numeros: "268-269" -> {"268","269"} y el rango
    "264-275" -> {"264"..."275"}. Sin expandirlas, una alerta que menciona
    "264 a 269" no marcaria la estacion que el feed llama "264-275".
    """
    s = str(short_name or "").strip()
    if not s:
        return set()

    parts = re.findall(r"\d+", s)
    if not parts:
        return set()
    if len(parts) == 1:
        return {str(int(parts[0]))}

    lo, hi = int(parts[0]), int(parts[-1])
    # Un compuesto es un rango solo si es ascendente y estrecho; si no, se
    # toman los numeros literales que aparecen y ya.
    if 0 < hi - lo <= 60:
        return {str(n) for n in range(lo, hi + 1)}
    return {str(int(p)) for p in parts}


def build_snapshot() -> dict:
    """Une los tres feeds en un solo objeto listo para el mapa."""
    info_raw, _ = CACHE.get("station_information", FEEDS["station_information"])
    status_raw, status_stale = CACHE.get("station_status", FEEDS["station_status"])
    info = json.loads(info_raw)
    status = json.loads(status_raw)

    # system_alerts es accesorio: si falla, el mapa debe seguir funcionando.
    try:
        alerts_raw, _ = CACHE.get("system_alerts", FEEDS["system_alerts"])
        alerts = json.loads(alerts_raw)["data"].get("alerts", [])
    except Exception:
        alerts = []

    by_id = {s["station_id"]: s for s in status["data"]["stations"]}

    # `alerted_ids` son station_id (espacio de identificadores interno);
    # `alerted_names` son numeros de estacion del texto, que corresponden a
    # `short_name`. Son espacios DISTINTOS: la estacion con short_name "033"
    # tiene station_id "545". Cruzarlos marca estaciones equivocadas, asi que
    # cada conjunto se compara solo contra su propio campo.
    alerted_ids: set[str] = set()
    alerted_names: set[str] = set()
    for alert in alerts:
        # El campo estandar de GBFS, cuando el operador lo llena.
        for sid in alert.get("station_ids", []) or []:
            alerted_ids.add(str(sid))
        # En la practica Ecobici no lo llena: manda los numeros de estacion
        # dentro del texto libre de `description` ("...Cuauhtemoc: 176, 264 a
        # 269 y 271 a 275..."). Se extraen de ahi, incluyendo los rangos.
        alerted_names |= parse_station_numbers(alert.get("description", ""))

    stations = []
    for st in info["data"]["stations"]:
        live = by_id.get(st["station_id"])
        if not live:
            continue
        stations.append(
            {
                "id": st["station_id"],
                "name": st["name"],
                "short_name": st.get("short_name", ""),
                "lat": st["lat"],
                "lon": st["lon"],
                "capacity": st.get("capacity", 0),
                "bikes": live["num_bikes_available"],
                "docks": live["num_docks_available"],
                "bikes_disabled": live.get("num_bikes_disabled", 0),
                "docks_disabled": live.get("num_docks_disabled", 0),
                "renting": bool(live.get("is_renting", 0)),
                "returning": bool(live.get("is_returning", 0)),
                "installed": bool(live.get("is_installed", 0)),
                "last_reported": live.get("last_reported"),
                "alerted": st["station_id"] in alerted_ids
                or bool(short_name_keys(st.get("short_name", "")) & alerted_names),
            }
        )

    return {
        "generated_at": int(time.time()),
        "last_updated": status.get("last_updated"),
        "stale": status_stale,
        "stations": stations,
        "alerts": [
            {
                "type": a.get("type"),
                "summary": a.get("summary"),
                "description": a.get("description"),
                "station_ids": a.get("station_ids", []),
            }
            for a in alerts
        ],
    }


def find_station(short_name: str) -> dict:
    """Busca una estacion en el snapshot en vivo por short_name.

    short_name y station_id son espacios de identificadores distintos (ver
    README); esta funcion solo compara contra short_name, nunca contra id.
    """
    snapshot = build_snapshot()
    for st in snapshot["stations"]:
        if st["short_name"] == short_name:
            return st
    raise HTTPException(404, f"Estacion con short_name={short_name!r} no encontrada")


def load_daily_snapshots() -> pd.DataFrame:
    """Carga data/derived/daily_snapshots.parquet (ver scripts/fetch_daily_snapshots.py).

    Dos filas por estacion y dia: snapshot_slot "05:30" y "12:30", cada una
    el snapshot real mas cercano a esa hora CDMX (no exactamente esa hora:
    el collector de origen tiene huecos). committed_at_utc guarda el momento
    real capturado para que la UI pueda mostrar el desfase.
    """
    if not DAILY_SNAPSHOTS_PATH.exists():
        raise HTTPException(
            503,
            "Sin snapshots diarios: falta data/derived/daily_snapshots.parquet "
            "(correr scripts/fetch_daily_snapshots.py)",
        )
    return pd.read_parquet(DAILY_SNAPSHOTS_PATH)


def build_daily_snapshot(date: str, slot: str) -> dict:
    """Un dia+slot de daily_snapshots.parquet, en el mismo shape que
    build_snapshot(), para que el frontend reuse toda la logica de color y
    tarjeta de la pestaña 'Bicis ahora' sin duplicar codigo."""
    df = load_daily_snapshots()
    rows = df[(df["date"].astype(str) == date) & (df["snapshot_slot"] == slot)]
    if rows.empty:
        raise HTTPException(404, f"Sin snapshot para date={date!r} slot={slot!r}")

    committed = rows["committed_at_utc"].iloc[0]
    stations = [
        {
            "id": r.station_id,
            "name": r.name,
            "short_name": r.short_name,
            "lat": r.latitude,
            "lon": r.longitude,
            "capacity": int(r.capacity or 0),
            "bikes": int(r.num_bikes_available),
            "docks": int(r.num_docks_available),
            "bikes_disabled": int(r.num_bikes_disabled),
            "docks_disabled": int(r.num_docks_disabled),
            "renting": bool(r.is_renting),
            "returning": bool(r.is_returning),
            "installed": bool(r.is_installed),
            "alerted": False,
        }
        for r in rows.itertuples()
    ]

    return {
        "date": date,
        "snapshot_slot": slot,
        "committed_at": committed.isoformat(),
        "stations": stations,
    }


def load_predictions() -> tuple[pd.DataFrame, bool]:
    """Carga el parquet de predicciones real, o el fixture si aun no existe.

    Devuelve (dataframe, used_fixture). El modelo lo entrena otro worker en
    paralelo: en cuanto predictions.parquet exista, se usa automaticamente,
    sin cambio de codigo.
    """
    if PREDICTIONS_PATH.exists():
        return pd.read_parquet(PREDICTIONS_PATH), False
    if PREDICTIONS_FIXTURE_PATH.exists():
        return pd.read_parquet(PREDICTIONS_FIXTURE_PATH), True
    raise HTTPException(
        503,
        "Sin datos de prediccion: falta models/predictions.parquet y "
        "models/predictions_fixture.parquet (correr make_fixture.py)",
    )


Z90 = 1.2815515655446004  # z tal que Phi(z) = 0.9, para pasar de p10/p90 a sigma


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _mean_sd_from_quantiles(p10: float, p50: float, p90: float) -> tuple[float, float]:
    """Aproxima (media, sigma) de una normal a partir de sus cuantiles p10/p50/p90.

    p50 es la media bajo simetria; sigma sale del semi-ancho p90-p10 dividido
    entre 2*Z90. Es una aproximacion (el fixture y el modelo real no
    garantizan normalidad), pero es la misma familia de cuantiles que ya
    manda el esquema, no una cantidad inventada aparte.
    """
    sd = max((p90 - p10) / (2 * Z90), 1e-6)
    return p50, sd


def build_forecast(short_name: str) -> dict:
    """Combina prediccion (parquet) con nivel/capacidad en vivo de la estacion.

    bikes_at(t+h) = clamp(bikes_now - dep_pred + arr_pred, 0, capacity)
    aplicado a cada cuantil (p10/p50/p90) de salidas y llegadas.

    P(empty) = P(bikes_now - dep + arr <= 0), la cola inferior real de la
    distribucion compuesta, no una cantidad aparte: dep y arr se aproximan
    como normales a partir de sus propios cuantiles (mismo esquema que ya
    manda el modelo), su diferencia tambien es normal (var_dep + var_arr), y
    P(empty) es el CDF de esa normal evaluado en bikes_now. Esto es monotono
    en el nivel esperado y coherente con p10/p50/p90 por construccion.
    """
    station = find_station(short_name)
    bikes_now = station["bikes"]
    capacity = station["capacity"] or (station["bikes"] + station["docks"]) or 1

    df, used_fixture = load_predictions()
    rows = df[df["short_name"] == short_name].sort_values("horizon_min")

    if rows.empty:
        raise HTTPException(
            404, f"Sin prediccion para short_name={short_name!r} en el parquet"
        )

    horizons = []
    for _, row in rows.iterrows():
        def bikes_for(dep: float, arr: float) -> float:
            return min(max(bikes_now - dep + arr, 0.0), capacity)

        # p10 de bicis restantes = escenario mas pesimista: mas salidas (p90
        # de dep) y menos llegadas (p10 de arr). p90 es el espejo.
        p10 = bikes_for(row["dep_p90"], row["arr_p10"])
        p50 = bikes_for(row["dep_p50"], row["arr_p50"])
        p90 = bikes_for(row["dep_p10"], row["arr_p90"])
        p10, p90 = min(p10, p90), max(p10, p90)
        p50 = min(max(p50, p10), p90)

        # net = dep - arr (bicis que se van, netas); bikes_at = bikes_now - net.
        dep_mean, dep_sd = _mean_sd_from_quantiles(
            row["dep_p10"], row["dep_p50"], row["dep_p90"]
        )
        arr_mean, arr_sd = _mean_sd_from_quantiles(
            row["arr_p10"], row["arr_p50"], row["arr_p90"]
        )
        net_mean = dep_mean - arr_mean
        net_sd = math.sqrt(dep_sd**2 + arr_sd**2)
        # P(bikes_now - net <= 0) = P(net >= bikes_now) = 1 - CDF_net(bikes_now).
        z = (bikes_now - net_mean) / net_sd
        p_empty = 1.0 - _normal_cdf(z)

        horizons.append(
            {
                "horizon_min": int(row["horizon_min"]),
                "p10": round(p10, 1),
                "p50": round(p50, 1),
                "p90": round(p90, 1),
                "p_empty": round(p_empty, 3),
            }
        )

    return {
        "short_name": short_name,
        "station_id": station["id"],
        "name": station["name"],
        "bikes_now": bikes_now,
        "capacity": capacity,
        "used_fixture": used_fixture,
        "horizons": horizons,
    }


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/snapshot")
def api_snapshot() -> Response:
    try:
        body = json.dumps(build_snapshot()).encode()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return Response(
        body, media_type="application/json", headers={"Cache-Control": "no-store"}
    )


@app.get("/api/snapshots/dates")
def api_snapshot_dates() -> dict:
    """Fechas disponibles en daily_snapshots.parquet, para poblar el selector
    de la pestaña Snapshots. Ambos slots existen para toda fecha guardada
    porque fetch_daily_snapshots.py siempre descarga los dos juntos."""
    df = load_daily_snapshots()
    dates = sorted(df["date"].astype(str).unique(), reverse=True)
    return {"dates": dates, "slots": ["05:30", "12:30"]}


@app.get("/api/snapshots/{date}/{slot}")
def api_snapshot_at(date: str, slot: str) -> dict:
    return build_daily_snapshot(date, slot)


@app.get("/forecast/{short_name}")
def forecast(short_name: str) -> dict:
    return build_forecast(short_name)


@app.get("/api/rebalance")
def api_rebalance() -> dict:
    """Discontinuidades entre viajes para REBALANCE_DAY, agregadas por par de
    estaciones y por days_crossed (0 = misma fecha calendario, 1 = cruzo de
    la vispera a este dia).

    Generado por `analysis/rebalance_routes.py`: una bici termina un viaje en
    A y su siguiente viaje empieza en B != A. Es evidencia de una reubicación
    entre viajes, compatible con operación de rebalanceo, mantenimiento o
    retiro; no identifica un camión ni su ruta.

    Sin filtro de horario: la version anterior solo contaba saltos dentro de
    una ventana nocturna fija (18:00-00:35 -> 05:00-10:00), asumiendo que el
    rebalanceo es un fenomeno exclusivamente nocturno. Medido sobre el
    dataset completo (ver wiki/Rebalanceo-Ecobici-suposicion-horario.md), la
    mayoria de los saltos detectados (70.6%) ocurre el mismo dia calendario
    en que se dejo la bici, no de un dia a otro -- por eso se sirven ambas
    categorias en vez de solo la nocturna.
    """
    if not REBALANCE_ROUTES_PATH.exists():
        raise HTTPException(status_code=404, detail="rebalance routes fixture not built")
    routes = pd.read_parquet(REBALANCE_ROUTES_PATH)
    return {
        "day": REBALANCE_DAY,
        "n_routes": len(routes),
        "n_bikes": int(routes.n_bikes.sum()),
        "n_bikes_same_day": int(routes[routes.days_crossed == 0].n_bikes.sum()),
        "n_bikes_overnight": int(routes[routes.days_crossed == 1].n_bikes.sum()),
        "routes": routes.to_dict(orient="records"),
    }


@app.get("/api/rebalance/station/{short_name}")
def api_rebalance_station(short_name: str) -> dict:
    """Per-bike detail for one station: how many bikes it had, and every
    tracked relocation in or out of it that night, with each bike's ID."""
    if not REBALANCE_MOVES_PATH.exists():
        raise HTTPException(status_code=404, detail="rebalance moves fixture not built")
    moves = pd.read_parquet(REBALANCE_MOVES_PATH)

    out = moves[moves.from_station == short_name].copy()
    out["direction"] = "out"
    out["other_station"] = out["to_station"]
    inn = moves[moves.to_station == short_name].copy()
    inn["direction"] = "in"
    inn["other_station"] = inn["from_station"]

    combined = pd.concat([out, inn], ignore_index=True).sort_values("arr_dt")
    if combined.empty:
        return {"short_name": short_name, "bikes_at_close": None, "capacity": None, "moves": []}

    bikes_at_close = combined["from_bikes_at_close"].iloc[0] if not out.empty else combined["to_bikes_at_open"].iloc[0]
    capacity = combined["from_capacity"].iloc[0] if not out.empty else combined["to_capacity"].iloc[0]

    return {
        "short_name": short_name,
        "bikes_at_close": None if pd.isna(bikes_at_close) else float(bikes_at_close),
        "capacity": None if pd.isna(capacity) else float(capacity),
        "n_out": int((combined.direction == "out").sum()),
        "n_in": int((combined.direction == "in").sum()),
        "moves": [
            {
                "bici": int(r.Bici),
                "direction": r.direction,
                "other_station": r.other_station,
                "arr_time": r.arr_dt.isoformat(),
                "next_ret_time": r.next_ret_dt.isoformat(),
                "days_crossed": int(r.days_crossed),
            }
            for r in combined.itertuples()
        ],
    }


# ---------------------------------------------------------------------------
# Replay de un dia (ecosim/replay.py precalcula; aqui solo se sirven archivos)
# ---------------------------------------------------------------------------

def _json_file(path: Path) -> Response:
    if not path.exists():
        raise HTTPException(
            404, f"{path.relative_to(REPO_ROOT)} no existe: corre `uv run python -m ecosim.replay`"
        )
    return FileResponse(path, media_type="application/json")


@app.get("/api/replay/index")
def api_replay_index() -> dict:
    """Dias y brazos precalculados, con E+F final y su comparacion contra
    resultados.csv. `stale` = el frozen.json actual no es con el que se
    precalculo (hay que volver a correr ecosim.replay)."""
    p = REPLAY_DIR / "index.json"
    if not p.exists():
        raise HTTPException(404, "sin replay: corre `uv run python -m ecosim.replay`")
    idx = json.loads(p.read_text())
    try:
        from ecosim import replay
        cur = replay.frozen_sha()
    except Exception:
        cur = None
    idx["frozen_sha_actual"] = cur
    idx["stale"] = cur is not None and cur != idx.get("frozen_sha")
    return idx


@app.get("/api/replay/{day}/{arm}")
def api_replay_arm(day: str, arm: str) -> Response:
    """`dia` = estaciones, viajes y movimientos de Ecobici; si no, frames del brazo."""
    if not DAY_RE.match(day) or not ARM_RE.match(arm):
        raise HTTPException(400, "dia o brazo invalido")
    return _json_file(REPLAY_DIR / day / f"{arm}.json")


# ---------------------------------------------------------------------------
# Prediccion en vivo (ecosim/live.py)
# ---------------------------------------------------------------------------

LIVE = None                      # ecosim.live.LiveLoop cuando corre el hilo
_LIVE_LOCK = threading.Lock()
_EVAL_CACHE: dict = {"t": 0.0, "data": None}


def _live_loop():
    """El loop en vivo (o uno sin hilo para corridas manuales)."""
    global LIVE
    from ecosim import live
    with _LIVE_LOCK:
        if LIVE is None:
            LIVE = live.LiveLoop(snapshot_fn=build_snapshot)
    return LIVE


def _station_meta() -> dict:
    snap = build_snapshot()
    return {s["short_name"]: [s["name"], s["lat"], s["lon"], s["capacity"]] for s in snap["stations"]}


@app.get("/api/live/status")
def api_live_status() -> dict:
    from ecosim import live
    runs = live.list_runs()
    loop = LIVE
    return {
        "loop": bool(loop and loop.running),
        "poll_s": live.POLL_S, "run_every_min": live.RUN_EVERY_MIN,
        "last_poll": loop.last_poll if loop else None,
        "last_run": loop.last_run if loop else None,
        "last_error": loop.last_error if loop else None,
        "n_runs": len(runs),
        "models": {k: {"desc": v[1], "disponible": k != "model"} for k, v in live.FORECASTERS.items()},
        "default_model": live.DEFAULT_MODEL,
    }


@app.get("/api/live/latest")
def api_live_latest(model: str = "daily") -> dict:
    """Ultima corrida guardada del modelo, con nombre y coordenadas de cada estacion."""
    from ecosim import live
    runs = [p for p in live.list_runs() if p.stem.endswith("_" + model)]
    if not runs:
        raise HTTPException(404, f"sin corridas de {model!r}: usa 'Correr ahora' o espera al loop de 15 min")
    r = json.loads(runs[-1].read_text())
    r["meta"] = _station_meta()
    return r


@app.post("/api/live/run")
def api_live_run(model: str = "daily") -> dict:
    """'Correr ahora': lee el feed, pronostica y corre el asignador."""
    try:
        r = _live_loop().run_now(model)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    r["meta"] = _station_meta()
    return r


@app.get("/api/live/eval")
def api_live_eval() -> dict:
    """Error acumulado de las corridas guardadas (cache de 60 s)."""
    from ecosim import live
    if _EVAL_CACHE["data"] is None or time.time() - _EVAL_CACHE["t"] > 60:
        _EVAL_CACHE["data"] = live.evaluate()
        _EVAL_CACHE["t"] = time.time()
    return _EVAL_CACHE["data"]


@app.get("/")
@app.get("/index.html")
def index() -> FileResponse:
    return FileResponse(HERE / "index.html", media_type="text/html")


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(FAVICON, media_type="image/svg+xml")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mapa Ecobici + replay y prediccion en vivo de ecosim.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true", help="no abrir el navegador")
    ap.add_argument("--no-live", action="store_true", help="no correr el loop en vivo (feed cada 60 s, corrida cada 15 min)")
    ap.add_argument("--results-dir", default=None,
                    help="carpeta con frozen.json/resultados.csv (por defecto ECOSIM_RESULTS_DIR o ecosim/results)")
    a = ap.parse_args(argv)
    if a.results_dir:
        os.environ["ECOSIM_RESULTS_DIR"] = str(Path(a.results_dir).resolve())
    if not a.no_live:
        loop = _live_loop()
        loop.start()
    url = f"http://{a.host}:{a.port}"
    print(f"Ecobici · {url}  (loop en vivo: {'no' if a.no_live else 'si'})", flush=True)
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
