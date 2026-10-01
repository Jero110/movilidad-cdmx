"""Constantes compartidas de ecosim (plan 2026-09-28-ecosim, sección 1.1;
run 2: plan 2026-09-28-ecosim2, subtask fundamentos2).

Todos los tiempos internos son hora local de la Ciudad de México, *naive*
(sin tz). La conversión desde UTC se hace una sola vez, en `ecosim.data`.

El día `d` es la ventana simulada que empieza el `d` a las 05:30 y termina el
`d + 1` a las 00:30 (run 2). Toda función que recibe un `day` usa esa
convención: `day_bounds(d)` devuelve (d 05:30, d+1 00:30).
"""

from __future__ import annotations

import os
from datetime import date, time, timedelta
from pathlib import Path

# --- rutas -------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
# `data/` está en .gitignore; en los worktrees `data/ecobici` y
# `data/derived/ecosim` son symlinks al checkout principal.
DATA_ROOT = Path(os.environ.get("ECOSIM_DATA_ROOT", REPO_ROOT / "data"))
RAW_TRIPS_DIR = DATA_ROOT / "ecobici"
DERIVED = DATA_ROOT / "derived" / "ecosim"
SNAPSHOT_DIR = DERIVED / "snapshots"
TRIPS_PARQUET = DERIVED / "trips_2025_01_11.parquet"
TRIPS_AUDIT = DERIVED / "trips_2025_01_11_audit.json"
DAYS_JSON = Path(__file__).resolve().parent / "days.json"

# Archivos crudos de viajes que se pueden leer: 2025-01..2025-11.
# Diciembre 2025 (`2025-12.csv`) está SELLADO y nunca se lee.
RAW_TRIP_FILES = [f"2025-{m:02d}.csv" for m in range(1, 10)] + ["2025-10-1.csv", "2025-11.csv"]

# --- GBFS (bucket público) -----------------------------------------------------
GBFS_BUCKET = "s3://bike-sharing-history/mexico-city/ecobici"
GBFS_S3_ENDPOINT = "storage.googleapis.com"
# Archivos mensuales, particionados por mes UTC. La ventana de snapshots del
# día d, [d 05:00, d+1 01:00) local = [d 11:00, d+1 07:00) UTC, cae en el
# archivo del mes de d y, si d es fin de mes, también en el del mes siguiente.
# La última ventana cacheada es la del 2025-11-29 (termina el 30-nov 07:00 UTC,
# dentro de Nov.parquet): Dec.parquet nunca se toca.
GBFS_MONTHS = [(2025, "Aug"), (2025, "Sep"), (2025, "Oct"), (2025, "Nov")]
SNAPSHOT_START = date(2025, 8, 1)
SNAPSHOT_END = date(2025, 11, 29)          # inclusive; la ventana del 30-nov llega a diciembre (sellado)
# [inicio, fin) local; el fin es del día SIGUIENTE (05:00 del d → 01:00 del d+1).
SNAPSHOT_WINDOW = (time(5, 0), time(1, 0))
SNAPSHOT_WINDOW_MIN = 20 * 60
SHORT_NAME_RE = r"\d{3}(-\d{3})?"           # "038", "271-272"; lo demás es de prueba
# Medido (FUNDAMENTOS.md): el estado del feed es ~30 s anterior a
# `committed_at`. `data.snapshots` NO lo aplica (su `t` es el commit crudo);
# sí lo aplican `medicion` y `data.initial_state` (al llevar el stock a 05:30).
GBFS_COMMIT_LAG_S = 30

# --- tiempo --------------------------------------------------------------------
TZ = "America/Mexico_City"   # UTC−6 fijo, sin horario de verano desde oct-2022
UTC_OFFSET_HOURS = -6

# Ventana simulada [05:30, 00:30 del día siguiente): horario de servicio de
# Ecobici. Run 1 usaba [05:30, 12:30). Auditoría de la noche en FUNDAMENTOS.md.
DAY_START = time(5, 30)
DAY_END = time(0, 30)                       # del día SIGUIENTE
WINDOW_MIN = 19 * 60

# Paso de decisión: cada 15 min (cadencia del feed en vivo)
STEP_MIN = 15

# Lead time L (orden emitida en t se hace efectiva en t+L)
LEAD_MIN = 60
LEAD_GRID_MIN = [60, 45, 30]

# Bloques del pronóstico, anclados a las 05:30. Nunca de 15 min.
BLOCK_MIN = 60
BLOCK_GRID_MIN = [60, 30]

# Horizonte H del asignador, en horas (grid del run 1; en el run 2 h se sube
# 1, 2, 3, … hasta que deje de mejorar, lo decide `integracion2`)
H_GRID_HOURS = [1, 2, 3]

