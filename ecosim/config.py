"""Constantes compartidas de ecosim (plan 2026-09-28-ecosim, sección 1.1;
run 2: plan 2026-09-28-ecosim2, subtask fundamentos2).

Todos los tiempos internos son hora local de la Ciudad de México, *naive*
(sin tz). La conversión desde UTC se hace una sola vez, en `ecosim.data`.

El día `d` es la ventana [d 05:00, d+1 00:30) (run 3).
"""

from __future__ import annotations

import os
import calendar
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
COVERAGE_AUDIT = DERIVED / "snapshot_coverage_run3.json"
TRIPS_PARQUET = DERIVED / "trips_2024_01_2026_08.parquet"
TRIPS_AUDIT = DERIVED / "trips_2024_01_2026_08_audit.json"
DAYS_JSON = Path(__file__).resolve().parent / "days.json"

RAW_TRIP_FILES = ([f"2024-{m:02d}.csv" for m in range(1, 13)]
                  + [f"2025-{m:02d}.csv" for m in range(1, 10)]
                  + ["2025-10-1.csv", "2025-11.csv", "2025-12.csv"]
                  + [f"2026-{m:02d}.csv" for m in range(1, 6)]
                  + ["public_data_web_2026-06.csv", "public_data_web_2026-07.csv",
                     "public_data_web_2026-08_2.csv"])
TRIPS_2026_MARCH_LAST_COMPLETE = date(2026, 3, 22)

# --- GBFS (bucket público) -----------------------------------------------------
GBFS_BUCKET = "s3://bike-sharing-history/mexico-city/ecobici"
GBFS_S3_ENDPOINT = "storage.googleapis.com"
# Archivos mensuales, particionados por mes UTC. Una ventana local puede
# requerir el archivo del mes UTC siguiente. Sep-2026 aún no está publicado:
# la última ventana completa es 2026-08-30.
GBFS_MONTHS = [(y, calendar.month_abbr[m]) for y, months in
               ((2025, range(1, 13)), (2026, range(1, 10))) for m in months]
SNAPSHOT_START = date(2025, 1, 1)
SNAPSHOT_END = date(2026, 8, 30)  # 08-31 requiere Sep.parquet, aún ausente
# [inicio, fin) local; el fin es del día SIGUIENTE (05:00 del d → 01:00 del d+1).
SNAPSHOT_WINDOW = (time(5, 0), time(1, 0))
SNAPSHOT_WINDOW_MIN = 20 * 60
SHORT_NAME_RE = r"\d{3}(-\d{3})?"           # "038", "271-272"; lo demás es de prueba
# Medido (FUNDAMENTOS.md): el estado del feed es ~30 s anterior a
# `committed_at`. `data.snapshots` NO lo aplica (su `t` es el commit crudo);
# sí lo aplican `medicion` y `data.initial_state` (al llevar el stock a 05:00).
GBFS_COMMIT_LAG_S = 30

# --- tiempo --------------------------------------------------------------------
TZ = "America/Mexico_City"   # UTC−6 fijo, sin horario de verano desde oct-2022
UTC_OFFSET_HOURS = -6

# Ventana simulada [05:00, 00:30 del día siguiente).
DAY_START = time(5, 0)
DAY_END = time(0, 30)                       # del día SIGUIENTE
WINDOW_MIN = 1170

# Paso de decisión: cada 15 min (cadencia del feed en vivo)
STEP_MIN = 15

PICKUP_MIN = 15
DELIVERY_MIN = 60
DELIVERY_SENS_MIN = (45, 60, 75)

VISITS_PER_DECISION = 67
MAX_BIKES_PER_VISIT = 14
VISITS_PER_DECISION_SENS = 83
MAX_BIKES_PER_VISIT_SENS = 24
N_GRID = (1, 2, 3, 4, 5, 6)
LAMBDA_GRID_N = (15, 30, 60)
LAMBDA_GRID = (10, 15, 20, 30, 45, 60)

# Radio de desvío para salidas en estación vacía
DETOUR_RADIUS_M = 500

# Estado inicial: snapshot con desfase mayor a esto respecto a 05:00 se marca
INITIAL_STALE_MIN = 10
INITIAL_OPEN_MAX = time(6, 0)  # primera foto abierta debe ser anterior a 06:00
INITIAL_OPEN_RENTING_MIN = 0.95

# --- días ------------------------------------------------------------------------
# Los 15 días de evaluación del run 1 (`ecosim/days.json` en el tag
# `ecosim-run1`); el run 2 los conserva si cumplen la cobertura nueva.
RUN1_EVAL_DAYS = (
    "2025-09-03", "2025-09-15", "2025-09-26", "2025-09-28", "2025-10-07",
    "2025-10-11", "2025-10-13", "2025-10-15", "2025-10-22", "2025-10-25",
    "2025-11-07", "2025-11-12", "2025-11-15", "2025-11-19", "2025-11-24",
)
# 15 días de selección en agosto de 2025.
SELECTION_SEED = 20261001
N_SEL_WEEKDAY = 11
N_SEL_WEEKEND = 4
# Cobertura: fracción de 20 bloques en [05:00, 00:30) con ≥1 snapshot.
COVERAGE_BLOCK_MIN = 60
COVERAGE_MIN = 0.90

# Entrenamiento de modelos
TRAIN_START = date(2025, 1, 1)
TRAIN_END = date(2025, 8, 31)               # inclusive

CURVE_SEED = 20261002


def _month_offset(year: int, month: int, offset: int) -> date:
    k = year * 12 + month - 1 + offset
    return date(k // 12, k % 12 + 1, 1)


FOLDS = (
    {"name": "elegir", "train_start": date(2025, 1, 1), "train_end": date(2025, 7, 31), "test_month": "2025-08"},
    *({"name": f"prueba_{i+1}", "train_start": _month_offset(2025, 1, i),
       "train_end": _month_offset(2025, 9, i) - timedelta(days=1),
       "test_month": _month_offset(2025, 9, i).strftime("%Y-%m")} for i in range(5)),
    *({"name": f"prod_2026_{m:02d}",
       "train_start": _month_offset(2026, m, -8),
       "train_end": date(2026, m, 1) - timedelta(days=1),
       "test_month": f"2026-{m:02d}"} for m in range(2, 9)),
)

# --- geografía -------------------------------------------------------------------
# Centro aproximado del sistema; estaciones a más de esto se marcan inválidas.
CDMX_CENTER = (19.40, -99.17)               # (lat, lon)
MAX_KM_FROM_CENTER = 50.0
EARTH_RADIUS_M = 6_371_000.0


def day_bounds(day) -> tuple:
    """(inicio, fin) de la ventana simulada del día, como datetime naive:
    (d 05:00, d+1 00:30)."""
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
    """05:00, 05:15, …, 00:15 (del día siguiente): todos los pasos de la
    ventana. Quien decide filtra los que no alcanzan (p. ej. t + L ≥ 00:30)."""
    start, end = day_bounds(day)
    n = WINDOW_MIN // STEP_MIN
    return [start + timedelta(minutes=STEP_MIN * k) for k in range(n)]


def window_day(t) -> date:
    """Día (ventana) al que pertenece el instante `t`: la fecha de `t − 5 h`.
    Así [d 05:00, d+1 05:00) → d, lo que cubre la ventana simulada
    [d 05:00, d+1 00:30) y la de snapshots [d 05:00, d+1 01:00).

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
