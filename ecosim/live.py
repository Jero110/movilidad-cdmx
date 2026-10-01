"""Predicción en vivo (subtask ecosim2-app): del feed GBFS a una
recomendación de qué mover ahora, y la medición de qué tan buena habría sido.

Pipeline de una corrida en t (`run_at`):

1. **Estado.** Bicis disponibles, dañadas, anclajes libres y capacidad por
   estación = el último registro del feed GBFS con hora ≤ t, tomado del
   registro propio en disco (`gbfs/{día}.jsonl`, un renglón por lectura del
   feed). Fuera de servicio = `is_renting` o `is_installed` en 0.
2. **Pronóstico** de salidas y llegadas por estación y bloque de 60 min
   anclado a las 05:30, con el formato `Forecast` de ecosim (por default
   `daily`, el mejor brazo real del run 2):
   * `ma`: media de los días del mismo tipo (entre semana / fin de semana o
     festivo) de las 4 semanas anteriores (`pronostico.ma_counts`), con la
     corrección intradía de `pronostico.emit_day` (factor acotado
     (obs + 5)/(pron + 5) sobre lo transcurrido del día). Lo "observado" hoy
     son viajes **inferidos del feed** (abajo), no viajes reales, y el factor
     solo usa los bins de 15 min que el registro cubre sin huecos (sin
     registro de la mañana, "0 inferidos" no es "0 viajes"). **Sesgo:** los
     inferidos son netos por lectura (menos que los viajes reales) y la base
     es de viajes reales, así que el factor tiende a la baja.
   * `daily`: la misma media, sin corrección.
   * `model`: enchufable (`FORECASTERS`), no disponible en vivo: el LightGBM
     de `pronostico` necesita los viajes del día anterior, y no los hay.
   **Datos para la media.** Los datos abiertos de Ecobici (`data/ecobici/`)
   llegan hasta el último mes publicado (hoy, agosto 2026), así que para una
   fecha de hoy no hay 4 semanas previas. Se usa entonces el **fallback
   declarado**: las 4 semanas (28 días) que terminan en el último día
   completo publicado, del mismo tipo de día. Nunca se lee `2025-12.csv`
   (sellado) y nunca se usa un día ≥ el de la corrida.
3. **Asignador** (`ecosim.asignador.Asignador`) con lo congelado en
   `frozen.json` (λ, μ, cota de retiro, L, topes, bicis por movimiento) y el
   (f, H) congelado de la variante; bodega de partida 0 y sin órdenes
   pendientes (no sabemos qué movió Ecobici ni si se ejecutó lo recomendado).
4. **Riesgo**: flujo fluido sin mover nada (la misma proyección del
   asignador) sobre [t, t + L + H): primer minuto vacía o llena por estación.

Cada corrida se guarda en `runs/{YYYYmmdd-HHMM}_{modelo}.json` (pronóstico,
recomendación, proyección). `evaluate()` compara lo pronosticado contra lo
observado cuando el bloque ya pasó:

* **Salidas y llegadas inferidas** de los cambios del stock (disponibles +
  dañadas) entre lecturas consecutivas del feed: una baja cuenta como
  salidas y una subida como llegadas. Es un conteo **neto por intervalo**:
  una salida y una llegada en el mismo intervalo se cancelan, así que
  subestima los viajes reales. Cambios de ≥ `TRUCK_DELTA` bicis en una
  lectura se toman como camión de Ecobici y no cuentan como viajes.
* **Stock**: bicis proyectadas sin mover nada contra las observadas (en
  la realidad Ecobici sí mueve bicis).
* **E/F**: minutos vacía / llena proyectados contra observados en el
  horizonte, cuando ya pasó completo.

El loop (lectura del feed cada `POLL_S` s y una corrida cada 15 min) lo corre
`scripts/ecobici_mapa/server.py` en un hilo (`LiveLoop`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time as _time
import traceback
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ecosim import config as C
from ecosim import contracts as K
from ecosim import pronostico as P
from ecosim.asignador import Asignador, forecast_rates
from ecosim.days import day_type

LIVE_DIR = C.DERIVED / "live"
LOG_DIR = LIVE_DIR / "gbfs"               # {YYYY-MM-DD}.jsonl (fecha local de la lectura)
RUNS_DIR = LIVE_DIR / "runs"
PROFILE_DIR = LIVE_DIR / "perfiles"
GBFS_BASE = "https://gbfs.mex.lyftbikes.com/gbfs/es"

POLL_S = 60                  # lectura del feed para el registro (el feed se actualiza ~cada 10–60 s)
RUN_EVERY_MIN = C.STEP_MIN   # una corrida cada 15 min (05:30, 05:45, …)
TRUCK_DELTA = 5              # |Δ stock| ≥ esto en una lectura → camión, no viajes
MAX_GAP_MIN = 5              # evaluación: huecos del registro mayores invalidan el bloque
MA_DAYS = 28
BIN = P.BIN_MIN
NB15, N60 = P.NB15, P.N60
DEFAULT_MODEL = "daily"      # el mejor brazo real del run 2 (`best_real`); `ma` sigue en el selector
HIGHS_THREADS = 1
HIGHS_TIME_LIMIT = 60.0
SHORT_RE = re.compile(C.SHORT_NAME_RE + r"$")
SEALED_MONTH = date(2025, 12, 1)      # diciembre 2025: SELLADO, nunca se lee
log = logging.getLogger("ecosim.live")
_logged_sealed: set = set()


def now_local() -> datetime:
    """Hora local de la CDMX (UTC−6 fijo), naive, al minuto."""
    t = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=C.UTC_OFFSET_HOURS)
    return t.replace(second=0, microsecond=0)


def epoch_local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None) + timedelta(hours=C.UTC_OFFSET_HOURS)


def ceil_minute(t: datetime) -> datetime:
    return t if (t.second, t.microsecond) == (0, 0) else t.replace(second=0, microsecond=0) + timedelta(minutes=1)


# ======================================================================
# Feed GBFS y registro propio
# ======================================================================

def fetch_gbfs() -> dict:
    """Lee station_information + station_status y devuelve el mismo formato
    que `server.build_snapshot()` (lo que usa el registro)."""
    def get(feed):
        req = urllib.request.Request(f"{GBFS_BASE}/{feed}.json", headers={"User-Agent": "movilidad-cdmx/ecosim-live"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    info, status = get("station_information"), get("station_status")
    by_id = {s["station_id"]: s for s in status["data"]["stations"]}
    st = []
    for s in info["data"]["stations"]:
        live = by_id.get(s["station_id"])
        if live:
            st.append({"short_name": s.get("short_name", ""), "name": s["name"], "lat": s["lat"], "lon": s["lon"],
                       "capacity": s.get("capacity", 0), "bikes": live["num_bikes_available"],
                       "docks": live["num_docks_available"], "bikes_disabled": live.get("num_bikes_disabled", 0),
                       "renting": bool(live.get("is_renting", 0)), "installed": bool(live.get("is_installed", 0))})
    return {"last_updated": status.get("last_updated"), "stations": st}


def snapshot_record(snap: dict) -> dict:
    """Renglón del registro: hora del feed (local) y, por estación,
    [disponibles, dañadas, anclajes libres, capacidad, en servicio]."""
    t = epoch_local(snap["last_updated"]) if snap.get("last_updated") else now_local()
    s = {}
    for x in snap["stations"]:
        sn = str(x.get("short_name") or "")
        if not SHORT_RE.match(sn):
            continue
        s[sn] = [int(x["bikes"]), int(x.get("bikes_disabled", 0)), int(x["docks"]), int(x.get("capacity") or 0),
                 int(bool(x.get("renting")) and bool(x.get("installed")))]
    return {"t": t.isoformat(timespec="seconds"), "s": s}


def append_log(snap: dict, log_dir: Path | None = None) -> bool:
    """Agrega la lectura al registro del día; no repite una hora del feed ya escrita."""
    log_dir = log_dir or LOG_DIR
    rec = snapshot_record(snap)
    log_dir.mkdir(parents=True, exist_ok=True)
    p = log_dir / f"{rec['t'][:10]}.jsonl"
    if p.exists():
        with p.open("rb") as fh:
            try:
                fh.seek(-200_000, 2)
            except OSError:
                fh.seek(0)
            tail = fh.read().decode(errors="ignore").strip().splitlines()
        if tail and tail[-1].startswith('{"t": "' + rec["t"] + '"'):
            return False
    with p.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return True


def station_meta(snap: dict) -> dict:
    return {str(x["short_name"]): {"name": x["name"], "lat": x["lat"], "lon": x["lon"]}
            for x in snap["stations"] if SHORT_RE.match(str(x.get("short_name") or ""))}


def read_log(t0: datetime, t1: datetime, log_dir: Path = LOG_DIR) -> pd.DataFrame:
    """Registro en [t0, t1] como tabla larga (t, short_name, bikes, disabled, docks, cap, ok)."""
    rows = []
    d = t0.date()
    while d <= t1.date():
        p = log_dir / f"{d.isoformat()}.jsonl"
        if p.exists():
            for line in p.read_text().splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                t = datetime.fromisoformat(r["t"])
                if t0 <= t <= t1:
                    rows.extend((t, sn, *v) for sn, v in r["s"].items())
        d += timedelta(days=1)
    df = pd.DataFrame(rows, columns=["t", "short_name", "bikes", "disabled", "docks", "cap", "ok"])
    return df.sort_values(["short_name", "t"], kind="stable").reset_index(drop=True)


def state_at(log: pd.DataFrame, t: datetime) -> pd.DataFrame:
    """Último registro ≤ t por estación → estado del asignador."""
    lg = log[log["t"] <= t]
    if lg.empty:
        raise ValueError(f"sin lecturas del feed con hora ≤ {t}")
    last = lg.groupby("short_name").tail(1).sort_values("short_name")
    return pd.DataFrame({
        "short_name": last["short_name"].to_numpy(), "bikes": last["bikes"].to_numpy(int),
        "disabled": last["disabled"].to_numpy(int), "docks": last["docks"].to_numpy(int),
        "cap": last["cap"].to_numpy(int), "out_of_service": last["ok"].to_numpy(int) == 0,
        "t_feed": last["t"].to_numpy(),
    }).reset_index(drop=True)


def infer_flows(log: pd.DataFrame, names: list[str], day0: datetime, upto: datetime,
                truck: int = TRUCK_DELTA) -> tuple[np.ndarray, dict]:
    """Salidas/llegadas inferidas `[estación, 76, {dep, arr}]` por bin de 15
    min desde `day0`, con los cambios de stock (disponibles + dañadas) entre
    lecturas consecutivas con hora ≤ `upto`. El cambio se asigna al bin de la
    lectura posterior. Cambios ≥ `truck` bicis (o que tocan una lectura fuera
    de servicio) no cuentan: van a `info`."""
    out = np.zeros((len(names), NB15, 2))
    lg = log[(log["t"] <= upto) & (log["t"] >= day0 - timedelta(minutes=30))]
    info = {"camion_bicis": 0, "camion_n": 0, "fuera_servicio_n": 0, "lecturas": int(lg["t"].nunique())}
    if lg.empty:
        return out, info
    lg = lg.assign(stock=lg["bikes"] + lg["disabled"])
    g = lg.groupby("short_name")
    d = (lg["stock"] - g["stock"].shift()).to_numpy()
    okpair = ((lg["ok"] == 1) & (g["ok"].shift() == 1)).to_numpy()
    m = ((lg["t"] - pd.Timestamp(day0)) / pd.Timedelta(minutes=1)).to_numpy()
    si = pd.Index(names).get_indexer(lg["short_name"])
    has = ~np.isnan(d) & (m > 0) & (m <= C.WINDOW_MIN) & (si >= 0)
    big = has & (np.abs(d) >= truck)
    info["camion_n"] = int((big & okpair).sum())
    info["camion_bicis"] = int(np.abs(d[big & okpair]).sum())
    info["fuera_servicio_n"] = int((has & ~okpair & (d != 0)).sum())
    use = has & ~big & okpair & (d != 0)
    b = np.minimum(((m[use] - 1e-9) // BIN).astype(int), NB15 - 1)
    np.add.at(out, (si[use], b, 0), np.maximum(-d[use], 0))
    np.add.at(out, (si[use], b, 1), np.maximum(d[use], 0))
    return out, info


# ======================================================================
# Historia para el `ma` (datos abiertos de Ecobici)
# ======================================================================

_FILE_RE = re.compile(r"(\d{4})-(\d{2})")


def _is_sealed(d: date) -> bool:
    return (d.year, d.month) == (SEALED_MONTH.year, SEALED_MONTH.month)


def trip_files(raw_dir: Path = C.RAW_TRIPS_DIR) -> list[tuple[date, Path]]:
    """(primer día del mes, archivo) de los CSV de viajes. Diciembre 2025
    (`2025-12.csv`) está SELLADO: se excluye aquí de forma explícita y se
    registra en el log (una vez por archivo y proceso)."""
    out = []
    for p in sorted(raw_dir.glob("*.csv")):
        m = _FILE_RE.search(p.name)
        if not m:
            continue
        d = date(int(m.group(1)), int(m.group(2)), 1)
        if _is_sealed(d):
            if p.name not in _logged_sealed:
                _logged_sealed.add(p.name)
                log.warning("live: se excluye %s (diciembre 2025 está sellado)", p.name)
            continue
        out.append((d, p))
    return out


def _read_trips(files: list[Path], lo: datetime, hi: datetime) -> pd.DataFrame:
    import duckdb
    for f in files:
        assert "2025-12" not in f.name, "diciembre 2025 está sellado"
    flist = ", ".join(f"'{f}'" for f in files)
    con = duckdb.connect()
    df = con.sql(f"""
        select trim(replace(Ciclo_Estacion_Retiro, '"', '')) as o,
               trim(replace(Ciclo_EstacionArribo, '"', '')) as d,
               try_strptime(Fecha_Retiro || ' ' || Hora_Retiro, '%d/%m/%Y %H:%M:%S') as t_dep,
               try_strptime(Fecha_Arribo || ' ' || Hora_Arribo, '%d/%m/%Y %H:%M:%S') as t_arr
        from read_csv([{flist}], all_varchar=true, header=true)
    """)
    return con.execute("""
        select o, d, t_dep, t_arr from df
        where t_dep is not null and t_arr is not null and t_arr >= t_dep
          and ((t_dep >= ? and t_dep < ?) or (t_arr >= ? and t_arr < ?))
    """, [lo, hi, lo, hi]).df()


def _last_full_day(files: list[tuple[date, Path]]) -> date:
    """Último día d cuya ventana [d 05:30, d+1 00:30) está completa en el último archivo."""
    import duckdb
    p = files[-1][1]
    mx = duckdb.connect().execute(f"""
        select max(try_strptime(Fecha_Arribo || ' ' || Hora_Arribo, '%d/%m/%Y %H:%M:%S'))
        from read_csv('{p}', all_varchar=true, header=true)""").fetchone()[0]
    return (pd.Timestamp(mx) - timedelta(days=1, minutes=30)).date()


def _weekday(d: date) -> bool:
    return day_type(d) == "weekday"


def history_profile(target: date, names: list[str], raw_dir: Path = C.RAW_TRIPS_DIR,
                    cache_dir: Path = PROFILE_DIR) -> tuple[np.ndarray, dict]:
    """Media `[estación, 76, 2]` (bins de 15 min) de los días del mismo tipo
    que `target` en las 4 semanas previas; si no hay datos para esas
    semanas, las 4 semanas que terminan en el último día publicado
    (fallback). Solo días < `target`."""
    files = trip_files(raw_dir)
    if not files:
        raise FileNotFoundError(f"sin CSV de viajes en {raw_dir}")
    last = _last_full_day(files)
    want_hi = target - timedelta(days=1)
    fallback = last < want_hi
    hi = min(want_hi, last)
    lo = hi - timedelta(days=MA_DAYS - 1)
    assert hi < target, "la historia debe ser anterior al día de la corrida"
    days = [lo + timedelta(days=k) for k in range(MA_DAYS)]
    wd = _weekday(target)
    sealed = [d for d in days if _is_sealed(d)]
    if sealed:
        log.warning("live: la ventana del ma %s → %s toca diciembre 2025 (sellado): %d días excluidos",
                    lo, hi, len(sealed))
    uni = hashlib.sha1("|".join(names).encode()).hexdigest()[:10]
    key = f"{lo.isoformat()}_{hi.isoformat()}_{'weekday' if wd else 'weekend'}_{uni}"
    cp = cache_dir / f"ma_{key}.npz"
    m_lo, m_hi = date(lo.year, lo.month, 1), date((hi + timedelta(days=1)).year, (hi + timedelta(days=1)).month, 1)
    fl = [p for d, p in files if m_lo <= d <= m_hi]
    if cp.exists():
        z = np.load(cp, allow_pickle=False)
        prof, used = z["prof"], [date.fromisoformat(x) for x in z["used"]]
    else:
        # meses que tocan [lo 05:30, hi+1 00:30) (particionados por mes de llegada)
        t_lo = datetime.combine(lo, C.DAY_START)
        t_hi = datetime.combine(hi + timedelta(days=1), C.DAY_END)
        tr = _read_trips(fl, t_lo, t_hi)
        counts = P.count_trips(tr, names, days)
        valid = counts.sum(axis=(1, 2, 3)) > 0
        idx = [i for i, d in enumerate(days) if _weekday(d) == wd and valid[i] and not _is_sealed(d)]
        used = [days[i] for i in idx]
        prof = counts[idx].astype(np.float64).mean(axis=0) if idx else np.zeros(counts.shape[1:])
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.savez(cp, prof=prof, used=np.array([d.isoformat() for d in used]))
    meta = {
        "tipo_dia": "entre semana" if wd else "fin de semana o festivo",
        "rango": [lo.isoformat(), hi.isoformat()], "dias_usados": [d.isoformat() for d in used],
        "ultimo_dia_publicado": last.isoformat(), "fallback": bool(fallback),
        "nota": (f"No hay viajes publicados de las 4 semanas previas a {target.isoformat()}: se usan las 4 semanas "
                 f"que terminan en el último día publicado ({last.isoformat()}), mismo tipo de día."
                 if fallback else "4 semanas previas, mismo tipo de día (como `ma` en ecosim)."),
        "archivos": [p.name for p in fl],
        "excluidos_sellados": [d.isoformat() for d in sealed if _weekday(d) == wd],
    }
    return prof, meta


# ======================================================================
# Pronóstico en vivo (formato Forecast de ecosim)
# ======================================================================

def covered_bins(log: pd.DataFrame, day0: datetime, sb: int, max_gap: float = MAX_GAP_MIN) -> np.ndarray:
    """`[76]` bool: bins de 15 min ya transcurridos (< sb) que el registro del
    feed cubre sin huecos > `max_gap` min. Solo en esos hay viajes inferidos."""
    ts = np.sort(log["t"].unique()) if len(log) else np.array([])
    cov = np.zeros(NB15, bool)
    for b in range(min(sb, NB15)):
        a = day0 + timedelta(minutes=BIN * b)
        cov[b] = _max_gap_min(ts, a, a + timedelta(minutes=BIN)) <= max_gap
    return cov


def _forecast_ma(base, today, sb, cov):
    """`pronostico.emit_day("ma", …)` con la corrección intradía calculada
    solo sobre los bins cubiertos por el registro (`cov`): factor por
    estación = clip((obs + 5) / (pron + 5), 0.5, 2) con obs y pron sumados en
    esos bins. Sin bins cubiertos el factor es 1 (= `daily`). Los bins ya
    transcurridos valen lo inferido si están cubiertos y la base si no."""
    obs = (today * cov[None, :, None]).sum(axis=1)
    pred = (base * cov[None, :, None]).sum(axis=1)
    factor = np.clip((obs + P.K_SHRINK) / (pred + P.K_SHRINK), *P.FACTOR_CLIP)[:, None, :] if cov.any() else 1.0
    adj = base * factor
    past = np.zeros(NB15, bool)
    past[:sb] = True
    adj[:, past & cov, :] = today[:, past & cov, :]
    adj[:, past & ~cov, :] = base[:, past & ~cov, :]
    return P.to60(adj, axis=1)


def _forecast_daily(base, today, sb, cov):
    return P.emit_day("daily", base, today, sb)


def _forecast_model(base, today, sb, cov):
    raise NotImplementedError("`model` (LightGBM de ecosim.pronostico) necesita los viajes reales de los días "
                              "previos y del día en curso; en vivo no los hay. Queda enchufable en FORECASTERS.")


# nombre → (función (base15, hoy15, bin actual, bins cubiertos) → [S, 19, 2], descripción)
FORECASTERS = {
    "ma": (_forecast_ma, "Media 4 semanas mismo tipo de día + corrección intradía con viajes inferidos del feed"),
    "daily": (_forecast_daily, "Media 4 semanas mismo tipo de día, sin corrección intradía"),
    "model": (_forecast_model, "LightGBM (ecosim.pronostico): no disponible en vivo"),
}


def forecast_frame(t: datetime, names: list[str], full60: np.ndarray, variant: str, f: int) -> pd.DataFrame:
    """Emisión en t: bloques de 60 min desde el que contiene a t hasta 00:30."""
    day0 = datetime.combine(C.window_day(t), C.DAY_START)
    b0 = int((t - day0) // timedelta(minutes=60))
    blocks = list(range(max(b0, 0), N60))
    S = len(names)
    df = pd.DataFrame({
        "issued_at": pd.Timestamp(t), "variant": variant,
        "short_name": np.repeat(names, len(blocks)),
        "block_start": np.tile([pd.Timestamp(day0 + timedelta(hours=b)) for b in blocks], S),
        "block_min": 60,
        "departures": full60[:, blocks, 0].ravel().astype(float),
        "arrivals": full60[:, blocks, 1].ravel().astype(float),
        "refresh_min": int(f), "horizon_h": N60,
    })
    for c in K.FORECAST_OPTIONAL:
        df[c] = np.nan
    return K.validate_forecast(df[K.FORECAST_COLUMNS])


def load_frozen() -> dict:
    """frozen.json de `replay.results_dir()` (ECOSIM_RESULTS_DIR o ecosim/results)."""
    from ecosim import replay
    fz = replay.load_frozen()
    fz["_path"] = replay.rel(replay.results_dir() / "frozen.json")
    return fz


# ======================================================================
# Una corrida
# ======================================================================

def run_at(t: datetime, model: str = DEFAULT_MODEL, log: pd.DataFrame | None = None, fz: dict | None = None,
           meta: dict | None = None, profile=None, log_dir: Path = LOG_DIR) -> dict:
    """Corrida en t. Solo usa lecturas del feed con hora ≤ t y viajes
    históricos de días < t. `log`/`profile` se pueden inyectar (tests)."""
    t0 = _time.perf_counter()
    t = pd.Timestamp(t).to_pydatetime().replace(second=0, microsecond=0)
    if model not in FORECASTERS:
        raise ValueError(f"modelo {model!r}: {sorted(FORECASTERS)}")
    fz = fz or load_frozen()
    wd = C.window_day(t)
    day0, day_end = C.day_bounds(wd)
    if log is None:
        log = read_log(day0 - timedelta(minutes=30), t, log_dir)
    log = log[log["t"] <= t]                                    # causalidad
    st = state_at(log, t)
    names = st["short_name"].tolist()
    out = {"t": t.isoformat(timespec="minutes"), "model": model, "window_day": wd.isoformat(),
           "model_desc": FORECASTERS[model][1]}
    out["state"] = {"t_feed": pd.Timestamp(st["t_feed"].max()).isoformat(), "n": len(st),
                    "bikes": int(st["bikes"].sum()), "disabled": int(st["disabled"].sum()),
                    "empty": int(((st["bikes"] == 0) & ~st["out_of_service"]).sum()),
                    "full": int(((st["docks"] == 0) & ~st["out_of_service"]).sum()),
                    "out_of_service": int(st["out_of_service"].sum())}
    if not (day0 <= t < day_end):
        out["nota"] = "Fuera del horario de servicio (05:30–00:30): no hay pronóstico ni recomendación."
        return out

    # --- pronóstico
    if profile is None:
        base, hmeta = history_profile(wd, names)
    else:
        base, hmeta = profile
    assert max(date.fromisoformat(d) for d in hmeta["dias_usados"] or [wd - timedelta(days=1)]) < wd
    sb = int((t - day0) // timedelta(minutes=BIN))
    today, finfo = infer_flows(log, names, day0, t)
    cov = covered_bins(log, day0, sb)
    finfo["bins_cubiertos"] = int(cov.sum())
    finfo["bins_transcurridos"] = sb
    full60 = FORECASTERS[model][0](base, today, sb, cov)       # NotImplementedError para `model`
    b = fz["best"].get(model) or fz["best"]["ma"]
    f, H, L = int(b["f"]), int(b["H"]), int(fz["L"])
    fc = forecast_frame(t, names, full60, model, f)
    out["fuente"] = {**hmeta, "inferidos_hoy": {**finfo, "salidas": float(today[:, :sb, 0].sum()),
                                                "llegadas": float(today[:, :sb, 1].sum())},
                      "correccion": ("factor intradía sobre %d bins de 15 min cubiertos por el registro" % cov.sum()
                                     if model == "ma" and cov.any() else "sin corrección intradía (factor 1)")}
    params = K.PolicyParams(lead_min=L, H_horas=H, lam=float(fz["lam"]), mu=float(fz["mu"]),
                            tope_hora=int(fz["tope_hora"]), tope_bodega=int(fz["tope_bodega"]), block_min=60,
                            max_bikes_per_move=int(fz["max_move"]), refresh_min=f, extra={"variant": model})
    out["params"] = {"f": f, "H": H, "L": L, "lam": params.lam, "mu": params.mu, "retiro": fz["retiro"],
                     "tope_hora": params.tope_hora, "tope_bodega": params.tope_bodega,
                     "max_move": params.max_bikes_per_move, "bodega_inicial": 0, "pendientes": 0,
                     "frozen_de": str(fz.get("_path", ""))}

    # --- riesgo: flujo sin mover nada en [t, t + L + H)
    m_t = int((t - day0) // timedelta(minutes=1))
    m_end = min(m_t + L + 60 * H, C.WINDOW_MIN)
    dep, arr = forecast_rates(fc, names, t, 60, model, f, need=(m_t, m_end))
    Kc = (st["bikes"] + st["docks"]).to_numpy(float)
    s = st["bikes"].to_numpy(float)
    first_e = np.full(len(names), -1)
    first_f = np.full(len(names), -1)
    e_min = np.zeros(len(names), int)
    f_min = np.zeros(len(names), int)
    hours = list(range(60, m_end - m_t + 1, 60))
    proj = []
    for k, m in enumerate(range(m_t, m_end)):
        s = np.clip(s + arr[:, m] - dep[:, m], 0, Kc)
        emp, ful = (s < 0.5) & (Kc > 0), (s > Kc - 0.5) & (Kc > 0)
        first_e[(first_e < 0) & emp] = k + 1
        first_f[(first_f < 0) & ful] = k + 1
        e_min += emp
        f_min += ful
        if k + 1 in hours:
            proj.append(np.round(s, 1).tolist())
    oos = st["out_of_service"].to_numpy(bool)
    first_e[oos], first_f[oos] = -1, -1
    out["stations"] = names
    blocks = sorted(fc["block_start"].unique())
    fcw = fc.pivot(index="short_name", columns="block_start", values=["departures", "arrivals"]).loc[names]
    out["forecast"] = {"blocks": [pd.Timestamp(x).strftime("%H:%M") for x in blocks],
                       "block_start": [pd.Timestamp(x).isoformat() for x in blocks],
                       "dep": np.round(fcw["departures"].to_numpy(), 2).tolist(),
                       "arr": np.round(fcw["arrivals"].to_numpy(), 2).tolist()}
    out["state_now"] = {"bikes": st["bikes"].astype(int).tolist(), "docks": st["docks"].astype(int).tolist(),
                        "disabled": st["disabled"].astype(int).tolist(), "oos": oos.tolist()}
    out["projection"] = {"horizon_min": m_end - m_t, "hours": hours, "stock": proj,
                         "first_empty": first_e.tolist(), "first_full": first_f.tolist(),
                         "E_min": e_min.tolist(), "F_min": f_min.tolist()}

    # --- asignador
    state = K.SimState(t=t, stations=st.drop(columns=["t_feed"]), warehouse=0)
    from ecosim.replay import reset_highs_threads
    reset_highs_threads()
    a = Asignador(threads=HIGHS_THREADS, retiro=fz["retiro"], time_limit=HIGHS_TIME_LIMIT)
    orders = a.decide(t, state, [], fc, params)
    p = a.last_plan
    if p is not None and p.status.startswith("fallback"):
        out["aviso"] = f"HiGHS no dio solución ({p.status}): no se recomienda nada en esta corrida."
    pos = {n: i for i, n in enumerate(names)}
    eff = t + timedelta(minutes=L)
    out["orders"] = []
    if p is not None:
        idx = np.arange(len(p.names))
        c_y, c_0 = p.cost[idx, p.y], p.cost[idx, p.phat]
        for o in sorted(orders, key=lambda o: -abs(o.delta)):
            i = pos[o.short_name]
            out["orders"].append({"i": i, "s": o.short_name, "delta": int(o.delta),
                                  "effective_at": eff.strftime("%H:%M"), "bikes_now": int(st["bikes"].iat[i]),
                                  "p": round(float(p.p[i]), 1), "K": int(p.K[i]),
                                  "EF_sin": float(c_0[i]), "EF_con": float(c_y[i])})
        out["plan"] = {"status": p.status, "moves_avail": int(p.moves_avail),
                       "bodega_bounds": list(map(int, p.bodega_bounds)), "EF_sub_sin": float(c_0.sum()),
                       "EF_sub_con": float(c_y.sum()), "solve_s": round(float(p.solve_s), 2),
                       "effective_at": eff.isoformat(timespec="minutes"),
                       "window": [(t + timedelta(minutes=L)).strftime("%H:%M"),
                                  (t + timedelta(minutes=L + 60 * H)).strftime("%H:%M")]}
    else:
        out["plan"] = None
        out["nota"] = "t + L cae en o después de las 00:30: ya no se emiten órdenes hoy."
    out["seconds"] = round(_time.perf_counter() - t0, 1)
    return out


def save_run(run: dict, runs_dir: Path = RUNS_DIR) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    t = datetime.fromisoformat(run["t"])
    p = runs_dir / f"{t:%Y%m%d-%H%M}_{run['model']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(run, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(p)
    return p


def list_runs(runs_dir: Path = RUNS_DIR) -> list[Path]:
    return sorted(runs_dir.glob("*.json"))


# ======================================================================
# Qué tan bueno sería: pronóstico vs observado
# ======================================================================

def _max_gap_min(ts: np.ndarray, a: datetime, b: datetime) -> float:
    """Hueco más grande (min) del registro dentro de [a, b], contando los bordes."""
    x = np.sort(np.array([pd.Timestamp(v).value for v in ts if a <= pd.Timestamp(v) <= b], dtype=np.int64))
    pts = np.r_[pd.Timestamp(a).value, x, pd.Timestamp(b).value]
    return float(np.diff(pts).max() / 6e10) if len(pts) > 1 else np.inf


def evaluate(runs_dir: Path = RUNS_DIR, log_dir: Path = LOG_DIR, now: datetime | None = None) -> dict:
    """Error acumulado de las corridas guardadas cuyos bloques ya pasaron."""
    now = now or now_local()
    runs = [json.loads(p.read_text()) for p in list_runs(runs_dir)]
    runs = [r for r in runs if r.get("forecast")]
    per_lead: dict = {}
    stock_err: dict = {}
    ef_rows = []
    n_runs = 0
    logs: dict = {}
    for r in runs:
        t = datetime.fromisoformat(r["t"])
        wd = date.fromisoformat(r["window_day"])
        if wd not in logs:
            d0, d1 = C.day_bounds(wd)
            logs[wd] = read_log(d0 - timedelta(minutes=30), d1 + timedelta(minutes=5), log_dir)
        lg = logs[wd]
        if lg.empty:
            continue
        lg_t = np.sort(lg["t"].unique())
        names = r["stations"]
        day0 = C.day_bounds(wd)[0]
        used = False
        inferred, _ = infer_flows(lg, names, day0, min(now, lg["t"].max()))
        obs60 = P.to60(inferred, axis=1)
        for j, bs in enumerate(r["forecast"]["block_start"]):
            a = datetime.fromisoformat(bs)
            b = a + timedelta(minutes=60)
            if a < t or b > now or b > pd.Timestamp(lg["t"].max()).to_pydatetime():
                continue                                   # bloque en curso en t o aún no termina
            if _max_gap_min(lg_t, a, b) > MAX_GAP_MIN:
                continue
            k = int((a - day0) // timedelta(minutes=60))
            lead = int((a - t) // timedelta(minutes=60))
            pd_dep = np.array([row[j] for row in r["forecast"]["dep"]])
            pd_arr = np.array([row[j] for row in r["forecast"]["arr"]])
            o_dep, o_arr = obs60[:, k, 0], obs60[:, k, 1]
            acc = per_lead.setdefault(lead, [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            acc[0] += len(names)
            acc[1] += float(np.abs(pd_dep - o_dep).sum())
            acc[2] += float(np.abs(pd_arr - o_arr).sum())
            acc[3] += float(pd_dep.sum())
            acc[4] += float(o_dep.sum())
            acc[5] += float(pd_arr.sum())
            acc[6] += float(o_arr.sum())
            used = True
        # stock proyectado (sin mover) vs observado
        pr = r.get("projection") or {}
        for h, stock in zip(pr.get("hours", []), pr.get("stock", [])):
            th = t + timedelta(minutes=h)
            if th > now:
                continue
            near = lg[(lg["t"] <= th) & (lg["t"] >= th - timedelta(minutes=3))]
            if near.empty:
                continue
            ob = near.groupby("short_name").tail(1).set_index("short_name")["bikes"]
            ob = ob.reindex(names).to_numpy(float)
            ok = ~np.isnan(ob) & ~np.array(r["state_now"]["oos"])
            e = np.abs(np.array(stock) - ob)[ok]
            acc = stock_err.setdefault(h, [0, 0.0])
            acc[0] += int(ok.sum())
            acc[1] += float(e.sum())
            used = True
        # E/F en el horizonte completo
        hz = pr.get("horizon_min")
        if hz and t + timedelta(minutes=hz) <= now and _max_gap_min(lg_t, t, t + timedelta(minutes=hz)) <= MAX_GAP_MIN:
            obs_e, obs_f = observed_ef(lg, names, t, t + timedelta(minutes=hz))
            oos = np.array(r["state_now"]["oos"])
            ef_rows.append({"t": r["t"], "model": r["model"], "horizon_min": hz,
                            "E_pron": int(np.array(pr["E_min"])[~oos].sum()), "F_pron": int(np.array(pr["F_min"])[~oos].sum()),
                            "E_obs": int(obs_e[~oos].sum()), "F_obs": int(obs_f[~oos].sum())})
            used = True
        n_runs += used
    leads = []
    for lead, a in sorted(per_lead.items()):
        leads.append({"lead_h": lead, "n": a[0], "mae_salidas": a[1] / a[0], "mae_llegadas": a[2] / a[0],
                      "salidas_pron": a[3], "salidas_obs": a[4], "llegadas_pron": a[5], "llegadas_obs": a[6]})
    return {
        "now": now.isoformat(timespec="minutes"), "corridas": len(runs), "corridas_evaluadas": n_runs,
        "por_antelacion": leads,
        "stock": [{"h_min": h, "n": a[0], "mae_bicis": a[1] / a[0]} for h, a in sorted(stock_err.items()) if a[0]],
        "EF": ef_rows[-50:],
        "notas": [
            "Salidas/llegadas observadas = inferidas del feed GBFS (cambio neto de disponibles + dañadas entre "
            "lecturas de ~1 min): subestiman los viajes reales; cambios ≥ %d bicis se toman como camión." % TRUCK_DELTA,
            "Stock y E/F proyectados SIN mover nada; en la realidad Ecobici sí mueve bicis.",
            "Solo cuentan bloques que empiezan después de la corrida, ya terminados y con registro sin huecos "
            "> %d min." % MAX_GAP_MIN,
        ],
    }


def observed_ef(log: pd.DataFrame, names: list[str], a: datetime, b: datetime) -> tuple[np.ndarray, np.ndarray]:
    """Minutos vacía (0 disponibles) y llena (0 anclajes libres) por estación
    en [a, b), con el registro como escalón (cada lectura vale hasta la siguiente)."""
    e = np.zeros(len(names))
    f = np.zeros(len(names))
    pos = {n: i for i, n in enumerate(names)}
    lg = log[log["t"] <= b]
    for sn, g in lg.groupby("short_name"):
        i = pos.get(sn)
        if i is None:
            continue
        ts = g["t"].to_numpy()
        nxt = np.r_[ts[1:], np.datetime64(b)]
        lo = np.maximum(ts, np.datetime64(a))
        hi = np.minimum(nxt, np.datetime64(b))
        dur = np.clip((hi - lo) / np.timedelta64(1, "m"), 0, None)
        e[i] = float((dur * (g["bikes"].to_numpy() == 0)).sum())
        f[i] = float((dur * (g["docks"].to_numpy() == 0)).sum())
    return e, f


# ======================================================================
# Loop (lo corre server.py en un hilo)
# ======================================================================

class LiveLoop:
    """Hilo: lee el feed cada `POLL_S` s (registro) y corre `run_at` con el
    modelo por defecto en cada múltiplo de 15 min (05:30, 05:45, …).
    `snapshot_fn` devuelve el snapshot en el formato de `server.build_snapshot`."""

    def __init__(self, snapshot_fn=fetch_gbfs, model: str = DEFAULT_MODEL, poll_s: int = POLL_S):
        self.snapshot_fn, self.model, self.poll_s = snapshot_fn, model, poll_s
        self.lock = threading.RLock()     # poll() y run_now() (que llama a poll) lo toman
        self.last_error: str | None = None
        self.last_poll: str | None = None
        self.last_run: str | None = None
        self.meta: dict = {}
        self.running = False
        self._stop = threading.Event()
        self._done: set = set()

    def poll(self):
        """Lee el feed y lo agrega al registro; con el lock para que el loop
        y "Correr ahora" no escriban la misma lectura dos veces."""
        with self.lock:
            snap = self.snapshot_fn()
            append_log(snap)
            self.meta.update(station_meta(snap))
            self.last_poll = now_local().isoformat(timespec="seconds")
            return snap

    def run_now(self, model: str | None = None, t: datetime | None = None) -> dict:
        """Lee el feed y corre. La hora de la corrida es la de la lectura
        redondeada hacia arriba al minuto (o `t` si es posterior): todo lo que
        usa tiene hora ≤ t."""
        with self.lock:
            snap = self.poll()
            t_feed = ceil_minute(datetime.fromisoformat(snapshot_record(snap)["t"]))
            r = run_at(max(t or t_feed, t_feed), model or self.model)
            if r.get("forecast"):
                save_run(r)
            self.last_run = r["t"]
            return r

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll()
                t = now_local()
                if t.minute % RUN_EVERY_MIN == 0 and t not in self._done:
                    self._done.add(t)
                    self.run_now(self.model, t)
                self.last_error = None
            except Exception as e:   # el loop no se cae por un feed caído
                self.last_error = f"{now_local():%H:%M} {type(e).__name__}: {e}"
                traceback.print_exc()
            # despertar al inicio del minuto siguiente (o cada poll_s)
            self._stop.wait(min(self.poll_s, 60 - datetime.now().second + 1))

    def start(self):
        th = threading.Thread(target=self._loop, name="ecosim-live", daemon=True)
        th.start()
        self.running = True
        return th

    def stop(self):
        self._stop.set()
        self.running = False


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Una corrida en vivo (lee el feed, pronostica y asigna).")
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(FORECASTERS))
    ap.add_argument("--eval", action="store_true", help="solo imprime la evaluación acumulada")
    a = ap.parse_args(argv)
    if a.eval:
        print(json.dumps(evaluate(), indent=2, ensure_ascii=False))
        return
    r = LiveLoop(model=a.model).run_now()
    print(json.dumps({k: r.get(k) for k in ("t", "model", "state", "fuente", "params", "plan", "nota", "seconds")},
                     indent=2, ensure_ascii=False, default=str))
    for o in r.get("orders", [])[:15]:
        print(o)


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
