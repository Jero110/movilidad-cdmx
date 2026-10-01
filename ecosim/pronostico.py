"""Pronósticos de salidas/llegadas por estación y bloque de 60 min (run 2,
plan 2026-09-28-ecosim2, subtask pronostico2).

`uv run python -m ecosim.pronostico` escribe, para cada día de evaluación y de
selección de `days.json`:

* `forecasts/{day}_daily.parquet`: UNA emisión a las 05:30 que cubre 05:30–00:30
  (`ma` de 4 semanas, sin corrección intradía; `refresh_min = 0`).
* `forecasts/{day}_{oracle|ma|model}_f{f}.parquet`, f ∈ {15, 60, 180} min:
  emisiones en 05:30, 05:30+f, … (< 00:30). La emisión de la hora s cubre los
  bloques que tocan [s, s + f + L + h_max] (L = 60 min, h_max = 6 h, recortado
  a 00:30). El bloque que contiene a s vale lo ya observado en él + el resto
  esperado corregido (total esperado del bloque, no solo el resto).
* `ecosim/results/pronostico/accuracy.csv`.

Todo se calcula sobre conteos `C[día, estación, bin15, {dep, arr}]` (76 bins de
15 min en [05:30, 00:30)). Un bin/bloque pertenece a la ventana del día
`window_day(t)`: los viajes de 00:00–00:30 son del día anterior.

Corrección intradía (`ma` y `model`): la emisión en s multiplica lo que falta
por, para cada estación y por separado salidas y llegadas,

    factor = clip((observado + K) / (pronosticado + K), 0.5, 2)

con lo observado y lo pronosticado sobre [05:30, s) del mismo día. K = 5 se
fijó ANTES de ver resultados y no se tunea. Causalidad: la emisión en s solo lee
`C[:i]` (días anteriores) y `C[i, :, :s]` (bins con fin ≤ s del propio día).
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

from ecosim import config as C
from ecosim import contracts as K
from ecosim import data
from ecosim.days import day_type

OUT_FORECASTS = C.DERIVED / "forecasts"
OUT_RESULTS = C.REPO_ROOT / "ecosim" / "results" / "pronostico"
BIN_MIN = 15
NB15 = C.WINDOW_MIN // BIN_MIN          # 76 bins de 15 min
N60 = C.WINDOW_MIN // 60                # 19 bloques de 60 min
PER = 60 // BIN_MIN                     # 4 bins por bloque
DEP, ARR = 0, 1
NUM_THREADS = 4

REFRESH_MIN = list(C.FORECAST_REFRESH_MIN)   # f ∈ {15, 60, 180}
LEAD_MIN = C.LEAD_MIN                        # L = 60
H_MAX_H = 6                                  # h_max del asignador
K_SHRINK = 5.0                               # encogimiento, fijado a priori
FACTOR_CLIP = (0.5, 2.0)
DAILY_HORIZON_H = N60                        # `daily` cubre las 19 h

# Hiperparámetros del run 1, SIN tunear.
LGB_PARAMS = dict(
    objective="poisson", n_estimators=200, learning_rate=0.05, num_leaves=31,
    min_child_samples=50, subsample=0.8, subsample_freq=1, colsample_bytree=0.9,
    random_state=0, deterministic=True, force_row_wise=True,
    n_jobs=NUM_THREADS, verbose=-1,
)


# ============================================================================
# Conteos
# ============================================================================

def count_trips(trips: pd.DataFrame, universe: list[str], days: list[date],
                dep_known: np.ndarray | None = None) -> np.ndarray:
    """Conteos reales `[día, estación, bin15, {dep, arr}]` a partir de viajes.

    `days` debe ser contiguo. Salida: `t_dep` en la ventana de su día
    (`window_day`) y `o` en el universo. Llegada: `t_arr` en la ventana de su
    propio día y `d` en el universo (una llegada a una estación desconocida no
    entra: sale del sistema). Aplica igual a un día (oracle) que a la historia.
    `dep_known` (opcional, bool por viaje): las salidas con False se ignoran (regla
    del simulador: salida desde estación desconocida se ignora); las llegadas de
    esos viajes SÍ cuentan.
    """
    d0 = pd.Timestamp(days[0])
    assert all(days[k + 1] - days[k] == timedelta(days=1) for k in range(len(days) - 1)), "days no contiguo"
    sidx = pd.Index(universe)
    out = np.zeros((len(days), len(universe), NB15, 2), dtype=np.float32)
    for col, scol, kind in (("t_dep", "o", DEP), ("t_arr", "d", ARR)):
        t = trips[col]
        day = (t - pd.Timedelta(hours=5)).dt.normalize()          # window_day, vectorizado
        off = ((t - day) / pd.Timedelta(minutes=1)).to_numpy() - (C.DAY_START.hour * 60 + C.DAY_START.minute)
        di = ((day - d0) / pd.Timedelta(days=1)).to_numpy()
        si = sidx.get_indexer(trips[scol])
        ok = (off >= 0) & (off < C.WINDOW_MIN) & (di >= 0) & (di < len(days)) & (si >= 0)
        if kind == DEP and dep_known is not None:
            ok &= np.asarray(dep_known, bool)
        np.add.at(out, (di[ok].astype(int), si[ok], (off[ok] // BIN_MIN).astype(int), kind), 1)
    return out


def load_history(universe: list[str], start: date, end: date) -> tuple[list[date], np.ndarray]:
    """Conteos de todos los días de [start, end] (por mes, para cuidar memoria).

    Lee viajes hasta el 00:30 del día siguiente a `end` (la cola de la última
    ventana), sin pasar del sello de diciembre.
    """
    days, d = [], start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    counts = np.zeros((len(days), len(universe), NB15, 2), dtype=np.float32)
    last = min(end + timedelta(days=1), C.SEALED_FROM - timedelta(days=1))
    lo = start
    while lo <= last:
        hi = min((pd.Timestamp(lo) + pd.offsets.MonthEnd(0)).date(), last)
        chunk = data.trips_range(lo, hi)
        counts += count_trips(chunk, universe, days)
        del chunk
        lo = hi + timedelta(days=1)
    return days, counts


def to60(c: np.ndarray, axis: int = 1) -> np.ndarray:
    """Suma grupos de 4 bins de 15 min a lo largo de `axis` (76 → 19)."""
    c = np.moveaxis(c, axis, 0)
    c = c.reshape(c.shape[0] // PER, PER, *c.shape[1:]).sum(1)
    return np.moveaxis(c, 0, axis)


@lru_cache(maxsize=None)
def _dtype(d: date) -> str:
    return day_type(d)


def _is_weekday(d: date) -> bool:
    return _dtype(d) == "weekday"


def _valid_days(counts: np.ndarray) -> np.ndarray:
    """Días con algún viaje en la ventana (descarta huecos de datos)."""
    return counts.sum(axis=(1, 2, 3)) > 0


# ============================================================================
# Bases sin corrección: ma / daily / model
# ============================================================================

def ma_counts(counts: np.ndarray, days: list[date], i: int, valid: np.ndarray | None = None) -> np.ndarray:
    """Media de los días de las 4 semanas anteriores (i-28..i-1) del mismo tipo
    de día. `[estación, 76, 2]`. Solo lee `counts[:i]`. NaN si no hay ninguno."""
    valid = _valid_days(counts[:i]) if valid is None else valid
    wd = _is_weekday(days[i])
    idx = [j for j in range(max(0, i - 28), i) if valid[j] and _is_weekday(days[j]) == wd]
    if not idx:
        return np.full(counts.shape[1:], np.nan)
    return counts[idx].astype(np.float64).mean(axis=0)


FEATURES = ["station", "block", "dow", "holiday", "lag7", "lag14", "mean28"]


def features(counts: np.ndarray, days: list[date], i: int, kind: int,
             valid: np.ndarray | None = None) -> pd.DataFrame:
    """Features del día `i` para (estación × bloque de 60) del tipo `kind`:
    rezago 7 y 14 días del mismo bloque y media de 28 días del mismo bloque y
    tipo de día (las del run 1, extendidas a 19 bloques). Solo usan
    `counts[:i]`. NaN si no hay dato."""
    S = counts.shape[1]
    b60 = to60(counts[:i, :, :, kind], axis=2) if i > 0 else np.zeros((0, S, N60))  # [i, S, 19]
    valid = _valid_days(counts[:i]) if valid is None else valid

    def lag(k):
        return b60[i - k] if i - k >= 0 and valid[i - k] else np.full((S, N60), np.nan)

    wd = _is_weekday(days[i])
    idx = [j for j in range(max(0, i - 28), i) if valid[j] and _is_weekday(days[j]) == wd]
    mean28 = b60[idx].mean(axis=0) if idx else np.full((S, N60), np.nan)
    return pd.DataFrame({
        "station": np.repeat(np.arange(S), N60),
        "block": np.tile(np.arange(N60), S),
        "dow": days[i].weekday(),
        "holiday": int(_dtype(days[i]) == "holiday"),
        "lag7": lag(7).ravel(), "lag14": lag(14).ravel(), "mean28": mean28.ravel(),
    })


class Model:
    """Dos LightGBM Poisson (salidas, llegadas) por (estación, bloque de 60)."""

    def __init__(self, counts, days, train_end: date, universe_size: int):
        import lightgbm as lgb
        self.S = universe_size
        valid_all = _valid_days(counts)
        tr = [i for i, d in enumerate(days) if d <= train_end and valid_all[i]]
        assert tr and days[max(tr)] <= train_end
        self.models = {}
        for kind in (DEP, ARR):
            X, y = [], []
            for i in tr:
                X.append(features(counts, days, i, kind, valid_all))
                y.append(to60(counts[i, :, :, kind], axis=1).ravel())
            X = pd.concat(X, ignore_index=True)
            X["station"] = pd.Categorical(X["station"], categories=range(self.S))
            m = lgb.LGBMRegressor(**LGB_PARAMS)
            m.fit(X[FEATURES], np.concatenate(y), categorical_feature=["station"])
            self.models[kind] = m

    def predict(self, counts, days, i, valid=None) -> np.ndarray:
        """Pronóstico base `[estación, 19, 2]` (bloques de 60) del día i."""
        out = np.zeros((self.S, N60, 2))
        for kind in (DEP, ARR):
            X = features(counts, days, i, kind, valid)
            X["station"] = pd.Categorical(X["station"], categories=range(self.S))
            out[:, :, kind] = np.clip(self.models[kind].predict(X[FEATURES]), 0, None).reshape(self.S, N60)
        return out


def base15(variant: str, counts, days, i, model: Model | None = None, valid=None) -> np.ndarray:
    """Base sin corrección `[estación, 76, 2]` en bins de 15 min. Solo lee `counts[:i]`.
    `model` se reparte parejo dentro de su bloque de 60."""
    valid = _valid_days(counts[:i]) if valid is None else valid
    if variant in ("ma", "daily"):
        return np.nan_to_num(ma_counts(counts, days, i, valid))   # sin días comparables → 0
    if variant == "model":
        return np.repeat(model.predict(counts, days, i, valid) / PER, PER, axis=1)
    raise ValueError(variant)


def correction_factor(base: np.ndarray, today: np.ndarray, sb: int) -> np.ndarray:
    """`[estación, 1, 2]`: (obs + K)/(pred + K) sobre los primeros `sb` bins
    del día, acotado a [0.5, 2]. Solo lee `today[:, :sb]`."""
    obs = today[:, :sb, :].astype(np.float64).sum(axis=1)
    pred = base[:, :sb, :].sum(axis=1)
    return np.clip((obs + K_SHRINK) / (pred + K_SHRINK), *FACTOR_CLIP)[:, None, :]


def emit_day(variant: str, base: np.ndarray, today: np.ndarray, sb: int) -> np.ndarray:
    """Emisión en el bin `sb` (s = 05:30 + 15·sb min): pronóstico de TODO el día
    en bloques de 60, `[estación, 19, 2]`. `daily` no corrige.

    Para `ma`/`model` se re-escala solo lo que FALTA (bins ≥ sb) por el factor;
    los bins ya transcurridos valen lo observado. Así el bloque en curso vale
    `observado_en_el_bloque + factor · resto_esperado`, y los bloques futuros
    `factor · base`. Solo lee `today[:, :sb]`."""
    if variant == "daily":
        return to60(base, axis=1)
    factor = correction_factor(base, today, sb)
    adj = base * factor
    adj[:, :sb, :] = today[:, :sb, :]
    return to60(adj, axis=1)


# ============================================================================
# Calendario de emisiones y formato Forecast
# ============================================================================

def emission_offsets(f: int) -> list[int]:
    """Minutos desde 05:30 de cada emisión de la serie de frecuencia f."""
    return list(range(0, C.WINDOW_MIN, f))


def covered_blocks(s_min: int, f: int) -> range:
    """Bloques de 60 que toca [s, s + f + L + h_max], recortado a 00:30."""
    end = min(s_min + f + LEAD_MIN + H_MAX_H * 60, C.WINDOW_MIN)
    return range(s_min // 60, math.ceil(end / 60))


def series_frame(day, variant: str, f: int, universe: list[str], full_by_emission) -> pd.DataFrame:
    """DataFrame Forecast de una serie. `full_by_emission(s_min)` →
    `[estación, 19, 2]`. f = 0 (`daily`): una emisión que cubre las 19 h."""
    start, _ = C.day_bounds(day)
    S = len(universe)
    parts = []
    offs = [0] if f == 0 else emission_offsets(f)
    for s_min in offs:
        blocks = range(N60) if f == 0 else covered_blocks(s_min, f)
        full = full_by_emission(s_min)
        nb = len(blocks)
        parts.append(pd.DataFrame({
            "issued_at": start + timedelta(minutes=s_min),
            "variant": variant,
            "short_name": np.repeat(universe, nb),
            "block_start": np.tile([start + timedelta(minutes=60 * b) for b in blocks], S),
            "block_min": 60,
            "departures": full[:, list(blocks), DEP].ravel().astype(float),
            "arrivals": full[:, list(blocks), ARR].ravel().astype(float),
            "refresh_min": f,
            "horizon_h": DAILY_HORIZON_H if f == 0 else H_MAX_H,
        }))
    df = pd.concat(parts, ignore_index=True)
    for c in K.FORECAST_OPTIONAL:
        df[c] = np.nan
    df["issued_at"] = pd.to_datetime(df["issued_at"])
    return K.validate_forecast(df[K.FORECAST_COLUMNS])


def forecast_path(day, variant: str, f: int):
    d = C.as_date(day).isoformat()
    return OUT_FORECASTS / (f"{d}_daily.parquet" if variant == "daily" else f"{d}_{variant}_f{f}.parquet")


def build_day(day, universe, counts, days, model: Model, oracle15: np.ndarray, write=True) -> dict:
    """Todas las series del día. Devuelve `{(variant, f): DataFrame}`:
    daily (f=0) y {oracle, ma, model} × f ∈ {15, 60, 180}. `oracle15` = conteos
    reales del día `[estación, 76, 2]`."""
    i = days.index(C.as_date(day))
    valid = _valid_days(counts[:i])
    today = counts[i]
    bases = {v: base15(v, counts, days, i, model, valid) for v in ("ma", "model")}
    out = {("daily", 0): series_frame(day, "daily", 0, universe,
                                      lambda s: emit_day("daily", bases["ma"], today, 0))}
    oracle60 = to60(oracle15.astype(np.float64), axis=1)
    for f in REFRESH_MIN:
        out[("oracle", f)] = series_frame(day, "oracle", f, universe, lambda s: oracle60)
        for v in ("ma", "model"):
            out[(v, f)] = series_frame(day, v, f, universe,
                                       lambda s, v=v: emit_day(v, bases[v], today, s // BIN_MIN))
    if write:
        OUT_FORECASTS.mkdir(parents=True, exist_ok=True)
        for (v, f), df in out.items():
            df.to_parquet(forecast_path(day, v, f), index=False)
    return out


# ============================================================================
# Exactitud
# ============================================================================

def censored(day, universe: list[str]) -> np.ndarray:
    """`[estación, 19]` bool: algún snapshot GBFS no en blanco con 0 bicis
    disponibles dentro del bloque de 60."""
    s = data.snapshots(day)
    s = s[(~s["blank"]) & (s["bikes"] == 0)]
    start, _ = C.day_bounds(day)
    off = ((s["t"] - start) / pd.Timedelta(minutes=1)).to_numpy()
    sidx = pd.Index(universe).get_indexer(s["short_name"])
    ok = (off >= 0) & (off < C.WINDOW_MIN) & (sidx >= 0)
    out = np.zeros((len(universe), N60), bool)
    out[sidx[ok], (off[ok] // 60).astype(int)] = True
    return out


def _acc_add(store: dict, key: tuple, err: np.ndarray, act: np.ndarray) -> None:
    a = store.setdefault(key, [0, 0.0, 0.0, 0.0])
    a[0] += err.size
    a[1] += float(np.abs(err).sum())
    a[2] += float(np.abs(act).sum())
    a[3] += float(err.sum())


def accumulate(store: dict, split: str, df: pd.DataFrame, universe: list[str],
               truth60: np.ndarray, cens60: np.ndarray, day) -> None:
    """Suma errores de una serie (verdad = conteos reales `[estación, 19, 2]`).

    Distancia a la emisión = piso((block_start − issued_at) / 1 h), con el
    bloque en curso (que empezó antes de s) en la cubeta 0.
    """
    start, _ = C.day_bounds(day)
    si = pd.Index(universe).get_indexer(df["short_name"])
    bi = ((df["block_start"] - start) / pd.Timedelta(minutes=60)).to_numpy().astype(int)
    dist = np.maximum(0, np.floor((df["block_start"] - df["issued_at"]) / pd.Timedelta(hours=1))).to_numpy().astype(int)
    tdep, tarr = truth60[si, bi, DEP], truth60[si, bi, ARR]
    pred = {"salidas": df["departures"].to_numpy(), "llegadas": df["arrivals"].to_numpy()}
    act = {"salidas": tdep, "llegadas": tarr}
    pred["neto"], act["neto"] = pred["llegadas"] - pred["salidas"], tarr - tdep
    cm = cens60[si, bi]
    variant, f = df["variant"].iat[0], int(df["refresh_min"].iat[0])
    for target in pred:
        err = pred[target] - act[target]
        for h in np.unique(dist):
            m = dist == h
            for subset, sel in (("all", m), ("censurado", m & cm), ("no_censurado", m & ~cm)):
                if sel.any():
                    _acc_add(store, (split, variant, f, int(h), subset, target), err[sel], act[target][sel])


def accuracy_table(store: dict) -> pd.DataFrame:
    rows = [dict(split=k[0], variant=k[1], refresh_min=k[2], dist_h=k[3], subset=k[4], target=k[5],
                 n=v[0], mae=v[1] / v[0], wape=v[1] / max(v[2], 1e-9), bias=v[3] / v[0])
            for k, v in store.items()]
    return pd.DataFrame(rows).sort_values(["split", "variant", "refresh_min", "subset", "target", "dist_h"]).reset_index(drop=True)


def pooled(acc: pd.DataFrame) -> pd.DataFrame:
    """MAE / WAPE por variante × f sobre todas las distancias (subset all), ponderado por n."""
    a = acc[acc["subset"] == "all"].copy()
    a["sum_abs"] = a["mae"] * a["n"]
    a["sum_act"] = a["sum_abs"] / a["wape"].replace(0, np.nan)
    g = a.groupby(["split", "target", "variant", "refresh_min"]).agg(n=("n", "sum"), sum_abs=("sum_abs", "sum"), sum_act=("sum_act", "sum"))
    g["mae"] = g["sum_abs"] / g["n"]
    g["wape"] = g["sum_abs"] / g["sum_act"]
    return g[["n", "mae", "wape"]].reset_index()


# ============================================================================
# Comparación justa a igual (bloque objetivo, antelación): como decide el asignador
# ============================================================================

K_AHEAD = range(6)      # k-ésima hora de la ventana [t+L, t+L+6h]


def lead_table() -> pd.DataFrame:
    """Lee los parquet ya escritos. Para cada decisión t de la rejilla de 15 min
    (`C.decision_times`, con t + L < 00:30) y cada k = 0..5, el objetivo es el
    bloque que contiene t + L + 60·k min (la k-ésima hora de la ventana del
    asignador). Cada serie usa su emisión más reciente con `issued_at ≤ t`
    (daily: la única). Todas las series se evalúan sobre EXACTAMENTE los mismos
    (día, estación, t, k), contra los conteos del oracle. Devuelve MAE/WAPE de
    salidas, llegadas y neto por split × serie × k (`ahead_h` = k)."""
    cfg = json.loads(C.DAYS_JSON.read_text())
    split_of = {d["day"]: "evaluacion" for d in cfg["evaluacion"]}
    split_of.update({d["day"]: "seleccion" for d in cfg["seleccion"]})
    series = [("daily", 0)] + [(v, f) for v in ("ma", "model") for f in REFRESH_MIN]
    acc: dict = {}
    for day, split in split_of.items():
        start, end = C.day_bounds(day)
        o = pd.read_parquet(forecast_path(day, "oracle", 60)).drop_duplicates(["short_name", "block_start"])
        o = o.set_index(["short_name", "block_start"])[["departures", "arrivals"]]
        rows = []
        for t in C.decision_times(day):
            for k in K_AHEAD:
                tt = t + timedelta(minutes=LEAD_MIN + 60 * k)
                if tt < end:
                    rows.append((t, k, start + timedelta(hours=int((tt - start) / pd.Timedelta(hours=1)))))
        grid = pd.DataFrame(rows, columns=["t", "ahead_h", "block_start"])
        for v, f in series:
            df = pd.read_parquet(forecast_path(day, v, f))
            ems = np.sort(df["issued_at"].unique())
            g = grid.copy()
            g["issued_at"] = ems[np.searchsorted(ems, g["t"].to_numpy(), side="right") - 1]
            m = g.merge(df, on=["issued_at", "block_start"])
            assert len(m) == len(g) * df["short_name"].nunique(), (v, f, len(m), len(g))
            m = m.join(o, on=["short_name", "block_start"], rsuffix="_true")
            e_dep, a_dep = m["departures"] - m["departures_true"], m["departures_true"]
            e_arr, a_arr = m["arrivals"] - m["arrivals_true"], m["arrivals_true"]
            for target, e, a in (("salidas", e_dep, a_dep), ("llegadas", e_arr, a_arr),
                                 ("neto", e_arr - e_dep, a_arr - a_dep)):
                for h, idx in m.groupby("ahead_h").groups.items():
                    _acc_add(acc, (split, v, f, int(h), "all", target), e.loc[idx].to_numpy(), a.loc[idx].to_numpy())
    return accuracy_table(acc).rename(columns={"dist_h": "ahead_h"}).drop(columns=["subset"])


# ============================================================================
def main() -> None:
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "lead":
        t = lead_table()
        t.to_csv(OUT_RESULTS / "accuracy_by_lead.csv", index=False)
        print(t[(t.target == "salidas") & (t.split == "evaluacion")].pivot_table(
            index="ahead_h", columns=["variant", "refresh_min"], values="mae").round(3).to_string())
        return
    cfg = json.loads(C.DAYS_JSON.read_text())
    split_of = {C.as_date(d["day"]): "evaluacion" for d in cfg["evaluacion"]}
    split_of.update({C.as_date(d["day"]): "seleccion" for d in cfg["seleccion"]})
    run_days = sorted(split_of)
    assert len(run_days) == 30 and max(run_days) < C.SEALED_FROM
    universe = sorted(set().union(*[set(data.stations(d)["short_name"]) for d in run_days]))
    hist_days, counts = load_history(universe, C.TRAIN_START, max(run_days))
    print(f"historial: {len(hist_days)} días, {len(universe)} estaciones", flush=True)
    # Los 30 días usan la regla del simulador (salida desde estación desconocida
    # se ignora; las llegadas de esos viajes sí cuentan). La historia previa usa
    # "origen en el universo" (unión de las estaciones de los 30 días): no hay
    # lista de estaciones por día antes de agosto. Se reporta la diferencia.
    oracle, dropped, total = {}, 0, 0
    for d in run_days:
        tr = data.trips(d)
        oracle[d] = count_trips(tr, universe, [d], dep_known=tr["o_known"].to_numpy())[0]
        raw = count_trips(tr, universe, [d])[0]
        dropped += raw[:, :, DEP].sum() - oracle[d][:, :, DEP].sum()
        total += raw[:, :, DEP].sum()
        counts[hist_days.index(d)] = oracle[d]
    print(f"salidas ignoradas por o_known en los 30 días: {int(dropped)} de {int(total)} ({dropped / total:.4%})", flush=True)
    model = Model(counts, hist_days, C.TRAIN_END, len(universe))
    print("modelo entrenado", flush=True)

    store: dict = {}
    for d in run_days:
        frames = build_day(d, universe, counts, hist_days, model, oracle[d])
        truth60 = to60(oracle[d].astype(np.float64), axis=1)
        cens = censored(d, universe)
        for df in frames.values():
            accumulate(store, split_of[d], df, universe, truth60, cens, d)
        print("listo", d, flush=True)

    OUT_RESULTS.mkdir(parents=True, exist_ok=True)
    acc = accuracy_table(store)
    acc.to_csv(OUT_RESULTS / "accuracy.csv", index=False)
    orc = acc[acc["variant"] == "oracle"]
    print("oracle MAE máx:", orc["mae"].max())
    p = pooled(acc)
    p.to_csv(OUT_RESULTS / "accuracy_pooled.csv", index=False)
    print(p[p["target"] != "neto"].to_string())


if __name__ == "__main__":
    main()
