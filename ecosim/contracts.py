"""Contratos de datos de ecosim (plan 2026-09-28-ecosim, secciones 1.3–1.6;
run 2: plan 2026-09-28-ecosim2, subtask fundamentos2).

Cada tabla es un `pandas.DataFrame` con columnas fijas. Las funciones
`validate_*` revisan columnas, tipos y reglas básicas y lanzan `ValueError`
con un mensaje claro; devuelven el mismo DataFrame para poder encadenar.
Se permiten columnas extra (p. ej. `long`, `o_known` en Trips).

Tipos: `str` = columna de texto (object o string); `datetime` = datetime64
*naive* (hora local CDMX); `int`/`float`/`bool` = numéricos de pandas.

Ventana del día d (run 2): [d 05:30, d+1 00:30) (`config.day_bounds`).
"""

from __future__ import annotations

import numbers
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd
from pandas.api import types as pt

from ecosim import config as C

# ----------------------------------------------------------------------------
# Esquemas
# ----------------------------------------------------------------------------

TRIP_SCHEMA = {"bike": "str", "o": "str", "d": "str", "t_dep": "datetime", "t_arr": "datetime"}

SNAPSHOT_SCHEMA = {
    "short_name": "str", "t": "datetime",
    "bikes": "int", "disabled": "int", "docks": "int", "docks_disabled": "int", "cap": "int",
    "is_renting": "bool", "is_returning": "bool",
}

INITIAL_STATE_SCHEMA = {
    "short_name": "str", "t_snap": "datetime", "offset_min": "float", "stale": "bool",
    "bikes": "int", "disabled": "int", "docks": "int", "docks_disabled": "int", "cap": "int",
    "is_renting": "bool", "is_returning": "bool", "is_installed": "bool", "blank": "bool",
    "out_of_service": "bool",
    # valor crudo del snapshot y ajuste a las 05:30 exactas (ver data.initial_state)
    "bikes_snap": "int", "docks_snap": "int", "roll_adj": "int", "roll_clipped": "bool",
}

# Run 2: varias emisiones por día (una por `issued_at`).
#   refresh_min: cada cuánto se re-emite la serie a la que pertenece la
#     emisión (15, 60, 180; 0 = una sola emisión en el día, p. ej. `daily`).
#   horizon_h: h máximo (horas) que la emisión soporta: cubre
#     [issued_at, issued_at + refresh_min + L + horizon_h], recortado a 00:30.
#     `daily` cubre todo el día.
FORECAST_SCHEMA = {
    "issued_at": "datetime", "variant": "str", "short_name": "str",
    "block_start": "datetime", "block_min": "int",
    "departures": "float", "arrivals": "float",
    "dep_lo": "float", "dep_hi": "float", "arr_lo": "float", "arr_hi": "float",
    "refresh_min": "int", "horizon_h": "int",
}
FORECAST_VARIANTS = ("oracle", "ma", "model", "daily")
FORECAST_OPTIONAL = ("dep_lo", "dep_hi", "arr_lo", "arr_hi")   # pueden ser nulas

ORDER_SCHEMA = {"issued_at": "datetime", "effective_at": "datetime", "short_name": "str", "delta": "int"}

# Run 2 (ver "Descomposición de dañadas" del plan ecosim2):
#   taller_retiro: dañadas que Ecobici se lleva en el intervalo (≥ 0);
#   delta_rebal = delta + taller_retiro: el rebalanceo sin el taller;
#   undo: el movimiento es parte de un par ±1 que se deshace (ruido).
ECOBICI_MOVES_SCHEMA = {
    "short_name": "str", "t0": "datetime", "t1": "datetime",
    "delta": "int", "arrivals": "int", "departures": "int",
    "delta_rebal": "int", "taller_retiro": "int", "undo": "bool",
}
# Opcionales: si la columna está, se valida su tipo y que no tenga nulos.
#   delta_avail: delta calculado solo con disponibles (replay `avail` del simulador).
ECOBICI_MOVES_OPTIONAL = {"delta_avail": "int"}