# --- run 2 -------------------------------------------------------------------------
# Tope de bicis por orden (capacidad de camioneta): p99 de las visitas de
# Ecobici sin pares ±1 (docs/executor/runs/2026-09-28-ecosim-run1/tope-bicis-por-movimiento.md).
MAX_BIKES_PER_MOVE = 22
MAX_BIKES_PER_MOVE_SENS = (14, 22, 42)      # 42 = camioneta con remolque
# Cada cuánto se re-emite el pronóstico (min) y tamaño de su bloque
FORECAST_REFRESH_MIN = (15, 60, 180)
FORECAST_BLOCK_MIN = 60

# Radio de desvío para salidas en estación vacía
DETOUR_RADIUS_M = 500

# Estado inicial: snapshot con desfase mayor a esto respecto a 05:30 se marca
INITIAL_STALE_MIN = 10

# --- días ------------------------------------------------------------------------
# Rango de los días de evaluación y de selección. El 2025-11-30 queda fuera:
# su ventana termina el 1-dic (sellado).
EVAL_START = date(2025, 9, 1)
EVAL_END = date(2025, 11, 29)              # inclusive
EVAL_SEED = 20260928
N_EVAL_WEEKDAY = 11
N_EVAL_WEEKEND = 4                          # fin de semana o festivo (MX)
# Los 15 días de evaluación del run 1 (`ecosim/days.json` en el tag
# `ecosim-run1`); el run 2 los conserva si cumplen la cobertura nueva.
RUN1_EVAL_DAYS = (
    "2025-09-03", "2025-09-15", "2025-09-26", "2025-09-28", "2025-10-07",
    "2025-10-11", "2025-10-13", "2025-10-15", "2025-10-22", "2025-10-25",
    "2025-11-07", "2025-11-12", "2025-11-15", "2025-11-19", "2025-11-24",
)
# 15 días de selección (fuera de muestra), disjuntos de los de evaluación
SELECTION_SEED = 20260929
N_SEL_WEEKDAY = 11
N_SEL_WEEKEND = 4
# Cobertura: fracción de bloques de COVERAGE_BLOCK_MIN en [05:30, 00:30) con
# ≥1 snapshot (run 1: bloques de 15 min en [05:30, 12:30)).
COVERAGE_BLOCK_MIN = 60
COVERAGE_MIN = 0.90

# Entrenamiento de modelos
TRAIN_START = date(2025, 1, 1)
TRAIN_END = date(2025, 8, 31)               # inclusive

# Diciembre 2025 sellado: nada en ecosim debe leer fechas desde aquí.
SEALED_FROM = date(2025, 12, 1)

# --- geografía -------------------------------------------------------------------
# Centro aproximado del sistema; estaciones a más de esto se marcan inválidas.
CDMX_CENTER = (19.40, -99.17)               # (lat, lon)
MAX_KM_FROM_CENTER = 50.0
EARTH_RADIUS_M = 6_371_000.0


def day_bounds(day) -> tuple:
    """(inicio, fin) de la ventana simulada del día, como datetime naive:
    (d 05:30, d+1 00:30)."""
    from datetime import datetime
    d = as_date(day)
    start = datetime.combine(d, DAY_START)
    end = start + timedelta(minutes=WINDOW_MIN)
    assert end.time() == DAY_END
    return start, end


def snapshot_bounds(day) -> tuple:
    """[inicio, fin) de la ventana de snapshots del día: (d 05:00, d+1 01:00)."""
    from datetime import datetime
    d = as_date(day)
    start = datetime.combine(d, SNAPSHOT_WINDOW[0])
    end = start + timedelta(minutes=SNAPSHOT_WINDOW_MIN)
    assert end.time() == SNAPSHOT_WINDOW[1]
    return start, end


def decision_times(day) -> list:
    """05:30, 05:45, …, 00:15 (del día siguiente): todos los pasos de la
    ventana. Quien decide filtra los que no alcanzan (p. ej. t + L ≥ 00:30)."""
    start, end = day_bounds(day)
    n = WINDOW_MIN // STEP_MIN
    return [start + timedelta(minutes=STEP_MIN * k) for k in range(n)]


def window_day(t) -> date:
    """Día (ventana) al que pertenece el instante `t`: la fecha de `t − 5 h`.
    Así [d 05:00, d+1 05:00) → d, lo que cubre la ventana simulada
    [d 05:30, d+1 00:30) y la de snapshots [d 05:00, d+1 01:00).

    Usar esto (no `as_date`) cuando `t` es una hora de decisión o de evento:
    `as_date(t)` con t = d+1 00:15 da d+1, que es otra ventana.
    """
    import pandas as pd
    return (pd.Timestamp(t) - timedelta(hours=5)).date()


def as_date(day) -> date:
    """Acepta 'YYYY-MM-DD', date, datetime o Timestamp (toma su fecha de
    calendario; para una hora dentro de la ventana usar `window_day`)."""
    if isinstance(day, str):
        return date.fromisoformat(day[:10])
    if hasattr(day, "date") and callable(day.date):
        return day.date()
    return day
