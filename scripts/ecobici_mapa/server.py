"""Servidor local del mapa Ecobici para ecosim run 3.

Sirve tres pestañas: Ahora, Replay y Predicción. Toda predicción pasa por
`ecosim.live` (en vivo, sin modo histórico) con los datos y modelos de
producción de `ecosim.actualizar` y los parámetros congelados de
`ecosim/results/frozen.json`. Contratos en `README.md`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

GBFS_BASE = "https://gbfs.mex.lyftbikes.com/gbfs/es"
FEEDS = {"station_information": 3600, "station_status": 15, "system_alerts": 300}
HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent
REPLAY_DIR = REPO_ROOT / "data" / "derived" / "ecosim" / "replay"
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")  # se usa con fullmatch
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FAVICON = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#15221d"/><path d="M7 20a5 5 0 1 0 10 0A5 5 0 0 0 7 20Zm8-9 4 9m-4-9-5 9m5-9h4m-1 9a5 5 0 1 0 10 0 5 5 0 0 0-10 0Z" fill="none" stroke="#33d17a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'


class Cache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, bytes]] = {}
        self._lock = Lock()

    def get(self, feed: str, ttl: int) -> tuple[bytes, bool]:
        with self._lock:
            hit = self._data.get(feed)
            if hit and time.time() - hit[0] < ttl:
                return hit[1], False
        try:
            req = urllib.request.Request(f"{GBFS_BASE}/{feed}.json", headers={"User-Agent": "movilidad-cdmx/mapa-ecobici"})
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
    if not text:
        return set()
    cleaned = re.sub(r"\bdel?\s+\d{1,2}\s+(?:al?\s+\d{1,2}\s+)?de\s+\w+", " ", text, flags=re.I)
    cleaned = re.sub(r"\b\d{1,2}\s+de\s+\w+", " ", cleaned, flags=re.I)
    found: set[int] = set()
    for chunk in re.split(r"[.;]", cleaned):
        for lo, hi in re.findall(r"\b(\d{1,4})\s+al?\s+(\d{1,4})\b", chunk):
            lo_i, hi_i = int(lo), int(hi)
            if 0 < hi_i - lo_i <= 60:
                found.update(range(lo_i, hi_i + 1))
        for num in re.findall(r"\b(\d{1,4})\b", chunk):
            found.add(int(num))
    return {str(n) for n in found}


def short_name_keys(short_name: str) -> set[str]:
    s = str(short_name or "").strip()
    parts = re.findall(r"\d+", s)
    if not parts:
        return set()
    if len(parts) == 1:
        return {str(int(parts[0]))}
    lo, hi = int(parts[0]), int(parts[-1])
    if 0 < hi - lo <= 60:
        return {str(n) for n in range(lo, hi + 1)}
    return {str(int(p)) for p in parts}


def build_snapshot() -> dict:
    info_raw, _ = CACHE.get("station_information", FEEDS["station_information"])
    status_raw, status_stale = CACHE.get("station_status", FEEDS["station_status"])
    info = json.loads(info_raw)
    status = json.loads(status_raw)
    try:
        alerts_raw, _ = CACHE.get("system_alerts", FEEDS["system_alerts"])
        alerts = json.loads(alerts_raw)["data"].get("alerts", [])
    except Exception:
        alerts = []
    by_id = {s["station_id"]: s for s in status["data"]["stations"]}
    alerted_ids: set[str] = set(); alerted_names: set[str] = set()
    for alert in alerts:
        for sid in alert.get("station_ids", []) or []:
            alerted_ids.add(str(sid))
        alerted_names |= parse_station_numbers(alert.get("description", ""))
    stations = []
    for st in info["data"]["stations"]:
        live = by_id.get(st["station_id"])
        if not live:
            continue
        stations.append({"id": st["station_id"], "name": st["name"], "short_name": st.get("short_name", ""),
                         "lat": st["lat"], "lon": st["lon"], "capacity": st.get("capacity", 0),
                         "bikes": live["num_bikes_available"], "docks": live["num_docks_available"],
                         "bikes_disabled": live.get("num_bikes_disabled", 0), "docks_disabled": live.get("num_docks_disabled", 0),
                         "renting": bool(live.get("is_renting", 0)), "returning": bool(live.get("is_returning", 0)),
                         "installed": bool(live.get("is_installed", 0)), "last_reported": live.get("last_reported"),
                         "alerted": st["station_id"] in alerted_ids or bool(short_name_keys(st.get("short_name", "")) & alerted_names),
                         # Ficha: todas las llaves de station_information y station_status, tal cual.
                         "raw": {**st, **live}})
    return {"generated_at": int(time.time()), "last_updated": status.get("last_updated"), "stale": status_stale,
            "stations": stations, "alerts": [{"type": a.get("type"), "summary": a.get("summary"),
                                               "description": a.get("description"), "station_ids": a.get("station_ids", [])}
                                              for a in alerts]}


app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


@app.get("/api/snapshot")
def api_snapshot() -> Response:
    try:
        body = json.dumps(build_snapshot()).encode()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return Response(body, media_type="application/json", headers={"Cache-Control": "no-store"})


def _json_file(path: Path) -> Response:
    if not path.exists():
        raise HTTPException(404, f"{path.relative_to(REPO_ROOT)} no existe: corre `uv run python -m ecosim.replay`")
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-store"})


@app.get("/api/replay/index")
def api_replay_index() -> dict:
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
    # Solo días AAAA-MM-DD y los siete brazos del run 3 (en la carpeta quedan
    # archivos viejos sin `snap` que la API no sirve).
    arms = set(_arms())
    idx["days"] = {d: {**v, "arms": {a: x for a, x in v.get("arms", {}).items() if a in arms}}
                   for d, v in idx.get("days", {}).items() if DAY_RE.fullmatch(d)}
    return idx


def _arms() -> tuple[str, ...]:
    from ecosim import replay
    return replay.ARMS


@app.get("/api/replay/{day}/dia")
def api_replay_day(day: str) -> Response:
    if not DAY_RE.fullmatch(day):
        raise HTTPException(400, "día inválido: se espera AAAA-MM-DD")
    return _json_file(REPLAY_DIR / day / "dia.json")


@app.get("/api/replay/{day}/{arm}")
def api_replay_arm(day: str, arm: str) -> Response:
    if not DAY_RE.fullmatch(day):
        raise HTTPException(400, "día inválido: se espera AAAA-MM-DD")
    if arm not in _arms():
        raise HTTPException(400, f"brazo inválido: {arm!r}; válidos: {', '.join(_arms())}")
    return _json_file(REPLAY_DIR / day / f"{arm}.json")


ZONAS_FILE = HERE / "zonas_ageb.geojson"


def zonas_con_estaciones(gj: dict, stations: list[dict]) -> dict:
    """Reparte las estaciones del snapshot entre las AGEB del GeoJSON.

    La que contiene a la estación; si cae fuera de todas (estación nueva en
    otra AGEB o justo en un borde simplificado), la más cercana, y se anota en
    `metadata.cercanas_snapshot`. Cada estación queda en exactamente una zona.
    """
    from shapely import STRtree
    from shapely.geometry import Point, shape
    geoms = [shape(f["geometry"]) for f in gj["features"]]
    tree = STRtree(geoms)
    por_zona = [[] for _ in geoms]
    cercanas = []
    for st in stations:
        pt = Point(float(st["lon"]), float(st["lat"]))
        hits = [int(i) for i in tree.query(pt, predicate="intersects")]
        if hits:
            k = min(hits)
        else:
            k = int(tree.nearest(pt))
            cercanas.append({"short_name": st["short_name"], "cvegeo": gj["features"][k]["properties"]["cvegeo"]})
        por_zona[k].append(str(st["short_name"]))
    out = {**gj, "metadata": {**gj.get("metadata", {}), "cercanas_snapshot": cercanas},
           "features": [{**f, "properties": {**f["properties"], "estaciones": sorted(e)}}
                        for f, e in zip(gj["features"], por_zona)]}
    return out


@app.get("/api/zonas")
def api_zonas() -> Response:
    if not ZONAS_FILE.exists():
        raise HTTPException(404, "sin zonas: corre `uv run python scripts/ecobici_mapa/zonas_ageb.py`")
    gj = json.loads(ZONAS_FILE.read_text())
    try:
        gj = zonas_con_estaciones(gj, build_snapshot()["stations"])
    except Exception as exc:  # sin feed: se quedan las estaciones con que se generó el archivo
        gj["metadata"] = {**gj.get("metadata", {}), "aviso": f"estaciones del archivo; feed no disponible: {exc}"}
    return Response(json.dumps(gj, ensure_ascii=False, separators=(",", ":")), media_type="application/geo+json",
                    headers={"Cache-Control": "no-store"})


LIVE = None
ASIGNACIONES = None
_LIVE_LOCK = threading.Lock()


def _live_loop():
    """Bitácora del feed (una lectura por minuto) y sesiones de asignación."""
    global LIVE, ASIGNACIONES
    from ecosim import live
    with _LIVE_LOCK:
        if LIVE is None:
            LIVE = live.LiveLoop(snapshot_fn=build_snapshot)
        if ASIGNACIONES is None:
            ASIGNACIONES = live.Asignaciones(LIVE)
    return LIVE


def _asignaciones():
    _live_loop()
    return ASIGNACIONES


@app.get("/api/live/models")
def api_live_models() -> dict:
    from ecosim import live
    return live.models_info()


@app.post("/api/live/forecast")
def api_live_forecast(request: Request, model: str = "ma_diaria", horizonte: str = "dia") -> dict:
    """Forma diaria (ma_diaria, lgbm_diario): solo horizonte=dia; directa (lgbm_directo): 1–4.

    Solo en vivo: cualquier otro parámetro (p. ej. `at`) responde 400."""
    from ecosim import live
    otros = sorted(set(request.query_params) - {"model", "horizonte"})
    if otros:
        raise HTTPException(400, f"parámetros no aceptados: {', '.join(otros)}. El pronóstico es solo en vivo: "
                                 "usa model y horizonte.")
    try:
        live.validar_horizonte(model, horizonte)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    loop = _live_loop()
    try:
        t_feed = loop.poll()
    except Exception as exc:
        raise HTTPException(502, f"feed de Ecobici no disponible: {exc}")
    try:
        fc = live.pronosticar(model, horizonte, loop.log_today(t_feed), t_feed, loop.meta)
    except LookupError as exc:
        raise HTTPException(409, str(exc))
    except (NotImplementedError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(503, str(exc))
    live.guardar_pronostico(fc)
    return fc


@app.get("/api/live/feed")
def api_live_feed() -> dict:
    """Estado del watcher del feed: última lectura, cuándo cambió y las últimas 20."""
    return _live_loop().feed_info()


@app.get("/api/live/eventos")
def api_live_eventos() -> StreamingResponse:
    """Server-Sent Events: `feed` (lectura nueva), `paso` (paso de una sesión) y `sesion` (cambio de estado)."""
    return StreamingResponse(_live_loop().eventos.sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/live/assign/start")
def api_live_assign_start(forecast_id: str, horas: int = 1) -> dict:
    try:
        sid = _asignaciones().start(forecast_id, horas)
    except KeyError:
        raise HTTPException(404, f"pronóstico {forecast_id!r} no existe: pide uno con POST /api/live/forecast")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except (NotImplementedError, FileNotFoundError) as exc:
        raise HTTPException(503, str(exc))
    return {"session_id": sid}


@app.get("/api/live/assign")
def api_live_assign_list() -> dict:
    return {"sesiones": _asignaciones().listar()}


@app.get("/api/live/assign/{sid}")
def api_live_assign_get(sid: str) -> dict:
    try:
        return _asignaciones().get(sid)
    except KeyError:
        raise HTTPException(404, f"sesión {sid!r} no existe")


@app.post("/api/live/assign/{sid}/stop")
def api_live_assign_stop(sid: str) -> dict:
    try:
        st = _asignaciones().stop(sid)
    except KeyError:
        raise HTTPException(404, f"sesión {sid!r} no está corriendo en este servidor")
    return {"session_id": sid, "estado": st["estado"], "nota": "se detiene antes del siguiente paso"}


@app.get("/")
@app.get("/index.html")
def index() -> FileResponse:
    return FileResponse(HERE / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/app.css")
def stylesheet() -> FileResponse:
    return FileResponse(HERE / "app.css", media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/app.js")
def javascript() -> FileResponse:
    return FileResponse(HERE / "app.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(FAVICON, media_type="image/svg+xml")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mapa Ecobici + replay y predicción run 3.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-live", action="store_true")
    ap.add_argument("--results-dir", default=None)
    a = ap.parse_args(argv)
    if a.results_dir:
        os.environ["ECOSIM_RESULTS_DIR"] = str(Path(a.results_dir).resolve())
    if not a.no_live:
        _live_loop().start()
    url = f"http://{a.host}:{a.port}"
    print(f"Ecobici run 3 · {url}  (loop en vivo: {'no' if a.no_live else 'si'})", flush=True)
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