# Eventos exógenos de dañadas por estación, en t (= t1 del intervalo).
#   daño: una disponible pasa a dañada; reparacion: una dañada vuelve a estar
#   disponible; taller_retiro: Ecobici se lleva dañadas (salen del sistema).
DAMAGE_EVENTS_SCHEMA = {"short_name": "str", "t": "datetime", "kind": "str", "n": "int"}
DAMAGE_KINDS = ("daño", "reparacion", "taller_retiro")

# Estado que ve la política en t (por estación)
STATE_SCHEMA = {"short_name": "str", "bikes": "int", "disabled": "int", "docks": "int", "cap": "int"}

# Métricas por día (1.4), todas dentro de la ventana [05:30, 00:30)
DAY_METRICS = [
    "E",                  # minutos-estación con 0 bicis disponibles
    "F",                  # minutos-estación con 0 anclajes libres
    "trips_served",
    "detours_dep", "detours_arr",
    "walk_m_dep", "walk_m_arr",
    "moves",              # pares (estación, orden aplicada) con delta ≠ 0
    "stations_touched",
    "A",                  # bicis metidas (suma de deltas > 0 aplicados)
    "R",                  # bicis sacadas (suma de |deltas < 0| aplicados)
    "rebalanced",         # min(A, R)
    "warehouse",          # A − R (positivo = entraron al sistema)
    "clipped",            # bicis de órdenes que no se pudieron aplicar
    "arrivals_unknown",   # llegadas a estaciones desconocidas: bicis que salen del sistema
    # run 2 (conteos en bicis, ≥ 0)
    "recorte_bodega",           # bicis de órdenes recortadas por el tope de bodega (aplicado)
    "recorte_por_movimiento",   # bicis recortadas por |delta| > max_bikes_per_move
    "danos_aplicados",          # bicis de eventos `daño` aplicados
    "danos_no_aplicables",      # bicis de eventos `daño` sin disponible que dañar
    "taller_aplicado",          # bicis de `taller_retiro` aplicadas
    "taller_no_aplicable",      # bicis de `taller_retiro` sin dañada que retirar
]
RUN2_METRICS = DAY_METRICS[DAY_METRICS.index("recorte_bodega"):]
# hour: hora local de inicio del tramo, 5..24; 5 = 05:30–06:00, 23 = 23:00–
# 24:00, 24 = 00:00–00:30 del día siguiente (así ordena cronológicamente).
STATION_HOUR_SCHEMA = {"short_name": "str", "hour": "int", "E": "int", "F": "int"}
STATION_HOURS = tuple(range(5, 25))

TRIP_COLUMNS = list(TRIP_SCHEMA)
SNAPSHOT_COLUMNS = list(SNAPSHOT_SCHEMA)
INITIAL_STATE_COLUMNS = list(INITIAL_STATE_SCHEMA)
FORECAST_COLUMNS = list(FORECAST_SCHEMA)
ORDER_COLUMNS = list(ORDER_SCHEMA)
ECOBICI_MOVES_COLUMNS = list(ECOBICI_MOVES_SCHEMA)
DAMAGE_EVENTS_COLUMNS = list(DAMAGE_EVENTS_SCHEMA)
STATE_COLUMNS = list(STATE_SCHEMA)
STATION_HOUR_COLUMNS = list(STATION_HOUR_SCHEMA)


# ----------------------------------------------------------------------------
# Validación genérica
# ----------------------------------------------------------------------------

_CHECK = {
    "str": lambda s: pt.is_string_dtype(s) or pt.is_object_dtype(s),
    "datetime": lambda s: pt.is_datetime64_dtype(s) and getattr(s.dt, "tz", None) is None,
    "int": pt.is_integer_dtype,
    "float": lambda s: pt.is_float_dtype(s) or pt.is_integer_dtype(s),
    "bool": pt.is_bool_dtype,
}


