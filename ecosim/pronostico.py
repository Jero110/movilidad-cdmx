"""Pronósticos del run 3: cinco brazos, dos formas y una sola interfaz ForecastTable.

Los oráculos reciben una copia congelada de la verdad del día: son cotas ideales,
no predictores causales. Los otros tres brazos leen únicamente historia anterior
al día, y el directo observa además únicamente cuartos terminados antes de t.
Ejecutar con `uv run python -m ecosim.pronostico` (bajo scripts/heavy.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from ecosim import config as C
from ecosim.days import day_type

DEP, ARR = 0, 1
NB15 = C.WINDOW_MIN // 15
SLOTS = (NB15 + 3) // 4  # 19 horas completas y 00:00–00:30
OUT_MODELS = C.DERIVED / "modelos"
OUT_FORECASTS = C.DERIVED / "forecasts"
OUT_RESULTS = C.REPO_ROOT / "ecosim" / "results" / "pronostico"
FEATURES_DAILY = ["station", "minute", "dow", "holiday", "lag7", "lag14", "mean28"]
FEATURES_DIRECT = FEATURES_DAILY + ["k", "last15", "last60", "elapsed_delta"]
# Hiperparámetros heredados, sin búsqueda.
LGB_PARAMS = dict(objective="poisson", n_estimators=200, learning_rate=0.05,
                  num_leaves=31, min_child_samples=50, subsample=0.8,
                  subsample_freq=1, colsample_bytree=0.9, random_state=0,
                  deterministic=True, force_row_wise=True, n_jobs=4, verbose=-1)


def count_trips(trips: pd.DataFrame, universe: list[str], days: list[date],
                dep_known: np.ndarray | None = None) -> np.ndarray:
    """Conteos [día, estación, cuarto, salida/llegada] con llegada independiente.

    No infiere llegadas desde salidas: un viaje puede cruzar dos ventanas.
    """
    out = np.zeros((len(days), len(universe), NB15, 2), np.float32)
    day_ix = {d: i for i, d in enumerate(days)}
    station_ix = pd.Index(universe)
    for col, station, kind in (("t_dep", "o", DEP), ("t_arr", "d", ARR)):
        times = pd.to_datetime(trips[col])
        shifted = times - pd.Timedelta(hours=5)
        di = shifted.dt.date.map(day_ix).fillna(-1).to_numpy(dtype=int)
        minutes = ((times - shifted.dt.normalize() - pd.Timedelta(hours=5)) /
                   pd.Timedelta(minutes=1)).to_numpy()
        si = station_ix.get_indexer(trips[station])
        ok = (di >= 0) & (si >= 0) & (minutes >= 0) & (minutes < C.WINDOW_MIN)
        if kind == DEP and dep_known is not None:
            ok &= np.asarray(dep_known, dtype=bool)
        np.add.at(out, (di[ok], si[ok], (minutes[ok] // 15).astype(int), kind), 1)
    return out


def load_counts(start: date = date(2024, 1, 1), end: date = date(2026, 8, 31)) -> tuple[list[date], list[str], np.ndarray]:
    """Agrega directamente el parquet de viajes sin cargar 54 M de filas en pandas.

    El universo es la unión de IDs válidos de 2025–2026; no indexamos estaciones
    de prueba que todavía no existían en entrenamiento por posición cambiante.
    """
    con = duckdb.connect()
    con.execute("SET threads=4")
    path = str(C.TRIPS_PARQUET)
    universe = [r[0] for r in con.execute(f"""select distinct station from (
        select o station from '{path}' where t_dep >= '2025-01-01'
        union all select d station from '{path}' where t_arr >= '2025-01-01')
        where regexp_full_match(station, '{C.SHORT_NAME_RE}') order by station""").fetchall()]
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    counts = np.zeros((len(days), len(universe), NB15, 2), np.float32)
    idx = {s: i for i, s in enumerate(universe)}
    for kind, tm, station in ((DEP, "t_dep", "o"), (ARR, "t_arr", "d")):
        rows = con.execute(f"""select cast({tm} - interval 5 hour as date) wd,
             {station} station,
             date_diff('minute', date_trunc('day', {tm} - interval 5 hour) + interval 5 hour, {tm}) // 15 bin,
             count(*) n from '{path}'
             where {tm} >= ? and {tm} < ? and regexp_full_match({station}, ?)
             group by 1,2,3""", [datetime.combine(start, C.DAY_START),
                                    datetime.combine(end + timedelta(days=1), C.DAY_END), C.SHORT_NAME_RE]).fetchall()
        for d, s, b, n in rows:
            di = (d - start).days
            if 0 <= di < len(days) and 0 <= b < NB15 and s in idx:
                counts[di, idx[s], b, kind] += n
    con.close()
    # El CSV de marzo de 2026 se corta el 22: el 23 es parcial y los
    # siguientes contienen solo filas sueltas. No sirven ni como rezagos ni
    # como etiquetas de entrenamiento, tampoco como verdad de evaluación.
    for d in pd.date_range("2026-03-23", "2026-03-31"):
        if start <= d.date() <= end:
            counts[(d.date() - start).days] = 0
    return days, universe, counts


def _blocks(c: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """Ventanas de 60 min [estación, (t,k), tipo]; la última se trunca en 00:30."""
    prefix = np.concatenate([np.zeros((c.shape[0], 1, 2)), c.cumsum(axis=1)], axis=1)
    ends = np.minimum(starts + 4, NB15)
    return prefix[:, ends] - prefix[:, starts]


def _type(d: date) -> bool:
    return day_type(d) == "weekday"


def _features(counts: np.ndarray, days: list[date], i: int, kind: int,
              ticks: np.ndarray, direct: bool, labels: bool = True) -> tuple[pd.DataFrame, np.ndarray | None]:
    """Una construcción compartida para entrenamiento y emisión, sin días futuros.

    ticks son índices de cuartos de hora desde las 05:00. La etiqueta de cada
    fila es el conteo de [t+60k,t+60(k+1)) (o la hora de reloj en forma diaria).
    """
    S = counts.shape[1]
    ticks = np.asarray(ticks, dtype=int)
    start = (ticks[:, None] + 4 * np.arange(6 if direct else SLOTS)[None, :]).ravel()
    start = start[start < NB15]
    # En diaria ticks=[0]; en directa todos los ticks se expanden por k.
    t_values = np.repeat(ticks, 6 if direct else SLOTS)
    k_values = np.tile(np.arange(6 if direct else SLOTS), len(ticks))
    keep = (t_values + 4 * k_values) < NB15
    t_values, k_values = t_values[keep], k_values[keep]
    B = len(start)
    assert B == len(t_values)
    valid = counts[:i].sum(axis=(1, 2, 3)) > 0
    blank = np.full((S, B, 2), np.nan)
    def lag(n: int) -> np.ndarray:
        j = i - n
        return _blocks(counts[j], start) if j >= 0 and valid[j] else blank
    peers = [j for j in range(max(0, i - 28), i) if valid[j] and _type(days[j]) == _type(days[i])]
    mean = np.mean([_blocks(counts[j], start) for j in peers], axis=0) if peers else blank
    vals = {
        "station": np.repeat(np.arange(S, dtype=np.int32), B),
        "minute": np.tile(300 + 15 * start, S).astype(np.int16) % 1440,
        "dow": np.full(S * B, days[i].weekday(), np.int8),
        "holiday": np.full(S * B, int(day_type(days[i]) == "holiday"), np.int8),
        "lag7": lag(7)[:, :, kind].ravel().astype(np.float32),
        "lag14": lag(14)[:, :, kind].ravel().astype(np.float32),
        "mean28": mean[:, :, kind].ravel().astype(np.float32),
    }
    if direct:
        today = counts[i, :, :, kind]
        cum = np.concatenate([np.zeros((S, 1)), today.cumsum(axis=1)], axis=1)
        # Solo observaciones con fin <= t, nunca del bloque que empieza en t.
        last15 = np.where(t_values > 0, cum[:, t_values] - cum[:, np.maximum(t_values - 1, 0)], 0)
        last60 = cum[:, t_values] - cum[:, np.maximum(t_values - 4, 0)]
        expected = np.mean([counts[j, :, :, kind].cumsum(axis=1) for j in peers], axis=0) if peers else None
        prior_mean = np.column_stack([np.zeros(S) if t == 0 or expected is None else expected[:, t-1]
                                      for t in t_values])
        vals.update(k=np.tile(k_values, S).astype(np.int8),
                    last15=last15.ravel().astype(np.float32),
                    last60=last60.ravel().astype(np.float32),
                    elapsed_delta=(cum[:, t_values] - prior_mean).ravel().astype(np.float32))
    y = _blocks(counts[i], start)[:, :, kind].ravel() if labels else None
    return pd.DataFrame(vals), y


def _ticks(direct: bool, day: date | None = None) -> np.ndarray:
    """Una de cada 12 decisiones; fase rota diariamente y cubre los 78 ticks.

    Referencia fija (sin azar): 2024-01-01 tiene fase cero. El mismo día
    conserva su fase independientemente del inicio de la historia cargada.
    """
    if not direct:
        return np.array([0])
    if day is None:
        raise ValueError("El submuestreo directo requiere el día")
    phase = (day - date(2024, 1, 1)).days % 12
    return np.arange(phase, NB15, 12)


def _eligible(counts: np.ndarray, days: list[date], start: date, end: date,
              excluded: set[date] | None = None) -> list[int]:
    """Días con historia de 28 días y etiqueta disponible dentro del corte."""
    excluded = excluded or set()
    return [i for i, d in enumerate(days) if start + timedelta(days=28) <= d <= end
            and d not in excluded and counts[i].sum() > 0]


def sampling_table(counts: np.ndarray, days: list[date]) -> pd.DataFrame:
    """Auditoría por corte/tick: número de días, filas y horizontes por estación."""
    rows = []
    S = counts.shape[1]
    for fold in C.FOLDS:
        ticks_days = np.zeros(NB15, dtype=int)
        for i in _eligible(counts, days, fold["train_start"], fold["train_end"]):
            ticks_days[_ticks(True, days[i])] += 1
        for tick, n_days in enumerate(ticks_days):
            k_validos = min(6, (NB15 - tick + 3) // 4)
            rows.append({"corte": fold["name"], "tick": tick,
                         "hora": (datetime.combine(date(2024, 1, 1), C.DAY_START)
                                  + timedelta(minutes=15*tick)).strftime("%H:%M"),
                         "dias": int(n_days), "k_validos": k_validos,
                         "filas_por_target": int(n_days * k_validos * S)})
    return pd.DataFrame(rows)


def train(counts: np.ndarray, days: list[date], start: date, end: date,
          direct: bool, output: Path | None = None, excluded: set[date] | None = None):
    """Dos Poisson, entrenados solo con días del corte y 28 días previos íntegros."""
    import lightgbm as lgb
    eligible = _eligible(counts, days, start, end, excluded)
    if not eligible:
        raise ValueError("Sin días de entrenamiento con rezagos íntegros")
    models = []
    cols = FEATURES_DIRECT if direct else FEATURES_DAILY
    for kind in (DEP, ARR):
        pairs = [_features(counts, days, i, kind, _ticks(direct, days[i]), direct) for i in eligible]
        X = pd.concat([p[0] for p in pairs], ignore_index=True)[cols]
        y = np.concatenate([p[1] for p in pairs])
        X["station"] = pd.Categorical(X["station"], categories=range(counts.shape[1]))
        model = lgb.LGBMRegressor(**LGB_PARAMS)
        model.fit(X, y, categorical_feature=["station"])
        models.append(model)
        del X, y, pairs
    if output is not None:
        import joblib
        output.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(models, output)
    return models


def predict(models, counts: np.ndarray, days: list[date], i: int,
            ticks: np.ndarray, direct: bool) -> np.ndarray:
    """Predicción [estación, bloques válidos, 2], mismas features que train."""
    result = []
    for kind, model in enumerate(models):
        X, _ = _features(counts, days, i, kind, ticks, direct, labels=False)
        cols = FEATURES_DIRECT if direct else FEATURES_DAILY
        X["station"] = pd.Categorical(X["station"], categories=range(counts.shape[1]))
        result.append(np.clip(model.predict(X[cols]), 0, None).reshape(counts.shape[1], -1))
    return np.stack(result, axis=-1)


@dataclass
class Forecast:
    """ForecastTable: diario congelado a 05:00; directo calculado para cada t."""
    modelo: str
    days: list[date]
    counts: np.ndarray
    day: date
    models: list | None = None

    def __post_init__(self):
        self.forma = "directa" if self.modelo.endswith("directo") else "diaria"
        self.i = self.days.index(self.day)
        self._truth = self.counts[self.i].copy() if self.modelo.startswith("oraculo") else None
        self._cache = {}
        if self.forma == "diaria":
            if self.modelo == "ma_diaria":
                peers = [j for j in range(max(0, self.i - 28), self.i)
                         if _type(self.days[j]) == _type(self.day) and self.counts[j].sum() > 0]
                base = self.counts[peers].mean(axis=0) if peers else np.zeros_like(self.counts[self.i])
                self._daily = _blocks(base, np.arange(0, NB15, 4))
            elif self.modelo == "oraculo_diario":
                self._daily = _blocks(self._truth, np.arange(0, NB15, 4))
            else:
                self._daily = predict(self.models, self.counts, self.days, self.i, np.array([0]), False)

    def table(self, t: datetime, n_hours: int) -> np.ndarray:
        start, end = C.day_bounds(self.day)
        if t < start or t >= end or (t - start).total_seconds() % 900 or n_hours < 1:
            raise ValueError("Decisión fuera de la rejilla/ventana o n_hours inválido")
        offset = int((t - start).total_seconds() // 900)
        out = np.zeros((self.counts.shape[1], 4 * n_hours, 2), np.float64)
        remaining = min(4 * n_hours, NB15 - offset)
        if self.forma == "diaria":
            for j in range(remaining):
                b = (offset + j) // 4
                width = min(4, NB15 - 4 * b)
                out[:, j, :] = self._daily[:, b, :] / width
        else:
            if self.modelo == "oraculo_directo":
                starts = offset + 4 * np.arange(n_hours)
                starts = starts[starts < NB15]
                blocks = _blocks(self._truth, starts)
            else:
                if not self._cache:
                    ticks = np.arange(NB15)
                    whole = predict(self.models, self.counts, self.days, self.i, ticks, True)
                    cursor = 0
                    for slot in ticks:
                        n = min(6, (NB15 - slot + 3) // 4)
                        self._cache[slot] = whole[:, cursor:cursor+n, :]
                        cursor += n
                blocks = self._cache[offset]
            for k in range(min(n_hours, blocks.shape[1])):
                width = min(4, NB15 - offset - 4 * k)
                out[:, 4*k:4*k+width, :] = blocks[:, k:k+1, :] / width
        return out


def forecast(modelo: str, counts: np.ndarray, days: list[date], day: date,
             models: list | None = None) -> Forecast:
    """Factory estable para los siete brazos (cinco pronósticos)."""
    if modelo not in {"ma_diaria", "lgbm_diario", "oraculo_diario", "lgbm_directo", "oraculo_directo"}:
        raise ValueError(modelo)
    if modelo.startswith("lgbm") and models is None:
        raise ValueError("LightGBM requiere sus modelos entrenados para el corte")
    return Forecast(modelo, days, counts, day, models)


def accuracy(days: list[date], counts: np.ndarray, test_days: list[date], models_daily,
             models_direct) -> pd.DataFrame:
    """Error de todos los días del periodo, todas las decisiones y cada k.

    El oráculo se evalúa sobre sus mismos bloques (no sobre una rejilla ajena).
    MAE, WAPE y sesgo se acumulan con denominadores exactos, incluido neto.
    """
    store = {}
    for d in test_days:
        variants = {name: forecast(name, counts, days, d, models_daily if name == "lgbm_diario" else
                                    models_direct if name == "lgbm_directo" else None)
                    for name in ("ma_diaria", "lgbm_diario", "oraculo_diario", "lgbm_directo", "oraculo_directo")}
        for t in C.decision_times(d):
            offset = int((t - C.day_bounds(d)[0]).total_seconds() // 900)
            for name, f in variants.items():
                table = f.table(t, 6)
                truth_table = variants["oraculo_directo" if f.forma == "directa" else "oraculo_diario"].table(t, 6)
                for k in range(6):
                    start = offset + 4*k
                    if start >= NB15:
                        continue
                    width = min(4, NB15 - start)
                    pred = table[:, 4*k:4*k+width, :].sum(axis=1)
                    truth = truth_table[:, 4*k:4*k+width, :].sum(axis=1)
                    # Verdad expresada en la misma forma del oráculo gemelo:
                    # sin fuga de resolución intrahoraria hacia la métrica.
                    for label, p, y in (("salidas", pred[:, DEP], truth[:, DEP]),
                                        ("llegadas", pred[:, ARR], truth[:, ARR]),
                                        ("neto", pred[:, ARR]-pred[:, DEP], truth[:, ARR]-truth[:, DEP])):
                        key = (d.strftime("%Y-%m"), name, k, label)
                        bucket = store.setdefault(key, np.zeros(4))
                        bucket += (len(y), np.abs(p-y).sum(), np.abs(y).sum(), (p-y).sum())
        print("exactitud", d, flush=True)
    return pd.DataFrame([dict(mes=m, variante=v, k=k, objetivo=target, n=int(a[0]),
                              mae=a[1]/a[0], wape=a[1]/a[2] if a[2] else np.nan,
                              sesgo=a[3]/a[0]) for (m,v,k,target), a in store.items()])


def main() -> None:
    import joblib
    days, universe, counts = load_counts()
    print(f"conteos: {len(days)} días, {len(universe)} estaciones", flush=True)
    cfg = json.loads(C.DAYS_JSON.read_text())
    OUT_RESULTS.mkdir(parents=True, exist_ok=True)
    OUT_MODELS.mkdir(parents=True, exist_ok=True)
    OUT_FORECASTS.mkdir(parents=True, exist_ok=True)
    (OUT_FORECASTS / "universe_run3.json").write_text(json.dumps(universe) + "\n")
    tables = []
    for fold in C.FOLDS:
        name, first, last = fold["name"], fold["train_start"], fold["train_end"]
        month = fold["test_month"]
        # Exactitud: todos los días con viajes íntegros, no solo los que tienen
        # fotos GBFS para simulación. Marzo 2026 termina el día 22 en el CSV.
        test = [d for i, d in enumerate(days) if d.strftime("%Y-%m") == month
                and counts[i].sum() > 0 and (month != "2026-03" or d <= C.TRIPS_2026_MARCH_LAST_COMPLETE)]
        models = []
        for direct in (False, True):
            path = OUT_MODELS / f"{name}_{first}_{last}_{'directo' if direct else 'diario'}.joblib"
            if path.exists():
                m = joblib.load(path)
            else:
                m = train(counts, days, first, last, direct, path)
            models.append(m)
            print("entrenado", name, "directo" if direct else "diario", flush=True)
        if test:
            tables.append(accuracy(days, counts, test, *models))
    acc = pd.concat(tables, ignore_index=True)
    acc.to_csv(OUT_RESULTS / "accuracy_run3.csv", index=False)
    sampling = sampling_table(counts, days)
    sampling.to_csv(OUT_RESULTS / "direct_sampling.csv", index=False)
    report(acc, days, counts, cfg, sampling)
    print("reporte:", OUT_RESULTS / "REPORT.md", flush=True)


def report(acc, days, counts, cfg, sampling):
    """Tablas de exactitud con baseline del mismo mes, y dos pruebas de control."""
    selected = [date.fromisoformat(r["day"]) for r in cfg["seleccion"]]
    # La comparación de 2024 y la fuga aleatoria usan exclusivamente selección.
    tests = []
    for label, start, end, excluded in (
        ("hacia adelante", date(2025,1,1), date(2025,7,31), set()),
        ("con 2024", date(2024,1,1), date(2025,7,31), set()),
        ("al azar (sin 15 días de selección)", date(2025,1,1), date(2025,8,31), set(selected))):
        model = train(counts, days, start, end, False, excluded=excluded)
        errors = []
        for d in selected:
            i = days.index(d)
            p = predict(model, counts, days, i, np.array([0]), False)[:, :, DEP]
            y = _blocks(counts[i], np.arange(0, NB15, 4))[:, :, DEP]
            errors.append((np.abs(p-y).sum(), np.abs(y).sum()))
        tests.append((label, np.array(errors)))
    base = tests[0][1]
    lines = ["# Pronósticos ecosim run 3", "", "## Método", "",
             "Dos Poisson LightGBM por forma y corte, parámetros fijos del run 2. "
             "Entrenamiento desde el día 29 de cada ventana (28 días previos completos). "
             "Directo toma uno de cada 12 ticks de decisión (cada 3 h): la fase "
             "(día − 2024-01-01) módulo 12 rota por día, de modo que todos los "
             "instantes de 15 min aparecen en entrenamiento. No hay azar ni ajuste "
             "de hiperparámetros. La evaluación usa todos los ticks de 15 min. "
             "MA diaria: 28 días anteriores del mismo tipo. El oráculo conoce la verdad futura: "
             "es cota ideal, no pronóstico desplegable. En evaluación cada k mide "
             "la hora móvil, con cuartos repartidos según el reloj (diaria) o según t (directa). "
             "Se rellenan con cero los cuartos posteriores a 00:30. Se excluyen "
             "del entrenamiento y la evaluación 23–31 de marzo de 2026 por CSV incompleto. "
             "El universo ordenado de 677 estaciones está en `forecasts/universe_run3.json`.",
             "", "## Auditoría del submuestreo directo", "",
             "`direct_sampling.csv` detalla, para cada corte y cada uno de los 78 ticks, "
             "los días de entrenamiento y las filas por target "
             "(días × estaciones × horizontes k válidos). Resumen por corte:", "",
             "| Corte | Filas/target total | Días/tick mín–máx | Filas/target por tick mín–máx |",
             "|---|---:|---:|---:|"]
    for name, group in sampling.groupby("corte", sort=False):
        lines.append(f"| {name} | {group.filas_por_target.sum():,} | "
                     f"{group.dias.min()}–{group.dias.max()} | "
                     f"{group.filas_por_target.min():,}–{group.filas_por_target.max():,} |")
    lines += ["", "## Exactitud por mes y k", "",
              "MAE, WAPE y sesgo (predicción menos verdad). El baseline `ma_diaria` del mismo "
              "mes figura junto a cada variante; el WAPE neto divide por |llegadas−salidas|.", ""]
    for month in sorted(acc.mes.unique()):
        lines += [f"### {month}", "", "| Variante | k | Objetivo | MAE | WAPE | Sesgo | MAE MA | WAPE MA | Sesgo MA |",
                  "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
        df = acc[acc.mes == month]
        baseline = df[df.variante == "ma_diaria"].set_index(["k", "objetivo"])
        for r in df.itertuples():
            b = baseline.loc[(r.k, r.objetivo)]
            lines.append(f"| {r.variante} | {r.k} | {r.objetivo} | {r.mae:.3f} | {r.wape:.3f} | {r.sesgo:.3f} | {b.mae:.3f} | {b.wape:.3f} | {b.sesgo:.3f} |")
        lines.append("")
    old_path = OUT_RESULTS / "accuracy_run3_before_rotation.csv"
    if old_path.exists():
        before = pd.read_csv(old_path)
        old = before[before.variante == "lgbm_directo"]
        new = acc[acc.variante == "lgbm_directo"]
        comparison = []
        for label, frame in (("antes", old), ("despues", new)):
            frame = frame.copy()
            frame["error_abs"] = frame.mae * frame.n
            frame["verdad_abs"] = frame.error_abs / frame.wape
            for (month, k, target), group in frame.groupby(["mes", "k", "objetivo"]):
                comparison.append({"mes": month, "k": k, "objetivo": target,
                                   "periodo": label, "error_abs": group.error_abs.sum(),
                                   "verdad_abs": group.verdad_abs.sum()})
            for (k, target), group in frame.groupby(["k", "objetivo"]):
                comparison.append({"mes": "TOTAL", "k": k, "objetivo": target,
                                   "periodo": label, "error_abs": group.error_abs.sum(),
                                   "verdad_abs": group.verdad_abs.sum()})
        comparison = pd.DataFrame(comparison)
        comparison["wape"] = comparison.error_abs / comparison.verdad_abs
        pivot = comparison.pivot(index=["mes", "k", "objetivo"], columns="periodo", values="wape").reset_index()
        pivot["diferencia_pp"] = 100 * (pivot["despues"] - pivot["antes"])
        pivot.to_csv(OUT_RESULTS / "accuracy_direct_comparison.csv", index=False)
        lines += ["## Efecto de rotar el submuestreo directo", "",
                  "WAPE de `lgbm_directo` antes (fase siempre cero) y después "
                  "(fase rotativa) por k. `accuracy_direct_comparison.csv` contiene "
                  "también el desglose por mes; TOTAL pondera los errores y la "
                  "verdad absoluta, no promedia WAPE mensuales.", "",
                  "| k | Objetivo | WAPE antes | WAPE después | Δ puntos porcentuales |",
                  "|---:|---|---:|---:|---:|"]
        for r in pivot[pivot.mes == "TOTAL"].itertuples():
            lines.append(f"| {r.k} | {r.objetivo} | {r.antes:.4f} | {r.despues:.4f} | {r.diferencia_pp:+.2f} |")
        lines.append("")
    lines += ["## Comprobaciones (solo 15 días de selección, lgbm_diario, salidas)", "",
              "| Entrenamiento | WAPE | Δ WAPE relativo vs hacia adelante | IC95 por bootstrap pareado |",
              "|---|---:|---:|---:|"]
    rng = np.random.default_rng(20261002)
    for label, vals in tests:
        delta = 1 - vals[:, 0].sum()/base[:, 0].sum()
        picks = rng.integers(0, len(selected), size=(2000, len(selected)))
        draws = 1 - vals[picks, 0].sum(axis=1)/base[picks, 0].sum(axis=1)
        low, high = np.quantile(draws, [.025, .975])
        lines.append(f"| {label} | {vals[:,0].sum()/vals[:,1].sum():.4f} | {delta:+.2%} | [{low:+.2%}, {high:+.2%}] |")
    # IC pareado: mismos índices del bootstrap en numerador y denominador.
    pick = np.random.default_rng(20261002).integers(0, len(selected), (2000, len(selected)))
    ci = np.quantile(1-tests[1][1][pick,0].sum(axis=1)/base[pick,0].sum(axis=1), .025)
    verdict = "usar 2024 (avisar al executor)" if 1-tests[1][1][:,0].sum()/base[:,0].sum() > .02 and ci > 0 else "mantener ventana desde 2025"
    lines += ["", f"Decisión 2024: **{verdict}**. División al azar solo es diagnóstico, nunca entrena los brazos del simulador.", ""]
    (OUT_RESULTS / "REPORT.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
