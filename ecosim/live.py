"""Predicción en vivo: pronóstico y asignación con el feed actual de Ecobici.

Solo en vivo: no hay modo histórico ni nada precalculado. Todo pronóstico sale
de `ecosim.pronostico.forecast` (la misma construcción de variables que el
experimento) con los datos y modelos de producción de `ecosim.actualizar`
(`data/derived/ecosim/produccion/`). Los parámetros del asignador (n, λ,
topes) son los congelados en `ecosim/results/frozen.json`.

- Rezagos (`lag7`, `lag14`, `mean28`): si los días que piden existen en los
  viajes publicados, se usan tal cual. Si faltan, el pronóstico se hace para el
  día publicado más reciente del mismo tipo de día que sí los tiene (día de
  referencia) y se alinea al reloj de hoy; la respuesta lo dice. Nunca se
  rellenan con ceros.
- Forma directa: sus variables del día (`last15`, `last60`, `elapsed_delta`)
  salen de `pronostico._features` alimentada con salidas y llegadas
  **inferidas del feed de hoy** (`infer_flows`: cambios de disponibles + no
  rentables entre lecturas; saltos de ±`TRUCK_DELTA` o más son camioneta y no
  viajes). Es un demo de cómo funcionaría en producción; no es un resultado.
- Watcher: `LiveLoop` consulta el feed cada `POLL_S` y registra una lectura
  solo cuando cambia; cada lectura es un evento `feed` (SSE en el servidor).
- Asignación: un hilo por sesión que decide cada 15 min; en cada marca espera
  la primera lectura del feed posterior a ella (o corre a los `ESPERA_MAX_S`
  con la última) y corre el `Asignador` sobre el estado del feed y sus
  órdenes pendientes. Solo registra
  órdenes y conteos operativos; no calcula E+F ni ahorro (Ecobici sigue
  operando, no hay contra qué comparar).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import queue
import re
import threading
import time as _time
import urllib.request
import uuid
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ecosim import actualizar as A
from ecosim import config as C
from ecosim import contracts as K
from ecosim import pronostico as P
from ecosim.asignador import Asignador
from ecosim.days import day_type

LIVE_DIR = C.DERIVED / "live"
LOG_DIR = LIVE_DIR / "gbfs"
FORECAST_DIR = LIVE_DIR / "pronosticos"
SESSION_DIR = LIVE_DIR / "asignaciones"
GBFS_BASE = "https://gbfs.mex.lyftbikes.com/gbfs/es"
POLL_S = 15          # el watcher consulta el feed cada 15 s; solo registra si cambió
ESPERA_MAX_S = 300   # tras una marca de 15 min, cuánto espera una lectura nueva antes de correr igual
MAX_LECTURAS = 5000  # lecturas en memoria (un día a una por ~15 s)
STEP = C.STEP_MIN
TRUCK_DELTA = 5
# Hueco máximo entre lecturas del feed de hoy para llamarlo completo: un
# cuarto de hora sin lecturas deja ese cuarto sin salidas ni llegadas.
MAX_GAP_MIN = 15
MIN_RECENT_PEERS = 4  # días comparables mínimos para que mean28 cuente como real
MAX_SESSION_H = 20
# Horizontes con sentido por forma: la diaria es el día completo hecho una vez;
# la directa mira 1–4 h desde lo observado hoy.
HORIZONTES = {"diaria": ["dia"], "directa": [1, 2, 3, 4]}
NB15 = C.WINDOW_MIN // 15
SHORT_RE = re.compile(C.SHORT_NAME_RE + r"$")
log = logging.getLogger("ecosim.live")

MODELOS = {
    "ma_diaria": ("diaria", "Media móvil diaria",
                  "Promedio, por estación y cuarto de hora, de los días publicados del mismo tipo "
                  "(entre semana / fin de semana) en los 28 días previos."),
    "lgbm_diario": ("diaria", "LightGBM diario",
                    "Pronóstico del día completo con estación, hora, día de la semana, festivo, "
                    "lag7, lag14 y mean28."),
    "lgbm_directo": ("directa", "LightGBM directo",
                     "Pronóstico de las próximas horas con las variables del diario más lo observado hoy "
                     "(last15, last60, elapsed_delta); en vivo, lo observado hoy se infiere del feed."),
}


# ---------------------------------------------------------------------------
# reloj y feed
# ---------------------------------------------------------------------------

def now_local() -> datetime:
    """Hora local de la CDMX (UTC−6 fija), sin zona."""
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=C.UTC_OFFSET_HOURS)).replace(microsecond=0)


def epoch_local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None) + timedelta(hours=C.UTC_OFFSET_HOURS)


def floor15(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % STEP, second=0, microsecond=0)


def iso_min(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M")


def iso_s(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%S")


def fetch_gbfs() -> dict:
    def get(feed):
        req = urllib.request.Request(f"{GBFS_BASE}/{feed}.json", headers={"User-Agent": "movilidad-cdmx/ecosim-live"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    info, status = get("station_information"), get("station_status")
    by_id = {s["station_id"]: s for s in status["data"]["stations"]}
    stations = []
    for s in info["data"]["stations"]:
        live = by_id.get(s["station_id"])
        if live:
            stations.append({"short_name": s.get("short_name", ""), "name": s["name"], "lat": s["lat"], "lon": s["lon"],
                             "capacity": s.get("capacity", 0), "bikes": live["num_bikes_available"],
                             "docks": live["num_docks_available"], "bikes_disabled": live.get("num_bikes_disabled", 0),
                             "renting": bool(live.get("is_renting", 0)), "installed": bool(live.get("is_installed", 0))})
    return {"last_updated": status.get("last_updated"), "stations": stations}


def snapshot_record(snap: dict) -> dict:
    t = epoch_local(snap["last_updated"]) if snap.get("last_updated") else now_local()
    rows = {}
    for x in snap.get("stations", []):
        sn = str(x.get("short_name") or "")
        if SHORT_RE.match(sn):
            rows[sn] = [int(x.get("bikes", 0)), int(x.get("bikes_disabled", 0)), int(x.get("docks", 0)),
                        int(x.get("capacity") or 0), int(bool(x.get("renting")) and bool(x.get("installed")))]
    return {"t": t.isoformat(timespec="seconds"), "s": rows}


def station_meta(snap: dict) -> dict:
    return {str(x.get("short_name")): (x.get("name"), x.get("lat"), x.get("lon"))
            for x in snap.get("stations", []) if SHORT_RE.match(str(x.get("short_name") or ""))}


def append_log(snap: dict, log_dir: Path | None = None) -> bool:
    log_dir = log_dir or LOG_DIR
    rec = snapshot_record(snap)
    log_dir.mkdir(parents=True, exist_ok=True)
    p = log_dir / f"{rec['t'][:10]}.jsonl"
    if p.exists():
        with p.open("rb") as fh:
            fh.seek(max(0, p.stat().st_size - 200_000))
            tail = fh.read().decode(errors="replace").strip().splitlines()[-1:]
        if tail and tail[0].startswith('{"t":"' + rec["t"] + '"'):
            return False
    with p.open("a") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return True


def read_log(t0: datetime, t1: datetime, log_dir: Path | None = None) -> pd.DataFrame:
    log_dir = log_dir or LOG_DIR
    rows = []
    d = t0.date()
    while d <= t1.date():
        p = log_dir / f"{d.isoformat()}.jsonl"
        if p.exists():
            for line in p.read_text().splitlines():
                r = json.loads(line)
                t = datetime.fromisoformat(r["t"])
                if t0 <= t <= t1:
                    rows.extend((t, sn, *v) for sn, v in r.get("s", {}).items())
        d += timedelta(days=1)
    df = pd.DataFrame(rows, columns=["t", "short_name", "bikes", "disabled", "docks", "cap", "ok"])
    return df.drop_duplicates(["short_name", "t"], keep="last").sort_values(["short_name", "t"]).reset_index(drop=True)


def state_at(log_df: pd.DataFrame, t: datetime) -> pd.DataFrame:
    lg = log_df[log_df.t <= t]
    if lg.empty:
        raise ValueError(f"sin lecturas del feed con hora ≤ {t}")
    last = lg.groupby("short_name").tail(1).sort_values("short_name")
    return pd.DataFrame({"short_name": last.short_name.astype(str).to_numpy(), "bikes": last.bikes.to_numpy(int),
                         "disabled": last.disabled.to_numpy(int), "docks": last.docks.to_numpy(int),
                         "cap": last.cap.to_numpy(int), "out_of_service": last.ok.to_numpy(int) == 0,
                         "t_feed": last.t.to_numpy()}).reset_index(drop=True)


def infer_flows(log_df: pd.DataFrame, names: list[str], day0: datetime, upto: datetime,
                truck: int = TRUCK_DELTA) -> tuple[np.ndarray, dict]:
    """Salidas y llegadas estimadas [estación, cuarto, salida/llegada] del feed.

    Cambio de inventario (disponibles + no rentables) entre lecturas
    consecutivas de una estación en servicio: bajadas = salidas, subidas =
    llegadas. Un salto de `truck` bicis o más es camioneta y no cuenta. Un
    cambio en la lectura del minuto m va al cuarto que termina en m o después.
    """
    out = np.zeros((len(names), NB15, 2), dtype=float)
    lg = log_df[(log_df.t <= upto) & (log_df.t >= day0 - timedelta(minutes=30))].copy()
    info = {"camion_bicis": 0, "camion_n": 0, "fuera_servicio_n": 0, "lecturas": int(lg.t.nunique())}
    if lg.empty:
        return out, info
    lg["stock"] = lg.bikes + lg.disabled
    g = lg.groupby("short_name", sort=False)
    d = (lg.stock - g.stock.shift()).to_numpy()
    okpair = ((lg.ok == 1) & (g.ok.shift() == 1)).to_numpy()
    m = ((lg.t - pd.Timestamp(day0)) / pd.Timedelta(minutes=1)).to_numpy()
    si = pd.Index(names).get_indexer(lg.short_name)
    has = ~np.isnan(d) & (m > 0) & (m <= C.WINDOW_MIN) & (si >= 0)
    big = has & (np.abs(d) >= truck)
    info["camion_n"] = int((big & okpair).sum()); info["camion_bicis"] = int(np.abs(d[big & okpair]).sum())
    info["fuera_servicio_n"] = int((has & ~okpair & (d != 0)).sum())
    use = has & ~big & okpair & (d != 0)
    b = np.minimum(((m[use] - 1e-9) // 15).astype(int), NB15 - 1)
    np.add.at(out, (si[use], b, 0), np.maximum(-d[use], 0))
    np.add.at(out, (si[use], b, 1), np.maximum(d[use], 0))
    return out, info


def feed_hoy(log_df: pd.DataFrame, day: date, upto: datetime) -> dict:
    """Desde cuándo hay lecturas de hoy y si cubren 05:00→ahora sin huecos."""
    day0 = datetime.combine(day, C.DAY_START)
    ts = sorted(set(pd.to_datetime(log_df.t[(log_df.t >= day0 - timedelta(minutes=30)) & (log_df.t <= upto)])))
    hoy = [t for t in ts if t >= day0]
    if not ts or not hoy:
        return {"desde": None, "fotos": 0, "completo": False, "hueco_max_min": None,
                "nota": "Sin lecturas del feed de hoy desde las 05:00: no hay salidas ni llegadas del día."}
    antes = [t for t in ts if t < day0]
    # Desde la 05:00 (o la última lectura previa) hasta ahora.
    puntos = [antes[-1] if antes else pd.Timestamp(day0)] + hoy + [pd.Timestamp(upto)]
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(puntos, puntos[1:])]
    primera_ok = (antes and day0 - antes[-1] <= timedelta(minutes=MAX_GAP_MIN)) or hoy[0] <= day0 + timedelta(minutes=MAX_GAP_MIN)
    completo = bool(primera_ok and max(gaps) <= MAX_GAP_MIN)
    out = {"desde": iso_s(hoy[0]), "fotos": len(hoy), "completo": completo, "hueco_max_min": round(max(gaps), 1)}
    if not completo:
        out["nota"] = ("Las lecturas de hoy no cubren desde las 05:00 sin huecos: las salidas y llegadas "
                       "del día están incompletas.")
    return out


# ---------------------------------------------------------------------------
# datos y modelos de producción
# ---------------------------------------------------------------------------

class Produccion:
    """Conteos publicados, universo de estaciones y modelos de `ecosim.actualizar`."""

    def __init__(self, prod_dir: Path | None = None):
        import joblib
        self.dir = prod_dir or A.PROD_DIR
        self.manifiesto = A.leer_manifiesto(self.dir / "manifiesto.json")
        if self.manifiesto is None:
            raise FileNotFoundError("sin datos de producción: corre `uv run python -m ecosim.actualizar`")
        self.days, self.universe, self.counts = A.leer_conteos(self.dir / self.manifiesto["conteos"]["archivo"])
        self.pos = {d: i for i, d in enumerate(self.days)}
        self.ultimo = date.fromisoformat(self.manifiesto["ultimo_dia_publicado"])
        self.valid = self.counts.sum(axis=(1, 2, 3)) > 0
        self.models, self.motivos = {}, {}
        for key in ("lgbm_diario", "lgbm_directo"):
            rel = self.manifiesto.get("modelos", {}).get(key)
            if rel is None or not (self.dir / rel).exists():
                self.motivos[key] = f"no hay modelo de producción {key}: corre `uv run python -m ecosim.actualizar`"
                continue
            self.models[key] = joblib.load(self.dir / rel)

    @classmethod
    def from_arrays(cls, days, universe, counts, models=None, manifiesto=None):
        """Para pruebas: mismos campos sin leer disco."""
        self = cls.__new__(cls)
        self.dir = None
        self.days, self.universe, self.counts = list(days), list(universe), np.asarray(counts, np.float32)
        self.pos = {d: i for i, d in enumerate(self.days)}
        self.valid = self.counts.sum(axis=(1, 2, 3)) > 0
        self.ultimo = max(d for d, v in zip(self.days, self.valid) if v)
        self.models = dict(models or {})
        self.motivos = {}
        self.manifiesto = manifiesto or {"ultimo_dia_publicado": self.ultimo.isoformat(), "actualizado": None,
                                         "entrenamiento": {"train_start": self.days[0].isoformat(),
                                                           "train_end": self.ultimo.isoformat()}}
        return self


_PROD = {"key": None, "obj": None}
_PROD_LOCK = threading.Lock()


def produccion(prod_dir: Path | None = None) -> Produccion:
    """Se recarga solo si cambia el manifiesto (otra corrida de actualizar)."""
    path = (prod_dir or A.PROD_DIR) / "manifiesto.json"
    key = (str(path), path.stat().st_mtime if path.exists() else None)
    with _PROD_LOCK:
        if _PROD["key"] != key:
            _PROD["obj"] = Produccion(prod_dir)
            _PROD["key"] = key
        return _PROD["obj"]


def ventana(prod: Produccion, day: date, hoy: np.ndarray | None = None) -> tuple[list[date], np.ndarray]:
    """Días [day−28, day] con sus conteos publicados; el renglón de `day` es `hoy`.

    `pronostico._features` y la media móvil solo miran 28 días atrás, así que
    la ventana da exactamente las mismas variables que la serie completa. Un
    día sin viajes publicados queda en cero y `pronostico` lo trata como
    faltante (exige conteos > 0); nunca se usa como rezago.
    """
    days = [day - timedelta(days=28 - k) for k in range(29)]
    counts = np.zeros((29, len(prod.universe), NB15, 2), np.float32)
    for k, d in enumerate(days[:-1]):
        i = prod.pos.get(d)
        if i is not None and d <= prod.ultimo:
            counts[k] = prod.counts[i]
    if hoy is not None:
        counts[-1] = hoy
    return days, counts


def faltantes(prod: Produccion, day: date, modelo: str) -> list[str]:
    """Rezagos que pide el modelo para `day` y no están en los viajes publicados."""
    days, counts = ventana(prod, day)
    valid = counts[:-1].sum(axis=(1, 2, 3)) > 0
    peers = [k for k in range(28) if valid[k] and P._type(days[k]) == P._type(day)]
    out = []
    if len(peers) < MIN_RECENT_PEERS:
        out.append(f"mean28: {len(peers)} días comparables publicados (< {MIN_RECENT_PEERS})")
    if modelo.startswith("lgbm"):
        for n in (7, 14):
            if not valid[28 - n]:
                out.append(f"lag{n}: {(day - timedelta(days=n)).isoformat()} sin viajes publicados")
    return out


def dia_base(prod: Produccion, day: date, modelo: str) -> tuple[date, dict]:
    """El día cuyas variables se usan: hoy si sus rezagos existen; si no, el día
    publicado más reciente del mismo tipo (festivo, entre semana, fin de semana;
    si no hay, entre semana / no) con rezagos completos."""
    faltan = faltantes(prod, day, modelo)
    if not faltan:
        return day, {"rezagos": "reales", "dia_referencia": None, "faltan": []}
    publicados = [d for d, v in zip(prod.days, prod.valid) if v and d <= prod.ultimo and d < day]
    for igual in (lambda d: day_type(d) == day_type(day), lambda d: P._type(d) == P._type(day)):
        for d in reversed(publicados):
            if igual(d) and not faltantes(prod, d, modelo):
                return d, {"rezagos": "dia_referencia", "dia_referencia": d.isoformat(), "faltan": faltan,
                           "tipo_dia": day_type(day), "tipo_dia_referencia": day_type(d)}
    raise ValueError(f"{modelo}: faltan rezagos de {day} ({'; '.join(faltan)}) y ningún día publicado "
                     "del mismo tipo los tiene completos; no se rellenan con ceros")


def construir(prod: Produccion, modelo: str, day: date, hoy: np.ndarray | None = None) -> tuple[P.Forecast, dict]:
    """`pronostico.forecast` sobre la ventana del día base. `hoy` (directa) son
    las salidas y llegadas del día en curso, con la forma de `count_trips`."""
    if modelo not in MODELOS:
        raise ValueError(f"modelo no disponible en vivo: {modelo}")
    if modelo.startswith("lgbm") and modelo not in prod.models:
        raise NotImplementedError(prod.motivos.get(modelo, f"{modelo}: sin modelo de producción"))
    base, ref = dia_base(prod, day, modelo)
    days, counts = ventana(prod, base, hoy if MODELOS[modelo][0] == "directa" else None)
    fc = P.forecast(modelo, counts, days, base, prod.models.get(modelo))
    return fc, ref


def variables(prod: Produccion, modelo: str, day: date, kind: int, ticks, hoy: np.ndarray | None = None) -> pd.DataFrame:
    """Las variables que recibe LightGBM en vivo (para auditar contra `pronostico`)."""
    base, _ = dia_base(prod, day, modelo)
    days, counts = ventana(prod, base, hoy)
    X, _ = P._features(counts, days, len(days) - 1, kind, np.asarray(ticks), MODELOS[modelo][0] == "directa", labels=False)
    return X


class VistaPronostico:
    """Pronóstico del día base, alineado al reloj de hoy y a las estaciones del feed."""

    def __init__(self, fc: P.Forecast, day: date, rows: np.ndarray):
        self.fc, self.day, self.rows = fc, day, np.asarray(rows, int)
        self.forma, self.modelo = fc.forma, fc.modelo

    def table(self, t: datetime, n_hours: int) -> np.ndarray:
        shift = C.day_bounds(self.fc.day)[0] - C.day_bounds(self.day)[0]
        return self.fc.table(t + shift, n_hours)[self.rows]


def frozen(path: Path | None = None) -> dict:
    p = path or C.REPO_ROOT / "ecosim" / "results" / "frozen.json"
    return json.loads(p.read_text())


def params_for(model: str, fz: dict) -> K.PolicyParams:
    if model not in fz.get("lambda_por_brazo", {}):
        raise NotImplementedError(f"{model}: no hay parámetros congelados en frozen.json")
    forma = "directa" if model.endswith("directo") else "diaria"
    caps = fz.get("topes_base", {})
    return K.PolicyParams(n_hours=int(fz["n"][forma]), lam=float(fz["lambda_por_brazo"][model]["lambda"]),
                          visits_per_decision=int(caps.get("visitas_por_decision", C.VISITS_PER_DECISION)),
                          max_bikes_per_visit=int(caps.get("bicis_por_visita", C.MAX_BIKES_PER_VISIT)),
                          pickup_min=C.PICKUP_MIN, delivery_min=C.DELIVERY_MIN, retiro=float(fz.get("retiro", 0.0)))


# ---------------------------------------------------------------------------
# lista de modelos
# ---------------------------------------------------------------------------

_PROBE: dict = {}


def _probe(prod: Produccion, modelo: str, day: date) -> str | None:
    """Corre una vez por día el pronóstico completo del modelo; None si funciona."""
    key = (id(prod), modelo, day)
    if key not in _PROBE:
        try:
            hoy = np.zeros((len(prod.universe), NB15, 2)) if MODELOS[modelo][0] == "directa" else None
            fc, _ = construir(prod, modelo, day, hoy)
            fc.table(datetime.combine(fc.day, C.DAY_START), 1)
            _PROBE[key] = None
        except Exception as exc:  # se muestra como motivo, no se oculta
            _PROBE[key] = f"{type(exc).__name__}: {exc}"
    return _PROBE[key]


def models_info(now: datetime | None = None, prod_dir: Path | None = None, fz: dict | None = None) -> dict:
    now = now or now_local()
    day = C.window_day(now)
    fz = fz or frozen()
    try:
        prod = produccion(prod_dir)
    except Exception as exc:
        return {"datos": {"ultimo_dia_publicado": None, "actualizado": None, "motivo": str(exc)},
                "modelos": [{"key": k, "forma": f, "label": lab, "disponible": False, "motivo": str(exc),
                             "corte": None, "n": None, "lambda": None, "nota": nota, "horizontes": HORIZONTES[f]}
                            for k, (f, lab, nota) in MODELOS.items()]}
    man = prod.manifiesto
    ent = man.get("entrenamiento", {})
    out = []
    for key, (forma, label, nota) in MODELOS.items():
        motivo = prod.motivos.get(key) or _probe(prod, key, day)
        try:
            pp = params_for(key, fz)
            n, lam = pp.n_hours, pp.lam
        except NotImplementedError as exc:
            n = lam = None
            motivo = motivo or str(exc)
        try:
            _, ref = dia_base(prod, day, key)
        except ValueError:
            ref = None
        if key == "ma_diaria":
            corte = {"train_start": None, "train_end": prod.ultimo.isoformat(),
                     "nota": "No se entrena: promedia los 28 días previos al día base."}
        else:
            corte = {"train_start": ent.get("train_start"), "train_end": ent.get("train_end")}
        out.append({"key": key, "forma": forma, "label": label, "disponible": motivo is None, "motivo": motivo,
                    "corte": corte, "n": n, "lambda": lam, "nota": nota, "rezagos_hoy": ref,
                    "horizontes": HORIZONTES[forma]})
    return {"datos": {"ultimo_dia_publicado": man.get("ultimo_dia_publicado"), "actualizado": man.get("actualizado"),
                      "dia": day.isoformat()},
            "modelos": out}


# ---------------------------------------------------------------------------
# pronóstico
# ---------------------------------------------------------------------------

def _estado(log_df: pd.DataFrame, prod: Produccion, t_feed: datetime):
    st = state_at(log_df, t_feed)
    fuera = sorted(set(st.short_name) - set(prod.universe))
    st = st[st.short_name.isin(prod.universe)].sort_values("short_name").reset_index(drop=True)
    if st.empty:
        raise ValueError("feed sin estaciones del universo del pronóstico")
    rows = pd.Index(prod.universe).get_indexer(st.short_name)
    return st, rows, fuera


def preparar(modelo: str, t_feed: datetime, log_df: pd.DataFrame, prod: Produccion, t: datetime | None = None):
    """Estado del feed + pronóstico listo para `t` (por omisión, el cuarto de hora en curso del feed)."""
    t = t or floor15(t_feed)
    day = C.window_day(t)
    start, end = C.day_bounds(day)
    if not start <= t < end:
        raise LookupError(f"fuera del horario de servicio (05:00–00:30): feed a las {iso_s(t_feed)}")
    st, rows, fuera = _estado(log_df, prod, t_feed)
    forma = MODELOS[modelo][0]
    hoy = feed = None
    info = {}
    if forma == "directa":
        hoy, info = infer_flows(log_df, prod.universe, start, t_feed)
        feed = feed_hoy(log_df, day, t_feed)
    fc, ref = construir(prod, modelo, day, hoy)
    vista = VistaPronostico(fc, day, rows)
    referencia = {**ref, "feed_hoy": feed, "viajes_del_dia": "inferidos_feed" if forma == "directa" else None,
                  "inferencia": info or None, "estaciones_sin_pronostico": fuera}
    return t, day, st, vista, referencia


def validar_horizonte(modelo: str, horizonte) -> int | None:
    """Horas (1–4) o None para 'dia'; ValueError si la combinación no tiene sentido."""
    if modelo not in MODELOS:
        raise ValueError(f"modelo no disponible en vivo: {modelo}")
    forma = MODELOS[modelo][0]
    validos = HORIZONTES[forma]
    h = str(horizonte)
    if h == "dia" and "dia" in validos:
        return None
    if h in {str(x) for x in validos if x != "dia"}:
        return int(h)
    if forma == "diaria":
        raise ValueError(f"horizonte inválido para {modelo}: {h!r}. Los modelos de forma diaria pronostican "
                         "el día completo (05:00–00:30); usa horizonte=dia.")
    raise ValueError(f"horizonte inválido para {modelo}: {h!r}. La forma directa pronostica las próximas "
                     "1, 2, 3 o 4 horas; usa horizonte=1, 2, 3 o 4.")


def pronosticar(modelo: str, horizonte, log_df: pd.DataFrame, t_feed: datetime, meta: dict | None = None,
                prod: Produccion | None = None, issued_at: datetime | None = None) -> dict:
    """Proyección por estación: forma diaria al cierre del día (00:30); directa a 1–4 h."""
    horas = validar_horizonte(modelo, horizonte)
    prod = prod or produccion()
    meta = meta or {}
    t, day, st, vista, ref = preparar(modelo, t_feed, log_df, prod)
    offset = int((t - C.day_bounds(day)[0]).total_seconds() // 900)
    restantes = NB15 - offset
    q = restantes if horas is None else min(4 * horas, restantes)
    notas = []
    if q < (restantes if horas is None else 4 * horas):
        notas.append("El horizonte se corta a las 00:30, fin del servicio.")
    tab = vista.table(t, math.ceil(q / 4))[:, :q, :]
    stock = st.bikes.to_numpy(float)
    cap = (st.bikes + st.docks).to_numpy(float)
    proy = []
    for j in range(q):
        stock = np.clip(stock - tab[:, j, 0] + tab[:, j, 1], 0, cap)
        proy.append(np.rint(stock).astype(int))
    riesgos = []
    for i, sn in enumerate(st.short_name):
        for tipo, hit in (("vacia", [p[i] <= 0 for p in proy]), ("llena", [p[i] >= cap[i] for p in proy])):
            if cap[i] > 0 and any(hit):
                riesgos.append({"short_name": sn, "tipo": tipo, "minutos": int(15 * (hit.index(True) + 1))})
    riesgos.sort(key=lambda r: (r["minutos"], r["short_name"]))
    names = st.short_name.tolist()
    issued = issued_at or now_local()
    return {
        "id": f"{issued.strftime('%Y%m%d-%H%M%S')}-{modelo}-{horizonte}-{uuid.uuid4().hex[:6]}",
        "issued_at": iso_s(issued), "t": iso_min(t), "t_feed": iso_s(st.t_feed.max()),
        "model": modelo, "forma": vista.forma, "horizonte": str(horizonte),
        "referencia": ref,
        "minutes": [15 * (j + 1) for j in range(q)],
        "stations": {"short_name": names,
                     "name": [meta.get(s, (s,))[0] for s in names],
                     "lat": [meta.get(s, (None, None, None))[1] for s in names],
                     "lon": [meta.get(s, (None, None, None))[2] for s in names],
                     "cap": cap.astype(int).tolist()},
        "bikes_now": st.bikes.astype(int).tolist(),
        "proyeccion": [p.tolist() for p in proy],
        "salidas": np.round(tab[:, :, 0].T, 3).tolist(),
        "llegadas": np.round(tab[:, :, 1].T, 3).tolist(),
        "riesgos": riesgos,
        "notas": notas + ["Proyección: inventario del feed menos salidas más llegadas esperadas, por cuarto de hora, "
                          "acotado a [0, capacidad]; no aplica órdenes de rebalanceo."],
        "estimado": True,
    }


def guardar_pronostico(fc: dict, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or FORECAST_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{fc['id']}.json"
    p.write_text(json.dumps(fc, ensure_ascii=False))
    return p


def leer_pronostico(fid: str, out_dir: Path | None = None) -> dict:
    if not re.fullmatch(r"[\w\-]+", fid or ""):
        raise KeyError(fid)
    p = (out_dir or FORECAST_DIR) / f"{fid}.json"
    if not p.exists():
        raise KeyError(fid)
    return json.loads(p.read_text())


# ---------------------------------------------------------------------------
# bitácora del feed: watcher + eventos para el navegador
# ---------------------------------------------------------------------------

def resumen_feed(rec: dict) -> dict:
    """Conteos del sistema en una lectura del feed (estaciones en servicio para vacías/llenas)."""
    rows = list(rec.get("s", {}).values())
    return {"bicis_disponibles": int(sum(r[0] for r in rows)), "no_rentables": int(sum(r[1] for r in rows)),
            "anclajes_libres": int(sum(r[2] for r in rows)), "estaciones": len(rows),
            "estaciones_vacias": int(sum(1 for r in rows if r[4] and r[0] == 0)),
            "estaciones_llenas": int(sum(1 for r in rows if r[4] and r[2] == 0))}


def _rec_df(rec: dict) -> pd.DataFrame:
    t = datetime.fromisoformat(rec["t"])
    return pd.DataFrame([(t, sn, *v) for sn, v in rec["s"].items()],
                        columns=["t", "short_name", "bikes", "disabled", "docks", "cap", "ok"])


def flujos_entre(prev: dict, cur: dict) -> tuple[int, int, int]:
    """(salidas, llegadas, saltos de camioneta) entre dos lecturas: la misma regla
    de `infer_flows` (cambio de disponibles + no rentables; saltos de ±TRUCK_DELTA fuera)."""
    df = pd.concat([_rec_df(prev), _rec_df(cur)]).sort_values(["short_name", "t"], kind="stable").reset_index(drop=True)
    names = sorted(set(prev["s"]) | set(cur["s"]))
    t0 = datetime.fromisoformat(prev["t"])
    if datetime.fromisoformat(cur["t"]) <= t0:
        return 0, 0, 0
    flows, info = infer_flows(df, names, t0, datetime.fromisoformat(cur["t"]))
    return round(float(flows[:, :, 0].sum())), round(float(flows[:, :, 1].sum())), int(info["camion_n"])


class Eventos:
    """Bus en memoria para Server-Sent Events: `feed`, `paso` y `sesion`."""

    def __init__(self, clock=now_local, recientes: int = 200):
        self.clock = clock
        self.lock = threading.Lock()
        self.subs: list[queue.Queue] = []
        self.n = 0
        self.recientes: deque = deque(maxlen=recientes)
        self.ultimo: dict[str, dict] = {}

    def publish(self, tipo: str, datos: dict) -> dict:
        with self.lock:
            self.n += 1
            ev = {"id": self.n, "evento": tipo, "hora": iso_s(self.clock()), **datos}
            self.recientes.append(ev)
            self.ultimo[tipo] = ev
            subs = list(self.subs)
        for q in subs:
            q.put(ev)
        return ev

    @staticmethod
    def formato(ev: dict) -> str:
        return f"id: {ev['id']}\nevent: {ev['evento']}\ndata: {json.dumps(ev, ensure_ascii=False, separators=(',', ':'))}\n\n"

    def sse(self, latido_s: float = 15.0, max_eventos: int | None = None):
        """Generador SSE: primero la última lectura del feed (si hay), luego cada evento.
        Un comentario de latido cada `latido_s` mantiene viva la conexión."""
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.subs.append(q)
            inicial = self.ultimo.get("feed")
        enviados = 0
        try:
            yield "retry: 3000\n\n"
            if inicial is not None:
                yield self.formato(inicial)
                enviados += 1
            while max_eventos is None or enviados < max_eventos:
                try:
                    ev = q.get(timeout=latido_s)
                except queue.Empty:
                    yield ": latido\n\n"
                    continue
                yield self.formato(ev)
                enviados += 1
        finally:
            with self.lock:
                if q in self.subs:
                    self.subs.remove(q)


class LiveLoop:
    """Watcher del feed: lo consulta cada `POLL_S` y registra una **lectura** solo
    cuando cambia (otra `last_updated` o distinto contenido). Cada lectura va a
    la bitácora del día y se emite como evento `feed`."""

    def __init__(self, snapshot_fn=fetch_gbfs, log_dir: Path | None = None, clock=now_local,
                 eventos: Eventos | None = None):
        self.snapshot_fn = snapshot_fn
        self.log_dir = log_dir or LOG_DIR
        self.clock = clock
        self.eventos = eventos or Eventos(clock)
        self.running = False
        self.thread = None
        self.lock = threading.RLock()
        self.last_poll = None
        self.last_error = None
        self.meta = {}
        self.prev: dict | None = None
        self.prev_hash: str | None = None
        self.lecturas: deque = deque(maxlen=MAX_LECTURAS)
        self.ultima: dict | None = None

    def poll(self) -> datetime:
        """Consulta el feed; devuelve la hora del feed (cambie o no)."""
        with self.lock:
            snap = self.snapshot_fn()
            rec = snapshot_record(snap)
            now = self.clock()
            self.meta = station_meta(snap) or self.meta
            self.last_poll = iso_s(now)
            h = hashlib.sha1(json.dumps(rec["s"], sort_keys=True).encode()).hexdigest()
            if self.prev is None or rec["t"] != self.prev["t"] or h != self.prev_hash:
                self._registrar(snap, rec, now)
                self.prev, self.prev_hash = rec, h
            return datetime.fromisoformat(rec["t"])

    def _registrar(self, snap: dict, rec: dict, now: datetime):
        append_log(snap, self.log_dir)
        lect = {"t_feed": rec["t"], "t_detectado": iso_s(now), "primera": self.prev is None,
                "estaciones_cambiaron": None, "salidas_est": None, "llegadas_est": None, "saltos_camioneta": None,
                **resumen_feed(rec)}
        if self.prev is not None:
            a, b = self.prev["s"], rec["s"]
            lect["estaciones_cambiaron"] = sum(1 for sn in set(a) | set(b) if a.get(sn) != b.get(sn))
            lect["salidas_est"], lect["llegadas_est"], lect["saltos_camioneta"] = flujos_entre(self.prev, rec)
        self.lecturas.append(lect)
        self.ultima = lect
        p = self.log_dir / f"{rec['t'][:10]}_lecturas.jsonl"
        with p.open("a") as fh:
            fh.write(json.dumps(lect, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.eventos.publish("feed", lect)

    def lectura_desde(self, t: datetime) -> dict | None:
        """Primera lectura con hora del feed ≥ t."""
        key = t.isoformat(timespec="seconds")
        with self.lock:
            return next((x for x in self.lecturas if x["t_feed"] >= key), None)

    def lecturas_entre(self, a: str | None, b: str) -> list[dict]:
        """Lecturas con a < t_feed ≤ b (horas ISO)."""
        with self.lock:
            return [x for x in self.lecturas if (a is None or x["t_feed"] > a) and x["t_feed"] <= b]

    def feed_info(self, n: int = 20) -> dict:
        with self.lock:
            ultima = self.ultima
            hoy = ultima["t_feed"][:10] if ultima else None
            p = self.log_dir / f"{hoy}_lecturas.jsonl" if hoy else None
            lineas = p.read_text().splitlines() if p is not None and p.exists() else []
            return {"ultima_lectura": ultima, "ultimo_cambio": ultima["t_detectado"] if ultima else None,
                    "ultima_consulta": self.last_poll, "lecturas_hoy": len(lineas),
                    "desde": json.loads(lineas[0])["t_feed"] if lineas else None, "poll_s": POLL_S,
                    "error": self.last_error, "lecturas": list(self.lecturas)[-n:]}

    def log_today(self, t_feed: datetime) -> pd.DataFrame:
        day0 = datetime.combine(C.window_day(t_feed), C.DAY_START)
        return read_log(day0 - timedelta(minutes=30), t_feed, self.log_dir)

    def _loop(self):
        while self.running:
            try:
                self.poll()
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)
                log.exception("poll live")
            _time.sleep(POLL_S)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False


# ---------------------------------------------------------------------------
# asignación en tiempo real, disparada por el feed
# ---------------------------------------------------------------------------

def _orden(o: K.Order) -> dict:
    return {"short_name": o.short_name, "accion": "recoger" if o.delta < 0 else "entregar", "n": abs(int(o.delta)),
            "emitida": iso_min(o.issued_at), "recoge": iso_min(o.pickup_at), "entrega": iso_min(o.delivery_at)}


def _efectiva(o: K.Order) -> datetime:
    return o.pickup_at if o.delta < 0 else o.delivery_at


ESTADO_KEYS = ("t_feed", "bicis_disponibles", "no_rentables", "anclajes_libres", "estaciones_vacias", "estaciones_llenas")


class Sesion:
    """Una asignación corriendo en un hilo; su estado vive en un JSON.

    Decide cada 15 min (recoge a t+15, entrega a t+60, topes por decisión).
    En cada marca t espera la primera lectura del feed con hora ≥ t; si en
    `ESPERA_MAX_S` no llega, corre con la última y lo marca."""

    def __init__(self, sid: str, forecast: dict, horas: int, loop: LiveLoop, out_dir: Path,
                 clock=None, sleep=None, prod: Produccion | None = None, fz: dict | None = None):
        self.id = sid
        self.loop = loop
        self.path = out_dir / f"{sid}.json"
        self.clock = clock or loop.clock
        self.sleep = sleep or _time.sleep
        self.prod = prod
        self.fz = fz
        self.model = forecast["model"]
        self.stop_event = threading.Event()
        self.pending: list[K.Order] = []
        self.asignador = Asignador(time_limit=10, threads=1)
        self.params = params_for(self.model, self.fz or frozen())
        self.prev_t_feed: str | None = None
        inicio = floor15(self.clock())
        fin = min(inicio + timedelta(hours=horas), C.day_bounds(C.window_day(inicio))[1])
        self.state = {
            "id": sid, "estado": "corriendo", "fase": "emitiendo", "model": self.model,
            "forecast_id": forecast["id"], "horizonte_pronostico": forecast.get("horizonte"),
            "inicio": iso_min(inicio), "horas": int(horas), "fin_emision": iso_min(fin),
            "params": {"n": self.params.n_hours, "lambda": self.params.lam,
                       "visitas_por_decision": self.params.visits_per_decision,
                       "bicis_por_visita": self.params.max_bikes_per_visit,
                       "recoge_min": self.params.pickup_min, "entrega_min": self.params.delivery_min,
                       "espera_max_s": ESPERA_MAX_S, "poll_s": POLL_S},
            "inicio_estado": None,
            "pasos": [], "totales": {"bicis_a_mover": 0, "visitas": 0, "recogidas": 0, "entregadas": 0},
            "pendientes": [], "siguiente_paso": iso_min(inicio), "error": None,
            "nota": "Demo en vivo: órdenes sobre el estado del feed y las órdenes pendientes de esta sesión. "
                    "No se ejecutan ni se mide E+F ni ahorro: Ecobici sigue operando.",
        }
        self._save()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False))
        tmp.replace(self.path)

    def _evento_sesion(self):
        s = self.state
        self.loop.eventos.publish("sesion", {"sesion": self.id, "estado": s["estado"], "fase": s["fase"],
                                             "siguiente_paso": s["siguiente_paso"], "pasos": len(s["pasos"]),
                                             "error": s["error"]})

    def esperar_feed(self, t: datetime) -> tuple[dict, str, float, bool] | None:
        """(lectura, disparo, espera_s, feed_atrasado) para la marca t; None si la detienen."""
        limite = t + timedelta(seconds=ESPERA_MAX_S)
        while True:
            if self.stop_event.is_set():
                return None
            now = self.clock()
            if now < t:
                self.sleep(min((t - now).total_seconds(), 5))
                continue
            try:
                self.loop.poll()
            except Exception as exc:  # el feed puede fallar un momento; se reintenta hasta el límite
                self.loop.last_error = str(exc)
            lect = self.loop.lectura_desde(t)
            if lect is not None:
                espera = max(0.0, (datetime.fromisoformat(lect["t_detectado"]) - t).total_seconds())
                return lect, "cambio_feed", round(espera, 1), False
            now = self.clock()
            if now >= limite:
                if self.loop.ultima is None:
                    raise RuntimeError("sin lecturas del feed")
                return self.loop.ultima, "sin_cambio_feed", round((now - t).total_seconds(), 1), True
            self.sleep(min(POLL_S, (limite - now).total_seconds()))

    def paso(self, t: datetime, lect: dict | None = None, disparo: str = "inicio", espera_s: float = 0.0,
             atrasado: bool = False) -> dict:
        """Un paso: lectura del feed → aplicar lo que toca en t → decidir (si aún emite)."""
        if lect is None:
            self.loop.poll()
            lect = self.loop.ultima
        t_feed = datetime.fromisoformat(lect["t_feed"])
        prod = self.prod or produccion()
        log_df = self.loop.log_today(t_feed)
        day = C.window_day(t)
        start, _ = C.day_bounds(day)
        aplicadas = [o for o in self.pending if _efectiva(o) <= t]
        self.pending = [o for o in self.pending if _efectiva(o) > t]
        emitidas: list[K.Order] = []
        nota = None
        if t < datetime.fromisoformat(self.state["fin_emision"]):
            try:
                if abs((t_feed - t).total_seconds()) > 60 * STEP:
                    nota = f"feed desfasado: lectura de {iso_s(t_feed)} para el paso {iso_min(t)}; no se emite"
                else:
                    _, _, st, vista, ref = preparar(self.model, t_feed, log_df, prod, t=t)
                    sim_state = K.SimState(t=t, stations=st[["short_name", "bikes", "disabled", "docks", "cap",
                                                             "out_of_service"]])
                    emitidas = self.asignador.decide(t, sim_state, list(self.pending), vista, self.params)
                    plan = self.asignador.last_plan
                    if plan is None:
                        nota = "sin decisión: la entrega caería después de las 00:30"
            except LookupError as exc:
                nota = str(exc)
        flows, info = infer_flows(log_df, prod.universe, start, t_feed)
        q = int((t - start).total_seconds() // 900) - 1
        sal = lle = None
        if 0 <= q < NB15:
            sal, lle = round(float(flows[:, q, 0].sum())), round(float(flows[:, q, 1].sum()))
        self.pending.extend(emitidas)
        tot = self.state["totales"]
        tot["bicis_a_mover"] += sum(-o.delta for o in emitidas if o.delta < 0)
        tot["visitas"] += len(emitidas)
        tot["recogidas"] += sum(-o.delta for o in aplicadas if o.delta < 0)
        tot["entregadas"] += sum(o.delta for o in aplicadas if o.delta > 0)
        estado = {k: lect[k] for k in ESTADO_KEYS}
        if self.state["inicio_estado"] is None:
            self.state["inicio_estado"] = estado
        paso = {"t": iso_min(t), "t_feed": iso_s(t_feed), "disparo": disparo, "espera_s": espera_s,
                "feed_atrasado": atrasado, "estado_feed": estado,
                "lecturas_desde_paso": [] if disparo == "inicio" else self.loop.lecturas_entre(self.prev_t_feed, lect["t_feed"]),
                "emitidas": [_orden(o) for o in emitidas], "aplicadas": [_orden(o) for o in aplicadas],
                "bicis_a_mover": sum(-o.delta for o in emitidas if o.delta < 0), "visitas": len(emitidas),
                "salidas_est": sal, "llegadas_est": lle,
                "ventana_est": [iso_min(t - timedelta(minutes=STEP)), iso_min(t)],
                "saltos_camioneta": info.get("camion_n"), "nota": nota}
        self.prev_t_feed = lect["t_feed"]
        self.state["pasos"].append(paso)
        self.state["pendientes"] = [_orden(o) for o in sorted(self.pending, key=_efectiva)]
        self.loop.eventos.publish("paso", {"sesion": self.id, "n": len(self.state["pasos"]), "t": paso["t"],
                                           "t_feed": paso["t_feed"], "disparo": disparo, "espera_s": espera_s,
                                           "feed_atrasado": atrasado, "visitas": paso["visitas"],
                                           "bicis_a_mover": paso["bicis_a_mover"],
                                           "aplicadas": len(paso["aplicadas"]), "estado_feed": estado})
        return paso

    def run(self):
        t = datetime.fromisoformat(self.state["inicio"])
        fin = datetime.fromisoformat(self.state["fin_emision"])
        self._evento_sesion()
        try:
            primero = True
            while not self.stop_event.is_set():
                if primero:
                    self.paso(t, disparo="inicio")
                    primero = False
                else:
                    espera = self.esperar_feed(t)
                    if espera is None:
                        break
                    self.paso(t, *espera)
                fase = self.state["fase"]
                if t + timedelta(minutes=STEP) >= fin:
                    self.state["fase"] = "cerrando"
                if t + timedelta(minutes=STEP) >= fin and not self.pending:
                    self.state["estado"], self.state["siguiente_paso"] = "terminada", None
                    self._save()
                    self._evento_sesion()
                    return
                t = t + timedelta(minutes=STEP)
                self.state["siguiente_paso"] = iso_min(t)
                self._save()
                if self.state["fase"] != fase:
                    self._evento_sesion()
            self.state["estado"], self.state["siguiente_paso"] = "detenida", None
            self._save()
            self._evento_sesion()
        except Exception as exc:
            log.exception("sesión %s", self.id)
            self.state.update(estado="error", error=f"{type(exc).__name__}: {exc}", siguiente_paso=None)
            self._save()
            self._evento_sesion()


class Asignaciones:
    """Sesiones de asignación; los JSON sobreviven a recargar la página."""

    def __init__(self, loop: LiveLoop, out_dir: Path | None = None, forecast_dir: Path | None = None,
                 clock=None, sleep=None, prod: Produccion | None = None, fz: dict | None = None):
        self.loop = loop
        self.dir = out_dir or SESSION_DIR
        self.forecast_dir = forecast_dir or FORECAST_DIR
        self.clock, self.sleep, self.prod, self.fz = clock, sleep, prod, fz
        self.sesiones: dict[str, Sesion] = {}
        self.lock = threading.Lock()
        # Un hilo no sobrevive a reiniciar el servidor: lo que quedó a medias se
        # marca. Solo si nadie la avanza (su siguiente paso ya pasó hace más de
        # 20 min); otro proceso puede estar corriéndola.
        now = (self.clock or self.loop.clock)()
        for p in self.dir.glob("*.json") if self.dir.exists() else []:
            s = json.loads(p.read_text())
            nxt = s.get("siguiente_paso")
            if s.get("estado") == "corriendo" and (not nxt or datetime.fromisoformat(nxt) < now - timedelta(minutes=20)):
                s.update(estado="error", error="el servidor se reinició con la sesión corriendo", siguiente_paso=None)
                p.write_text(json.dumps(s, ensure_ascii=False))

    def start(self, forecast_id: str, horas: int, thread: bool = True) -> str:
        if not isinstance(horas, int) or not 1 <= horas <= MAX_SESSION_H:
            raise ValueError(f"horas debe ser un entero entre 1 y {MAX_SESSION_H}")
        fc = leer_pronostico(forecast_id, self.forecast_dir)
        sid = f"{(self.clock or self.loop.clock)().strftime('%Y%m%d-%H%M%S')}-{fc['model']}-{uuid.uuid4().hex[:6]}"
        s = Sesion(sid, fc, horas, self.loop, self.dir, clock=self.clock, sleep=self.sleep, prod=self.prod, fz=self.fz)
        with self.lock:
            self.sesiones[sid] = s
        if thread:
            threading.Thread(target=s.run, daemon=True, name=f"asignacion-{sid}").start()
        else:
            s.run()
        return sid

    def get(self, sid: str) -> dict:
        if sid in self.sesiones:
            return self.sesiones[sid].state
        if not re.fullmatch(r"[\w\-]+", sid or ""):
            raise KeyError(sid)
        p = self.dir / f"{sid}.json"
        if not p.exists():
            raise KeyError(sid)
        return json.loads(p.read_text())

    def stop(self, sid: str) -> dict:
        s = self.sesiones.get(sid)
        if s is None:
            raise KeyError(sid)
        s.stop_event.set()
        return s.state

    def listar(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json"), reverse=True) if self.dir.exists() else []:
            s = self.sesiones[p.stem].state if p.stem in self.sesiones else json.loads(p.read_text())
            out.append({k: s.get(k) for k in ("id", "estado", "fase", "model", "inicio", "horas", "siguiente_paso")})
        return out


def main(argv=None):
    loop = LiveLoop()
    t = loop.poll()
    fc = pronosticar("ma_diaria", 1, loop.log_today(t), t, loop.meta)
    print(json.dumps({k: fc[k] for k in ("id", "t", "t_feed", "referencia")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