def _validate(df: pd.DataFrame, schema: dict, name: str, nullable=()) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        raise ValueError(f"{name}: se esperaba DataFrame, llegó {type(df).__name__}")
    missing = [c for c in schema if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: faltan columnas {missing}")
    for col, kind in schema.items():
        s = df[col]
        if col in nullable and s.isna().all():
            continue
        if not _CHECK[kind](s):
            raise ValueError(f"{name}.{col}: se esperaba {kind}, tiene {s.dtype}")
        if col not in nullable and s.isna().any():
            raise ValueError(f"{name}.{col}: tiene nulos")
    return df


def _nonneg(df, cols, name):
    for c in cols:
        if (df[c] < 0).any():
            raise ValueError(f"{name}.{c}: valores negativos")


# ----------------------------------------------------------------------------
# Validadores por tabla
# ----------------------------------------------------------------------------

def validate_trips(df: pd.DataFrame) -> pd.DataFrame:
    _validate(df, TRIP_SCHEMA, "Trips")
    if (df["t_arr"] < df["t_dep"]).any():
        raise ValueError("Trips: t_arr < t_dep")
    return df


def validate_snapshots(df: pd.DataFrame) -> pd.DataFrame:
    _validate(df, SNAPSHOT_SCHEMA, "Snapshots")
    _nonneg(df, ["bikes", "disabled", "docks", "docks_disabled", "cap"], "Snapshots")
    if df.duplicated(["short_name", "t"]).any():
        raise ValueError("Snapshots: (short_name, t) duplicado")
    return df


def validate_initial_state(df: pd.DataFrame) -> pd.DataFrame:
    _validate(df, INITIAL_STATE_SCHEMA, "InitialState")
    _nonneg(df, ["bikes", "disabled", "docks", "docks_disabled", "cap"], "InitialState")
    if df["short_name"].duplicated().any():
        raise ValueError("InitialState: short_name duplicado")
    return df


FORECAST_KEY = ["issued_at", "variant", "refresh_min", "short_name", "block_start", "block_min"]


def _window_start(ts: pd.Series) -> pd.Series:
    """05:30 del día (ventana) de cada instante: fecha de (t − 5 h) + 05:30
    (ver `config.window_day`)."""
    return (ts - pd.Timedelta(hours=5)).dt.normalize() + pd.Timedelta(hours=5, minutes=30)


def validate_forecast(df: pd.DataFrame) -> pd.DataFrame:
    """Pronóstico con una o varias emisiones (`issued_at`) de un día.

    Reglas: variante válida; bloques de 30 o 60 min anclados a las 05:30 y
    dentro de [05:30, 00:30); `issued_at` dentro de la ventana del mismo día
    que sus bloques ([05:30, 00:30)); `refresh_min` ≥ 0 y `horizon_h` ≥ 1;
    llave `FORECAST_KEY` sin duplicados.
    """
    _validate(df, FORECAST_SCHEMA, "Forecast", nullable=FORECAST_OPTIONAL)
    bad = set(df["variant"].unique()) - set(FORECAST_VARIANTS)
    if bad:
        raise ValueError(f"Forecast.variant: valores no válidos {sorted(bad)}")
    if not df["block_min"].isin([30, 60]).all():
        raise ValueError("Forecast.block_min: solo 30 o 60")
    _nonneg(df, ["departures", "arrivals"], "Forecast")
    if (df["refresh_min"] < 0).any():
        raise ValueError("Forecast.refresh_min: negativo")
    if (df["horizon_h"] < 1).any():
        raise ValueError("Forecast.horizon_h: debe ser ≥ 1")
    # bloques anclados a las 05:30 del día y dentro de [05:30, 00:30)
    day0 = _window_start(df["block_start"])
    rel = (df["block_start"] - day0) / pd.Timedelta(minutes=1)
    if ((rel < 0) | (rel + df["block_min"] > C.WINDOW_MIN) | (rel % df["block_min"] != 0)).any():
        raise ValueError("Forecast.block_start: bloques no anclados a 05:30 o fuera de [05:30, 00:30)")
    rel_i = (df["issued_at"] - day0) / pd.Timedelta(minutes=1)
    if ((rel_i < 0) | (rel_i >= C.WINDOW_MIN)).any():
        raise ValueError("Forecast.issued_at: fuera de la ventana [05:30, 00:30) del día de sus bloques")
    if df.duplicated(FORECAST_KEY).any():
        raise ValueError(f"Forecast: llave ({', '.join(FORECAST_KEY)}) duplicada")
    return df


def validate_orders(df: pd.DataFrame) -> pd.DataFrame:
    _validate(df, ORDER_SCHEMA, "Orders")
    if (df["delta"] == 0).any():
        raise ValueError("Orders: delta 0")
    if (df["effective_at"] < df["issued_at"]).any():
        raise ValueError("Orders: effective_at < issued_at")
    return df


def validate_ecobici_moves(df: pd.DataFrame) -> pd.DataFrame:
    _validate(df, ECOBICI_MOVES_SCHEMA, "EcobiciMoves")
    present = {c: k for c, k in ECOBICI_MOVES_OPTIONAL.items() if c in df.columns}
    _validate(df, present, "EcobiciMoves")
    if (df["t1"] <= df["t0"]).any():
        raise ValueError("EcobiciMoves: t1 <= t0")
    _nonneg(df, ["arrivals", "departures", "taller_retiro"], "EcobiciMoves")
    if (df["delta_rebal"] != df["delta"] + df["taller_retiro"]).any():
        raise ValueError("EcobiciMoves: delta_rebal != delta + taller_retiro")
    return df


def validate_damage_events(df: pd.DataFrame) -> pd.DataFrame:
    """Eventos de dañadas: `kind` ∈ `DAMAGE_KINDS`, `n` entero > 0, sin
    duplicados de (short_name, t, kind)."""
    _validate(df, DAMAGE_EVENTS_SCHEMA, "DamageEvents")
    bad = set(df["kind"].unique()) - set(DAMAGE_KINDS)
    if bad:
        raise ValueError(f"DamageEvents.kind: valores no válidos {sorted(bad)}")
    if (df["n"] <= 0).any():
        raise ValueError("DamageEvents.n: debe ser > 0")
    if df.duplicated(["short_name", "t", "kind"]).any():
        raise ValueError("DamageEvents: (short_name, t, kind) duplicado")
    return df


def validate_state(df: pd.DataFrame) -> pd.DataFrame:
    _validate(df, STATE_SCHEMA, "State")
    _nonneg(df, ["bikes", "disabled", "docks", "cap"], "State")
    return df


# ----------------------------------------------------------------------------
# Órdenes
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Order:
    """Orden de rebalanceo: en `effective_at` sumar `delta` bicis disponibles
    a la estación (`delta > 0` pone, `delta < 0` quita). Entero, nunca 0."""
    issued_at: datetime
    effective_at: datetime
    short_name: str
    delta: int

    def __post_init__(self):
        if not isinstance(self.delta, numbers.Integral) or isinstance(self.delta, bool):
            raise ValueError(f"Order.delta debe ser entero, llegó {type(self.delta).__name__}")
        object.__setattr__(self, "delta", int(self.delta))
        object.__setattr__(self, "short_name", str(self.short_name))
        if self.delta == 0:
            raise ValueError("Order.delta no puede ser 0")
        if self.effective_at < self.issued_at:
            raise ValueError("Order: effective_at < issued_at")


def orders_to_frame(orders: list[Order]) -> pd.DataFrame:
    df = pd.DataFrame(
        [(o.issued_at, o.effective_at, o.short_name, o.delta) for o in orders],
        columns=ORDER_COLUMNS,
    )
    df["issued_at"] = pd.to_datetime(df["issued_at"])
    df["effective_at"] = pd.to_datetime(df["effective_at"])
    df["short_name"] = df["short_name"].astype(str)
    df["delta"] = df["delta"].astype("int64")
    return df


def frame_to_orders(df: pd.DataFrame) -> list[Order]:
    validate_orders(df)
    return [
        Order(r.issued_at.to_pydatetime(), r.effective_at.to_pydatetime(), str(r.short_name), int(r.delta))
        for r in df.itertuples(index=False)
    ]


# ----------------------------------------------------------------------------
# Estado, parámetros y política
# ----------------------------------------------------------------------------

@dataclass
class SimState:
    """Lo que ve la política en t (como el feed en vivo).

    stations: DataFrame con `STATE_COLUMNS` (bicis disponibles, dañadas,
    anclajes libres, capacidad por estación). En el run 2 las dañadas
    (`disabled`) cambian durante el día por los eventos exógenos
    (`DamageEvents`): el estado trae las de t, no las de las 05:30.
    warehouse: bodega neta acumulada aplicada del día (A − R hasta t).
    """
    t: datetime
    stations: pd.DataFrame
    warehouse: int = 0


def _is_int(x) -> bool:
    return isinstance(x, numbers.Integral) and not isinstance(x, bool)


@dataclass
class PolicyParams:
    lead_min: int = 60
    H_horas: int = 2          # entero ≥ 1, sin tope (run 2: h = 1, 2, 3, … hasta que deje de mejorar)
    lam: float = 0.0
    mu: float = 1.0
    tope_hora: int = 60       # placeholder hasta que `medicion` dé el p95
    tope_bodega: int = 200    # placeholder hasta que `medicion` dé el promedio |A − R|
    block_min: int = 60
    max_bikes_per_move: int = C.MAX_BIKES_PER_MOVE   # |delta| máximo por orden
    refresh_min: int = 60     # emisiones de pronóstico a usar (0 = una sola, `daily`)
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if not _is_int(self.H_horas) or self.H_horas < 1:
            raise ValueError(f"PolicyParams.H_horas debe ser entero ≥ 1, llegó {self.H_horas!r}")
        if not _is_int(self.max_bikes_per_move) or self.max_bikes_per_move < 1:
            raise ValueError(f"PolicyParams.max_bikes_per_move debe ser entero ≥ 1, llegó {self.max_bikes_per_move!r}")
        if not _is_int(self.refresh_min) or self.refresh_min < 0:
            raise ValueError(f"PolicyParams.refresh_min debe ser entero ≥ 0, llegó {self.refresh_min!r}")
        self.H_horas = int(self.H_horas)
        self.max_bikes_per_move = int(self.max_bikes_per_move)
        self.refresh_min = int(self.refresh_min)


@runtime_checkable
class Policy(Protocol):
    def decide(
        self,
        t: datetime,
        state: SimState,
        pending_orders: list[Order],
        forecast: pd.DataFrame | None,
        params: PolicyParams,
    ) -> list[Order]:
        """Órdenes a emitir en t (`issued_at = t`, `effective_at = t + L`)."""
        ...


# ----------------------------------------------------------------------------
# Resultado de un día
# ----------------------------------------------------------------------------

@dataclass
class DayResult:
    """Resultado del simulador para un día y un brazo.

    metrics: dict con (al menos) las llaves `DAY_METRICS`.
    station_hour: DataFrame `STATION_HOUR_COLUMNS` (E y F en minutos por
    estación y hora 5..24; la 5 cubre 05:30–06:00 y la 24 cubre 00:00–00:30
    del día siguiente, ver `STATION_HOURS`).
    extra: cualquier cosa adicional del simulador (log de desvíos, recortes…).
    """
    day: str
    arm: str
    metrics: dict
    station_hour: pd.DataFrame
    extra: dict = field(default_factory=dict)


def validate_day_result(r: DayResult) -> DayResult:
    if not isinstance(r, DayResult):
        raise ValueError(f"DayResult: llegó {type(r).__name__}")
    missing = [k for k in DAY_METRICS if k not in r.metrics]
    if missing:
        raise ValueError(f"DayResult.metrics: faltan {missing}")
    _validate(r.station_hour, STATION_HOUR_SCHEMA, "DayResult.station_hour")
    sh = r.station_hour
    if not sh["hour"].isin(STATION_HOURS).all():
        raise ValueError("DayResult.station_hour.hour fuera de 5..24")
    if int(sh["E"].sum()) != int(r.metrics["E"]) or int(sh["F"].sum()) != int(r.metrics["F"]):
        raise ValueError("DayResult: la tabla estación × hora no suma E/F")
    if r.metrics["rebalanced"] != min(r.metrics["A"], r.metrics["R"]):
        raise ValueError("DayResult: rebalanced != min(A, R)")
    if r.metrics["warehouse"] != r.metrics["A"] - r.metrics["R"]:
        raise ValueError("DayResult: warehouse != A − R")
    for k in RUN2_METRICS:
        v = r.metrics[k]
        if not _is_int(v) or v < 0:
            raise ValueError(f"DayResult.metrics[{k!r}]: debe ser entero ≥ 0, llegó {v!r}")
    return r
