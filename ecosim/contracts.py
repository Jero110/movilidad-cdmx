"""Contratos de datos de ecosim (plan 2026-09-28-ecosim, secciones 1.3–1.6;
run 2: plan 2026-09-28-ecosim2, subtask fundamentos2).

Cada tabla es un `pandas.DataFrame` con columnas fijas. Las funciones
`validate_*` revisan columnas, tipos y reglas básicas y lanzan `ValueError`
con un mensaje claro; devuelven el mismo DataFrame para poder encadenar.
Se permiten columnas extra (p. ej. `long`, `o_known` en Trips).

Tipos: `str` = columna de texto (object o string); `datetime` = datetime64
*naive* (hora local CDMX); `int`/`float`/`bool` = numéricos de pandas.

Ventana del día d (run 3): [d 05:00, d+1 00:30) (`config.day_bounds`).
"""

from __future__ import annotations

import numbers
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd
import numpy as np
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
    # valor crudo del snapshot y ajuste a las 05:00 exactas (ver data.initial_state)
    "bikes_snap": "int", "docks_snap": "int", "roll_adj": "int", "roll_clipped": "bool",
}

ORDER_SCHEMA = {"issued_at": "datetime", "pickup_at": "datetime", "delivery_at": "datetime", "short_name": "str", "delta": "int"}

# Un par que se deshace se marca con `par` y no cuenta como visita.
ECOBICI_MOVES_SCHEMA = {
    "short_name": "str", "t0": "datetime", "t1": "datetime",
    "delta": "int", "arrivals": "int", "departures": "int", "par": "bool",
}

# Cambios de etiqueta en sitio, en t (= t1 del intervalo).
DAMAGE_EVENTS_SCHEMA = {"short_name": "str", "t": "datetime", "kind": "str", "n": "int"}
DAMAGE_KINDS = ("sube", "baja")

# Estado que ve la política en t (por estación)
STATE_SCHEMA = {"short_name": "str", "bikes": "int", "disabled": "int", "docks": "int", "cap": "int"}

# Métricas por día, todas dentro de la ventana [05:00, 00:30)
DAY_METRICS = (
    "E", "F", "EF", "visitas", "visitas_recoger", "visitas_entregar",
    "bicis_movidas", "en_transito_max", "desvios_salida", "desvios_llegada",
    "km_desvio_medio", "danadas_no_aplicables",
)
# hour: hora local de inicio del tramo, 5..24; 5 = 05:00–06:00, 23 = 23:00–
# 24:00, 24 = 00:00–00:30 del día siguiente (así ordena cronológicamente).
STATION_HOUR_SCHEMA = {"short_name": "str", "hour": "int", "E": "int", "F": "int"}
STATION_HOURS = tuple(range(5, 25))

TRIP_COLUMNS = list(TRIP_SCHEMA)
SNAPSHOT_COLUMNS = list(SNAPSHOT_SCHEMA)
INITIAL_STATE_COLUMNS = list(INITIAL_STATE_SCHEMA)
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


def validate_orders(df: pd.DataFrame) -> pd.DataFrame:
    _validate(df, ORDER_SCHEMA, "Orders")
    if (df["delta"] == 0).any():
        raise ValueError("Orders: delta 0")
    if ((df["pickup_at"] < df["issued_at"]) | (df["delivery_at"] < df["pickup_at"])).any():
        raise ValueError("Orders: tiempos fuera de orden")
    return df


def validate_ecobici_moves(df: pd.DataFrame) -> pd.DataFrame:
    _validate(df, ECOBICI_MOVES_SCHEMA, "EcobiciMoves")
    if (df["t1"] <= df["t0"]).any():
        raise ValueError("EcobiciMoves: t1 <= t0")
    _nonneg(df, ["arrivals", "departures"], "EcobiciMoves")
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
    """Una visita: delta negativo sale en pickup_at; positivo llega en delivery_at."""
    issued_at: datetime
    pickup_at: datetime
    delivery_at: datetime
    short_name: str
    delta: int

    def __post_init__(self):
        if not isinstance(self.delta, numbers.Integral) or isinstance(self.delta, bool):
            raise ValueError(f"Order.delta debe ser entero, llegó {type(self.delta).__name__}")
        object.__setattr__(self, "delta", int(self.delta))
        object.__setattr__(self, "short_name", str(self.short_name))
        if self.delta == 0:
            raise ValueError("Order.delta no puede ser 0")
        if self.pickup_at < self.issued_at or self.delivery_at < self.pickup_at:
            raise ValueError("Order: tiempos fuera de orden")


def orders_to_frame(orders: list[Order]) -> pd.DataFrame:
    df = pd.DataFrame(
        [(o.issued_at, o.pickup_at, o.delivery_at, o.short_name, o.delta) for o in orders],
        columns=ORDER_COLUMNS,
    )
    df["issued_at"] = pd.to_datetime(df["issued_at"])
    df["pickup_at"] = pd.to_datetime(df["pickup_at"])
    df["delivery_at"] = pd.to_datetime(df["delivery_at"])
    df["short_name"] = df["short_name"].astype(str)
    df["delta"] = df["delta"].astype("int64")
    return df


def frame_to_orders(df: pd.DataFrame) -> list[Order]:
    validate_orders(df)
    return [
        Order(r.issued_at.to_pydatetime(), r.pickup_at.to_pydatetime(),
              r.delivery_at.to_pydatetime(), str(r.short_name), int(r.delta))
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
    (`DamageEvents`): el estado trae las de t, no las de las 05:00.
    warehouse: bodega neta acumulada aplicada del día (A − R hasta t).
    """
    t: datetime
    stations: pd.DataFrame
    warehouse: int = 0


def _is_int(x) -> bool:
    return isinstance(x, numbers.Integral) and not isinstance(x, bool)


@dataclass
class PolicyParams:
    n_hours: int = 2
    lam: float = 0.0
    visits_per_decision: int = C.VISITS_PER_DECISION
    max_bikes_per_visit: int = C.MAX_BIKES_PER_VISIT
    pickup_min: int = C.PICKUP_MIN
    delivery_min: int = C.DELIVERY_MIN
    retiro: float = 0.0

    def __post_init__(self):
        for key in ("n_hours", "visits_per_decision", "max_bikes_per_visit", "pickup_min", "delivery_min"):
            value = getattr(self, key)
            if not _is_int(value) or value < 1:
                raise ValueError(f"PolicyParams.{key} debe ser entero ≥ 1, llegó {value!r}")
            setattr(self, key, int(value))
        if self.delivery_min <= self.pickup_min:
            raise ValueError("PolicyParams.delivery_min debe superar pickup_min")
        if self.lam < 0 or self.retiro < 0:
            raise ValueError("PolicyParams: lam y retiro deben ser no negativos")


@runtime_checkable
class ForecastTable(Protocol):
    """Pronóstico en cuartos de hora desde t: [estación, 4·n_hours, salida/llegada]."""

    forma: str  # "diaria" o "directa"
    modelo: str

    def table(self, t: datetime, n_hours: int) -> np.ndarray: ...


def validate_forecast_table(forecast: ForecastTable, t: datetime,
                            n_hours: int, n_stations: int) -> np.ndarray:
    """Comprueba metadatos y la tabla que recibirá el asignador."""
    if forecast.forma not in ("diaria", "directa") or not isinstance(forecast.modelo, str):
        raise ValueError("ForecastTable: forma o modelo inválido")
    if not _is_int(n_hours) or n_hours < 1:
        raise ValueError("ForecastTable: n_hours debe ser entero ≥ 1")
    table = forecast.table(t, n_hours)
    if not isinstance(table, np.ndarray) or table.shape != (n_stations, 4 * n_hours, 2):
        raise ValueError(f"ForecastTable: se esperaba {(n_stations, 4 * n_hours, 2)}")
    if not np.issubdtype(table.dtype, np.number) or not np.isfinite(table).all() or (table < 0).any():
        raise ValueError("ForecastTable: conteos inválidos")
    return table


@runtime_checkable
class Policy(Protocol):
    def decide(
        self,
        t: datetime,
        state: SimState,
        pending_orders: list[Order],
        forecast: ForecastTable | None,
        params: PolicyParams,
    ) -> list[Order]:
        """Órdenes a emitir en t."""
        ...


# ----------------------------------------------------------------------------
# Resultado de un día
# ----------------------------------------------------------------------------

@dataclass
class DayResult:
    """Resultado de una ventana y un brazo, con esfuerzo por visitas."""
    day: str
    arm: str
    E: int
    F: int
    EF: int
    visitas: int
    visitas_recoger: int
    visitas_entregar: int
    bicis_movidas: int
    en_transito_max: int
    recortes: dict[str, int]
    desvios_salida: int
    desvios_llegada: int
    km_desvio_medio: float
    danadas_no_aplicables: int
    tiempos_decision: list[float]
    station_hour: pd.DataFrame | None = None
    # Diagnósticos del simulador fuera de las métricas del contrato (p. ej.
    # `replay_neto`, flujos externos, series por minuto). Opcional.
    extra: dict = field(default_factory=dict)

    @property
    def metrics(self) -> dict:
        return {k: getattr(self, k) for k in DAY_METRICS} | {"recortes": self.recortes}


def validate_day_result(r: DayResult) -> DayResult:
    if not isinstance(r, DayResult):
        raise ValueError(f"DayResult: llegó {type(r).__name__}")
    for k in DAY_METRICS:
        v = getattr(r, k)
        if k == "km_desvio_medio":
            if not np.isfinite(v) or v < 0:
                raise ValueError("DayResult.km_desvio_medio inválido")
        elif not _is_int(v) or v < 0:
            raise ValueError(f"DayResult.{k}: debe ser entero ≥ 0")
    if r.EF != r.E + r.F or r.visitas != r.visitas_recoger + r.visitas_entregar:
        raise ValueError("DayResult: EF o visitas no cuadran")
    if any(not _is_int(v) or v < 0 for v in r.recortes.values()):
        raise ValueError("DayResult.recortes: valores inválidos")
    if r.station_hour is not None:
        _validate(r.station_hour, STATION_HOUR_SCHEMA, "DayResult.station_hour")
        sh = r.station_hour
        if not sh["hour"].isin(STATION_HOURS).all():
            raise ValueError("DayResult.station_hour.hour fuera de 5..24")
        if int(sh["E"].sum()) != r.E or int(sh["F"].sum()) != r.F:
            raise ValueError("DayResult: la tabla estación × hora no suma E/F")
    return r
