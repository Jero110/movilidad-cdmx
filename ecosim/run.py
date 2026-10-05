"""Experimento walk-forward ecosim run 3. Reanuda por (día, brazo, configuración).

    uv run python -m ecosim.run --all --procesos 8 --threads 1
    uv run python -m ecosim.run --paso 1

Nunca se ajusta un parámetro mirando prueba. Los conteos se cargan una vez por
proceso y cada archivo de resultados se guarda después de cada corrida.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import date, timedelta
from functools import lru_cache
from functools import lru_cache
import joblib
import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from ecosim import config as C, contracts as K, data, medicion, pronostico as P, sim
from ecosim.asignador import Asignador
from ecosim.days import load_days

RESULTS = C.REPO_ROOT / "ecosim" / "results"
CACHE = C.DERIVED / "run3_blocks"
RUNS = RESULTS / "corridas_run3.csv"
FINAL = RESULTS / "resultados.csv"
FROZEN = RESULTS / "frozen.json"
N_FILE = RESULTS / "n_seleccion.csv"
CURVE = RESULTS / "curvas_seleccion.csv"
STATS = RESULTS / "medicion" / "ecobici_stats.json"
POLICIES = ("oraculo_diario", "ma_diaria", "lgbm_diario", "oraculo_directo", "lgbm_directo")
ARMS = ("sin_rebalanceo", "ecobici", *POLICIES)
REAL = ("ma_diaria", "lgbm_diario", "lgbm_directo")
N_LAM = (15, 30, 60)
LAM = (10, 15, 20, 30, 45, 60)
RETIRO = 0.0  # mismo margen de seguridad para oráculo y modelo
_COUNTS = None
_MODELS = {}
_FORECASTS = {}  # por proceso: (día, brazo) → predicción ya calculada


def days(kind):
    if kind in ("seleccion", "curva"):
        return load_days(kind)
    months = ("2025-09", "2025-10", "2025-11", "2025-12", "2026-01") if kind == "prueba" else tuple(f"2026-{m:02d}" for m in range(2, 9))
    return [d for month in months for d in load_days(kind, month)]


def cap(source="base"):
    stats = json.loads(STATS.read_text())
    key = "referencia_wiki" if source in ("base", "p99") else "recalculados"
    quantile = "p99" if source == "p99" else "p95"
    return stats[key][quantile]


def spec(day, arm, tag, n=0, lam=0.0, caps="base", delivery=60, pairs="todas", damage="onsite"):
    return dict(day=day, arm=arm, tag=tag, n=int(n), lam=float(lam), caps=caps,
                delivery=int(delivery), pairs=pairs, damage=damage)


def key(s):
    return "|".join(str(s[k]) for k in ("day", "arm", "tag", "n", "lam", "caps", "delivery", "pairs", "damage"))


def _load_counts():
    global _COUNTS
    if _COUNTS is None:
        # Solo se cargan para brazos de política; los controles no necesitan
        # duplicar el parquet. El lock serializa los picos entre procesos.
        with open(C.DERIVED / "run3_count_load.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                _COUNTS = P.load_counts()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    return _COUNTS


def _model(arm, day):
    if not arm.startswith("lgbm"):
        return None
    d = date.fromisoformat(day)
    fold = next((f for f in C.FOLDS if f["test_month"] == d.strftime("%Y-%m")), None)
    if fold is None:
        raise ValueError(f"día sin corte walk-forward: {day}")
    suffix = "directo" if arm.endswith("directo") else "diario"
    path = P.OUT_MODELS / f"{fold['name']}_{fold['train_start']}_{fold['train_end']}_{suffix}.joblib"
    if path not in _MODELS:
        _MODELS[path] = joblib.load(path)
    return _MODELS[path]


def _forecast(arm, day):
    cache_key = (arm, day)
    if cache_key not in _FORECASTS:
        dd, universe, counts = _load_counts()
        if len(_FORECASTS) >= 40:
            _FORECASTS.pop(next(iter(_FORECASTS)))
        _FORECASTS[cache_key] = P.forecast(arm, counts, dd, C.as_date(day), _model(arm, day))
    return _FORECASTS[cache_key]


@lru_cache(maxsize=1)
def _moves_all():
    return pd.read_parquet(sim.MOVES_FILE)


def _measured_effort(day, pairs):
    """La regla de pares cambia solo el esfuerzo; nunca reproduce otra física."""
    moves = _moves_all()
    start, end = C.day_bounds(day)
    moves = moves[(moves.t1 >= start) & (moves.t1 < end) & (moves.delta != 0)]
    if pairs == "todas":
        moves = moves[~moves.par]
    elif pairs == "solo_1":
        moves = moves[~(moves.par & (moves.delta.abs() == 1))]
    elif pairs != "sin_regla":
        raise ValueError(pairs)
    return (int(len(moves)), int(moves.delta.abs().sum()),
            int(moves.delta.lt(0).sum()), int(moves.delta.gt(0).sum()))


def _policy_effort(applied, pairs):
    if pairs == "todas":
        return None
    if pairs not in ("sin_regla", "solo_1"):
        raise ValueError(pairs)
    ordered = applied.assign(at=np.where(applied.applied < 0, applied.pickup_at, applied.delivery_at))
    paired = set()
    for _, g in ordered.sort_values(["at", "short_name"], kind="stable").groupby("short_name"):
        for (ia, a), (ib, b) in zip(list(g.iterrows()), list(g.iloc[1:].iterrows())):
            if ia not in paired and ib not in paired and pd.Timestamp(b["at"]) - pd.Timestamp(a["at"]) == pd.Timedelta(minutes=15) and a.applied == -b.applied:
                if pairs == "sin_regla" or abs(a.applied) == 1:
                    paired.update((ia, ib))
    return len(applied)-len(paired)


def one(s):
    """Una simulación. Las variantes de pares solo cambian el esfuerzo medido
    del benchmark, no el mundo físico de la política ni su E+F."""
    start = time.perf_counter()
    day, arm = s["day"], s["arm"]
    kwargs = {"arm": arm, "damage_variant": s["damage"]}
    policy = None
    if arm == "ecobici":
        kwargs["orders"] = sim.ecobici_orders(day)
    elif arm in POLICIES:
        forecast = _forecast(arm, day)
        limit = cap(s["caps"])
        params = K.PolicyParams(n_hours=s["n"], lam=s["lam"],
                                visits_per_decision=limit["visitas_por_decision"],
                                max_bikes_per_visit=limit["bicis_por_visita"],
                                delivery_min=s["delivery"], retiro=RETIRO)
        policy = Asignador(threads=int(os.environ.get("ECOSIM_THREADS", "1")), time_limit=10)
        kwargs.update(policy=policy, forecast=forecast, params=params)
    r = sim.run_day(day, **kwargs)
    oa = r.extra["orders_applied"]
    applied = oa[oa.applied != 0]
    picked = int(-applied.loc[applied.applied < 0, "applied"].sum())
    delivered = int(applied.loc[applied.applied > 0, "applied"].sum())
    # Las entregas imposibles vuelven físicamente al origen: contabilizarlas,
    # sin confundirlas con órdenes exitosas al destino previsto.
    returned = int(sum(item[2] for item in r.extra["returns"]))
    max_visit = int(applied.applied.abs().max()) if len(applied) else 0
    max_decision = int(oa.groupby("issued_at").size().max()) if len(oa) else 0
    if arm in POLICIES:
        limit = cap(s["caps"])
        assert picked == delivered + returned and r.extra["truck_end"] == 0, (day, arm, picked, delivered, returned)
        assert max_visit <= limit["bicis_por_visita"] and max_decision <= limit["visitas_por_decision"]
    times = r.tiempos_decision
    metrics = r.metrics.copy()
    if arm == "ecobici":
        (metrics["visitas"], metrics["bicis_movidas"],
         metrics["visitas_recoger"], metrics["visitas_entregar"]) = _measured_effort(day, s["pairs"])
    elif arm in POLICIES and s["pairs"] != "todas":
        metrics["visitas"] = _policy_effort(applied, s["pairs"])
    row = {"run": key(s), **s, **metrics,
           "visitas_aplicadas_replay": r.visitas if arm == "ecobici" else 0,
           "recogidas_aplicadas": picked,
           "entregadas_aplicadas": delivered + returned, "devoluciones_origen": returned,
           "truck_end": r.extra["truck_end"],
           "replay_neto": r.extra["replay_neto"], "damage_external": r.extra["damage_external"],
           "visitas_sin_regla": r.extra["visitas_sin_regla"], "pares_visitas": r.extra["pares_visitas"],
           "max_bicis_visita": max_visit, "max_visitas_decision": max_decision,
           "decisiones_limite_10s": sum(t >= 9.99 for t in times),
           "decisiones": len(times), "tiempos_decision": json.dumps([round(t, 4) for t in times]),
           "decision_mediana_s": float(np.median(times)) if times else 0,
           "decision_p95_s": float(np.percentile(times, 95)) if times else 0,
           "decision_max_s": max(times, default=0), "fallbacks": len(policy.fallbacks) if policy else 0,
           "segundos": round(time.perf_counter()-start, 3),
           "recortes": json.dumps(r.recortes, sort_keys=True)}
    if s["tag"] == "prueba":
        CACHE.mkdir(parents=True, exist_ok=True)
        r.station_hour.to_parquet(CACHE / f"{day}_{arm}.parquet", index=False)
    return row


def _worker_init(threads):
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "POLARS_MAX_THREADS"):
        os.environ[var] = str(threads)
    os.environ["ECOSIM_THREADS"] = str(threads)
    # La carga de conteos es lazy: el control sin rebalanceo no la necesita.


def saved():
    return pd.read_csv(RUNS, dtype={"day": str, "run": str, "tag": str}) if RUNS.exists() else pd.DataFrame()


def _persist(row):
    """Amplía esquema una vez al reanudar CSV previos; después añade cada día."""
    if not RUNS.exists():
        pd.DataFrame([row]).to_csv(RUNS, index=False)
        return
    columns = list(pd.read_csv(RUNS, nrows=0).columns)
    if set(row) - set(columns):
        out = pd.concat([saved(), pd.DataFrame([row])], ignore_index=True)
        tmp = RUNS.with_suffix(".tmp")
        out.to_csv(tmp, index=False)
        os.replace(tmp, RUNS)
    else:
        pd.DataFrame([row]).reindex(columns=columns).to_csv(RUNS, mode="a", header=False, index=False)


def reconcile_benchmark():
    """Migra corridas del replay previas: esfuerzo medido, E/F sin cambio."""
    old = saved()
    if old.empty or "visitas_aplicadas_replay" in old.columns and old.loc[old.arm.eq("ecobici"), "visitas_aplicadas_replay"].notna().all():
        return
    if "visitas_aplicadas_replay" not in old:
        old["visitas_aplicadas_replay"] = np.nan
    for i, row in old[old.arm == "ecobici"].iterrows():
        if pd.notna(row.visitas_aplicadas_replay):
            continue
        visits, bikes, pickups, deliveries = _measured_effort(row.day, row.pairs)
        old.at[i, "visitas_aplicadas_replay"] = row.visitas
        old.at[i, "visitas"] = visits
        old.at[i, "bicis_movidas"] = bikes
        old.at[i, "visitas_recoger"] = pickups
        old.at[i, "visitas_entregar"] = deliveries
    tmp = RUNS.with_suffix(".tmp")
    old.to_csv(tmp, index=False)
    os.replace(tmp, RUNS)


def execute(specs, procesos=2, threads=1):
    if procesos > 2 or procesos * threads > 8:
        raise ValueError("máximo dos procesos de simulación y ocho threads en total")
    ids = [key(s) for s in specs]
    if len(set(ids)) != len(ids):
        raise ValueError("especificaciones duplicadas")
    previous = saved()
    done = set(previous.run) if len(previous) else set()
    todo = [s for s in specs if key(s) not in done]
    print(f"[{specs[0]['tag'] if specs else '-'}] {len(specs)} corridas; {len(todo)} nuevas; {procesos} procesos × {threads} threads", flush=True)
    if todo:
        # Cola acotada: al fallar no quedan cientos de días ya enviados al pool.
        errors = RESULTS / "errores_run3.log"
        pool = ProcessPoolExecutor(max_workers=procesos, initializer=_worker_init, initargs=(threads,))
        pending = {}
        source = iter(todo)
        finished = failed = 0
        elapsed = time.perf_counter()
        def fill():
            while len(pending) < 2 * procesos:
                s = next(source, None)
                if s is None:
                    break
                pending[pool.submit(one, s)] = s
        try:
            fill()
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    s = pending.pop(future)
                    finished += 1
                    try:
                        row = future.result()
                    except Exception:
                        failed += 1
                        with errors.open("a") as out:
                            out.write(json.dumps(s, ensure_ascii=False) + "\n" + traceback.format_exc() + "\n")
                        print(f"ERROR {finished}/{len(todo)}: {key(s)} (ver {errors})", flush=True)
                    else:
                        _persist(row)
                    if finished % 5 == 0 or finished == len(todo) or failed:
                        avg = (time.perf_counter()-elapsed)/finished
                        print(f"avance {finished}/{len(todo)}, fallas {failed}, {avg:.1f}s/corrida", flush=True)
                # Tras tres fallos (o tres éxitos), las causas se hacen visibles
                # inmediatamente; no quemar CPU si un contrato está roto.
                if failed >= 3:
                    raise RuntimeError(f"{failed} corridas fallaron; ver {errors}")
                fill()
            if failed:
                raise RuntimeError(f"{failed} corridas fallaron; ver {errors}")
        finally:
            for future in pending:
                future.cancel()
            pool.shutdown(wait=True, cancel_futures=True)
    result = saved().set_index("run")
    return result.loc[ids].reset_index()


def paired(x, y):
    """Media e IC t95 de diferencias pareadas por día; empates aparte."""
    x, y = x.align(y, join="inner")
    diff = x-y
    n = len(diff)
    if n < 1:
        return (float("nan"), float("nan"), float("nan"), 0, 0)
    center = float(diff.mean())
    margin = float(student_t.ppf(.975, n-1)*diff.std(ddof=1)/np.sqrt(n)) if n > 1 else float("nan")
    return center, center-margin, center+margin, int((diff < 0).sum()), int((diff == 0).sum())


def frozen():
    return json.loads(FROZEN.read_text())


def save_frozen(x):
    FROZEN.write_text(json.dumps(x, indent=2, ensure_ascii=False) + "\n")


def paso1(procesos, threads):
    selection = days("seleccion")
    # n=1: por construcción no hay ventana posterior a la entrega.
    base = execute([spec(d, "sin_rebalanceo", "n1_base") for d in selection], procesos, threads)
    specs = [spec(d, arm, "n", n, lam) for arm in ("oraculo_diario", "oraculo_directo")
             for n in range(2, 7) for lam in N_LAM for d in selection]
    rows = execute(specs, procesos, threads)
    virtual = []
    for arm in ("oraculo_diario", "oraculo_directo"):
        for lam in N_LAM:
            for _, b in base.iterrows():
                virtual.append({"day": b.day, "arm": arm, "n": 1, "lam": lam,
                                "E": b.E, "F": b.F, "EF": b.EF, "visitas": 0, "n1_sin_resolver": True})
    rows = pd.concat([rows.assign(n1_sin_resolver=False), pd.DataFrame(virtual)], ignore_index=True)
    output = []
    for (arm, lam, n), g in rows.groupby(["arm", "lam", "n"]):
        current = g.set_index("day").EF
        next_n = rows[(rows.arm == arm) & (rows.lam == lam) & (rows.n == n+1)].set_index("day").EF
        mean, lo, hi, wins, ties = paired(next_n, current) if len(next_n) else (np.nan,)*3+(0, 0)
        output.append({"arm": arm, "lam": lam, "n": n, "EF": float(current.mean()),
                       "visitas": float(g.visitas.mean()), "mejora_n_siguiente": -mean,
                       "IC95_mejora_inf": -hi, "IC95_mejora_sup": -lo,
                       "gana_dias_n_siguiente": wins, "empata_dias": ties, "n1_sin_resolver": n == 1})
    table = pd.DataFrame(output).sort_values(["arm", "lam", "n"])
    table.to_csv(N_FILE, index=False)
    choice, all_ci = choose_n(rows)
    table["elegido"] = [choice["directa" if a.endswith("directo") else "diaria"] == n
                         for a, n in zip(table.arm, table.n)]
    table.to_csv(N_FILE, index=False)
    save_frozen({"seleccion": selection, "n": choice, "n_ci_promedio_lambda": all_ci,
                 "n_regla": "primera n sin mejora distinguible: IC95 t pareado por día de la media sobre las tres λ incluye 0; si el IC95 indica empeoramiento, también se detiene por dominancia", "retiro": RETIRO,
                 "topes_base": cap(), "topes_p99": cap("p99"), "topes_hacia_adelante": cap("adelante"),
                 "procesos": procesos, "threads": threads})
    return table


def _n(arm, fz):
    return fz["n"]["directa" if arm.endswith("directo") else "diaria"]


def choose_n(rows):
    """Cada día pesa una vez: media E+F sobre λ=15,30,60, luego IC pareado."""
    choices, summary = {}, {}
    for arm in ("oraculo_diario", "oraculo_directo"):
        g = rows[rows.arm == arm].groupby(["day", "n"], as_index=False).EF.mean()
        pivot = g.pivot(index="day", columns="n", values="EF")
        ci = {}
        chosen = 6
        for n in range(1, 6):
            change, lo, hi, wins, ties = paired(pivot[n+1], pivot[n])
            ci[str(n)] = {"delta_siguiente_menos_actual": change, "IC95_inf": lo, "IC95_sup": hi,
                          "gana_siguiente_dias": wins, "empata_dias": ties}
            if chosen == 6 and hi >= 0:  # empate o empeoramiento; no hay mejora demostrable
                chosen = n
        choices["diaria" if arm.endswith("diario") else "directa"] = chosen
        summary[arm] = ci
    return choices, summary


def paso2(procesos, threads):
    fz = frozen(); sel = days("seleccion")
    eco = execute([spec(d, "ecobici", "eco_seleccion") for d in sel], procesos, threads)
    target = float(eco.visitas.mean())
    target_ef = float(eco.EF.mean())
    grid = execute([spec(d, arm, "curva_sel", _n(arm, fz), lam) for arm in POLICIES for lam in LAM for d in sel], procesos, threads)
    grid.to_csv(CURVE, index=False)
    picks = {}
    for arm in POLICIES:
        curve = grid[grid.arm == arm].groupby("lam", as_index=False)[["visitas", "EF"]].mean().sort_values("lam")
        # Interpolación y confirmación; si discreción del MILP deja >3%,
        # agregar el punto medido y repetir hasta cuatro veces sin mirar prueba.
        points = curve[["lam", "visitas"]].copy()
        if curve.visitas.max() < .97*target:
            attempts = [10.0]
        else:
            attempts = []
        for attempt in range(4):
            if not attempts:
                options = []
                for a, b in zip(points.sort_values("lam").iloc[:-1].itertuples(),
                                points.sort_values("lam").iloc[1:].itertuples()):
                    if min(a.visitas, b.visitas) <= target <= max(a.visitas, b.visitas) and a.visitas != b.visitas:
                        options.append(float(a.lam+(target-a.visitas)*(b.lam-a.lam)/(b.visitas-a.visitas)))
                if options:
                    candidate = options[0]
                elif points.visitas.min() > target:
                    last = points.sort_values("lam").iloc[-2:]
                    a, b = last.iloc[0], last.iloc[1]
                    slope = max((a.visitas-b.visitas)/(b.lam-a.lam), .01)
                    candidate = b.lam + max(5, (b.visitas-target)/slope)
                else:
                    candidate = float(points.loc[(points.visitas-target).abs().idxmin(), "lam"])
                attempts.append(round(max(10, min(300, candidate)), 4))
            lam = attempts[-1]
            confirm = execute([spec(d, arm, "confirm", _n(arm, fz), lam) for d in sel], procesos, threads)
            actual = float(confirm.visitas.mean())
            if abs(actual/target-1) <= .03 or lam == 10 and actual < .97*target:
                break
            points = pd.concat([points, pd.DataFrame([{"lam":lam,"visitas":actual}])], ignore_index=True)
            points = points.drop_duplicates("lam", keep="last")
            options = []
            for a, b in zip(points.sort_values("lam").iloc[:-1].itertuples(),
                            points.sort_values("lam").iloc[1:].itertuples()):
                if min(a.visitas, b.visitas) <= target <= max(a.visitas, b.visitas) and a.visitas != b.visitas:
                    options.append(round(float(a.lam+(target-a.visitas)*(b.lam-a.lam)/(b.visitas-a.visitas)), 4))
            next_lam = next((p for p in options if p not in attempts), None)
            if next_lam is None and points.visitas.min() > target:
                a, b = points.sort_values("lam").iloc[-2:].itertuples(index=False)
                slope = max((a.visitas-b.visitas)/(b.lam-a.lam), .01)
                next_lam = round(min(300, b.lam + max(5, (b.visitas-target)/slope)), 4)
            if next_lam is None or next_lam in attempts:
                break
            attempts.append(next_lam)
        # La meta E+F de Ecobici queda fuera de la rejilla principal si todas
        # las políticas la superan ampliamente; extender solo para leer el
        # esfuerzo al mismo resultado, sin usarlo para elegir n ni λ operativa.
        result_curve = curve.copy()
        extension = []
        if result_curve.EF.max() < target_ef:
            for extra_lam in (90, 120, 180, 240, 300):
                extra = execute([spec(d, arm, "igual_resultado", _n(arm, fz), extra_lam)
                                 for d in sel], procesos, threads)
                extension.append(extra_lam)
                result_curve = pd.concat([result_curve, pd.DataFrame([{
                    "lam":extra_lam,"visitas":extra.visitas.mean(),"EF":extra.EF.mean()}])], ignore_index=True)
                if extra.EF.mean() >= target_ef:
                    break
        same_result = float("nan")
        for a, b in zip(result_curve.sort_values("lam").iloc[:-1].itertuples(),
                        result_curve.sort_values("lam").iloc[1:].itertuples()):
            if min(a.EF, b.EF) <= target_ef <= max(a.EF, b.EF) and a.EF != b.EF:
                same_result = float(a.visitas+(target_ef-a.EF)*(b.visitas-a.visitas)/(b.EF-a.EF))
                break
        picks[arm] = {"lambda": lam, "visitas_confirmadas": actual, "visitas_ecobici": target,
                      "EF_confirmado": float(confirm.EF.mean()), "diferencia_pct": 100*(actual/target-1),
                      "confirmaciones": attempts, "tipo_ajuste": "extrapolación >60" if lam > 60 else "interpolación rejilla",
                      "lectura_igual_resultado_lambda_extra": extension,
                      "igual_esfuerzo_3pct": bool(abs(actual/target-1) <= .03),
                      "visitas_igual_resultado": same_result,
                      "visitas_menos_igual_resultado_pct": 100*(1-same_result/target) if np.isfinite(same_result) else None,
                      "no_alcanza_con_10": bool(lam == 10 and actual < .97*target)}
    fz["lambda_por_brazo"] = picks
    fz["mejor_real"] = min(REAL, key=lambda a: picks[a]["EF_confirmado"])
    fz["cortes"] = [{k: str(v) for k, v in fold.items()} for fold in C.FOLDS]
    fz["configuracion"] = {"intervalo_decision_min": C.STEP_MIN,
                             "recogida_min": C.PICKUP_MIN, "entrega_min": C.DELIVERY_MIN,
                             "lambda_rejilla": list(LAM), "n_rejilla": list(C.N_GRID),
                             "modelo_directorio": str(P.OUT_MODELS), "conteos": str(C.TRIPS_PARQUET),
                             "movimientos": str(sim.MOVES_FILE), "danadas": str(sim.DAMAGE_EVENTS_FILE)}
    save_frozen(fz)
    return grid


def base_spec(d, arm, tag="prueba"):
    fz = frozen()
    return spec(d, arm, tag, _n(arm, fz), fz["lambda_por_brazo"][arm]["lambda"])


def paso3(procesos, threads):
    ds = days("prueba")
    rows = execute([spec(d, arm, "prueba") if arm not in POLICIES else base_spec(d, arm)
                    for d in ds for arm in ARMS], procesos, threads)
    if len(rows) != len(ds)*7 or rows.duplicated(["day", "arm"]).any():
        raise AssertionError("faltan días/brazos")
    rows.to_csv(FINAL, index=False)
    check_oracles(rows)
    validate_v1(rows)
    where_fails(rows)
    tablas()
    return rows


def check_oracles(rows):
    for real, oracle in (("ma_diaria", "oraculo_diario"), ("lgbm_diario", "oraculo_diario"), ("lgbm_directo", "oraculo_directo")):
        a = rows[rows.arm == real].set_index("day").EF
        b = rows[rows.arm == oracle].set_index("day").EF
        # Señal solicitada de posible bug; el informe conserva la observación.
        if a.mean() < b.mean():
            raise RuntimeError(f"ALTO: {real} mejor que {oracle} (E+F medio {a.mean():.0f} < {b.mean():.0f})")


def validate_v1(rows):
    """V1: replay contra escalones observados del feed en quince días de prueba."""
    out = []
    for day in days("prueba")[:15]:
        snaps = medicion.state_time(data.snapshots(day))
        observed = medicion.observed_ef(snaps, day).iloc[0]
        replay = rows[(rows.day == day) & (rows.arm == "ecobici")].iloc[0]
        out.append({"day": day, "E_obs": observed.E, "F_obs": observed.F,
                    "E_replay": replay.E, "F_replay": replay.F,
                    "rel_E": replay.E/observed.E-1 if observed.E else np.nan,
                    "rel_F": replay.F/observed.F-1 if observed.F else np.nan,
                    "blank_min": observed.blank_min, "unobs_min": observed.unobs_min})
    pd.DataFrame(out).to_csv(RESULTS / "v1_run3.csv", index=False)


def where_fails(rows):
    """Top estaciones × hora, en el mejor brazo real y su oráculo gemelo."""
    fz = frozen()
    arm = fz["mejor_real"]
    oracle = "oraculo_directo" if arm.endswith("directo") else "oraculo_diario"
    frames = []
    for day in days("prueba"):
        for variant in (arm, oracle):
            path = CACHE / f"{day}_{variant}.parquet"
            if not path.exists():
                raise FileNotFoundError(path)
            frame = pd.read_parquet(path)
            frame["day"] = day
            frame["arm"] = variant
            frames.append(frame)
    g = pd.concat(frames, ignore_index=True)
    by = g.groupby(["arm", "short_name", "hour"], as_index=False)[["E", "F"]].sum()
    by["EF"] = by.E + by.F
    top = by[by.arm == arm].nlargest(20, "EF")
    top = top.merge(by[by.arm == oracle][["short_name", "hour", "EF"]],
                    on=["short_name", "hour"], suffixes=("_real", "_oracle"))
    top.to_csv(RESULTS / "donde_falla.csv", index=False)
    by.to_csv(RESULTS / "ef_por_bloque.csv", index=False)


def paso4(procesos, threads):
    fz = frozen()
    specs = []
    for arm in POLICIES:
        lam = fz["lambda_por_brazo"][arm]["lambda"]
        lower = max((x for x in LAM if x < lam), default=max(10, lam-5))
        upper = min((x for x in LAM if x > lam), default=lam+(LAM[-1]-LAM[-2]))
        grid = sorted(set((round(float(lower), 4), lam, round(float(upper), 4))))
        specs += [spec(d, arm, "curva_prueba", _n(arm, fz), price) for d in days("curva") for price in grid]
    return execute(specs, procesos, threads)


def paso5(procesos, threads):
    fz = frozen(); selected = ("oraculo_directo", fz["mejor_real"])
    variants = (("p99", {"caps":"p99"}), ("hacia_adelante", {"caps":"adelante"}),
                ("entrega_45", {"delivery":45}), ("entrega_75", {"delivery":75}),
                ("pares_sin_regla", {"pairs":"sin_regla"}), ("pares_solo_1", {"pairs":"solo_1"}),
                ("danadas_feed", {"damage":"feed"}))
    specs = []
    for d in days("curva"):
        for label, override in variants:
            for arm in selected:
                p = base_spec(d, arm, "sens_"+label)
                p.update(override)
                specs.append(p)
            if label.startswith("pares"):
                specs.append(spec(d, "ecobici", "sens_"+label, pairs=override["pairs"]))
    return execute(specs, procesos, threads)


def paso6(procesos, threads):
    ds = [d for d in days("prod_2026") if not "2026-03-23" <= d <= "2026-03-31"]
    assert all(not "2026-03-23" <= d <= "2026-03-31" for d in ds)
    apr = []
    universe = len(json.loads((P.OUT_FORECASTS / "universe_run3.json").read_text()))
    for day in load_days("prod_2026", "2026-04"):
        d = date.fromisoformat(day)
        lag7 = d - timedelta(days=7)
        lag14 = d - timedelta(days=14)
        missing7 = date(2026,3,23) <= lag7 <= date(2026,3,31)
        missing14 = date(2026,3,23) <= lag14 <= date(2026,3,31)
        apr.append({"day": day, "rezago_7_cero": missing7, "rezago_14_cero": missing14,
                    "bloques_estacion_afectados": (int(missing7)+int(missing14))*P.NB15*universe})
    pd.DataFrame(apr).to_csv(RESULTS / "rezagos_abril.csv", index=False)
    rows = execute([spec(d, "sin_rebalanceo", "prod_2026") if arm == "sin_rebalanceo" else base_spec(d, arm, "prod_2026")
                    for d in ds for arm in ("sin_rebalanceo", *POLICIES)], procesos, threads)
    check_oracles(rows)
    return rows


def _md(df):
    """Tabla Markdown sin dependencia opcional tabulate."""
    def cell(value):
        if pd.isna(value):
            return "—"
        if isinstance(value, (float, np.floating)):
            return f"{value:,.2f}"
        return str(value).replace("|", "\\|").replace("\n", " ")
    names = list(df.columns)
    return "\n".join(["| " + " | ".join(names) + " |", "|" + "---|"*len(names)] +
                     ["| " + " | ".join(cell(value) for value in row) + " |" for row in df.itertuples(index=False, name=None)])


def tablas():
    if not FINAL.exists() or not FROZEN.exists():
        return
    fz = frozen()
    if "lambda_por_brazo" not in fz:
        return  # --all en curso: prueba previa no debe contaminar selección
    df = pd.read_csv(FINAL)
    lines = ["# Run 3: tablas reproducibles", "", "E/F = minutos-estación/día; IC95 t pareado por día, diferencia brazo − Ecobici. Empates separados.", ""]
    if N_FILE.exists():
        lines += ["## Horizonte n (15 días de selección)", "",
                  "`n=1` equivale a sin rebalanceo por entrega a t+60. Regla sobre la media diaria de las tres λ: parar si n+1 no mejora significativamente o empeora.",
                  "", _md(pd.read_csv(N_FILE)), ""]
    summary = []
    for mes, group in list(df.groupby(df.day.str[:7])) + [("TOTAL", df)]:
        eco = group[group.arm == "ecobici"].set_index("day")
        for arm, a in group.groupby("arm"):
            a = a.set_index("day")
            delta, lo, hi, wins, ties = paired(a.EF, eco.EF)
            summary.append({"mes": mes, "brazo": arm, "días": len(a), "E": a.E.mean(), "F": a.F.mean(),
                            "E+F": a.EF.mean(), "visitas": a.visitas.mean(), "bicis_movidas": a.bicis_movidas.mean(),
                            "bicis_por_visita": a.bicis_movidas.sum()/a.visitas.sum() if a.visitas.sum() else 0,
                            "desvios": (a.desvios_salida+a.desvios_llegada).mean(), "km_desvio": a.km_desvio_medio.mean(),
                            "IC95_diff_inf": lo, "IC95_diff_sup": hi, "gana_dias": wins, "empata_dias": ties,
                            "replay_neto": a.replay_neto.mean()})
    table = pd.DataFrame(summary)
    lines += ["## Siete brazos por mes y total", "", _md(table), "", "## Tres lecturas y tiempos", ""]
    v1 = RESULTS / "v1_run3.csv"
    if v1.exists():
        v = pd.read_csv(v1)
        lines += ["## V1: replay Ecobici contra GBFS (15 días)", "",
                  _md(v[["E_obs", "F_obs", "E_replay", "F_replay", "rel_E", "rel_F", "blank_min", "unobs_min"]].mean().to_frame().T), ""]
    falla = RESULTS / "donde_falla.csv"
    if falla.exists():
        lines += ["## Dónde falla (estación × hora, todos los días)", "", _md(pd.read_csv(falla)), ""]
    readings=[]
    for arm in POLICIES:
        a = df[df.arm == arm].set_index("day"); eco = df[df.arm == "ecobici"].set_index("day")
        delta, lo, hi, wins, ties = paired(a.EF, eco.EF)
        readings.append({"brazo":arm,"E+F_menos_pct": -100*delta/eco.EF.mean(), "IC95_pct_inf":-100*hi/eco.EF.mean(),
                         "IC95_pct_sup":-100*lo/eco.EF.mean(),"gana":wins,"empata":ties,
                         "visitas_pct":100*(a.visitas.mean()/eco.visitas.mean()-1),
                         "visitas_menos_igual_resultado_pct":fz["lambda_por_brazo"][arm]["visitas_menos_igual_resultado_pct"],
                         "minutos_por_visita_lambda":fz["lambda_por_brazo"][arm]["lambda"],
                         "mediana_decision_s":a.decision_mediana_s.median(),"p95_decision_s":a.decision_p95_s.quantile(.95),
                         "max_decision_s":a.decision_max_s.max(),"limite_10s":a.decisiones_limite_10s.sum()})
    lines += [_md(pd.DataFrame(readings)), "", "## Tiempos por decisión en estados reales, brazo × λ", ""]
    timing = []
    raw = saved()
    if len(raw):
        for (arm, lam), g in raw[raw.arm.isin(POLICIES)].groupby(["arm", "lam"]):
            values = [t for entry in g.tiempos_decision.dropna()
                      for t in json.loads(entry)]
            if values:
                timing.append({"brazo": arm, "lambda":lam, "decisiones":len(values),
                               "mediana_s":np.median(values), "p95_s":np.percentile(values,95),
                               "max_s":max(values), "limite_10s":sum(t >= 9.99 for t in values)})
    if timing:
        lines += [_md(pd.DataFrame(timing)), ""]
    lines += ["## Brechas pareadas (modelo − oráculo; directo − diario)", ""]
    gaps = []
    for mes, group in list(df.groupby(df.day.str[:7])) + [("TOTAL", df)]:
        for a, b in (("ma_diaria", "oraculo_diario"), ("lgbm_diario", "oraculo_diario"),
                     ("lgbm_directo", "oraculo_directo"), ("oraculo_directo", "oraculo_diario")):
            left = group[group.arm == a].set_index("day").EF
            right = group[group.arm == b].set_index("day").EF
            value, lo, hi, wins, ties = paired(left, right)
            gaps.append({"mes":mes,"a":a,"b":b,"delta_EF":value,"IC95_inf":lo,"IC95_sup":hi,
                         "gana_dias":wins,"empata_dias":ties})
    lines += [_md(pd.DataFrame(gaps)), "", "## Evolución de topes Ecobici", ""]
    stats = json.loads(STATS.read_text())
    lines += [_md(pd.DataFrame(stats["meses"])[["mes","ventana","visitas_p95","bicis_p95","visitas_p99","bicis_p99"]]), ""]
    for tag, title in (("curva_prueba", "Curvas fuera de selección"), ("sens_", "Sensibilidades"), ("prod_2026", "Producción 2026 sin Ecobici")):
        raw = saved()
        rows = raw[raw.tag.str.startswith(tag)] if len(raw) else pd.DataFrame()
        if len(rows):
            agg = rows.groupby([rows.day.str[:7], "tag", "arm", "lam"], as_index=False, dropna=False)[["EF", "visitas", "damage_external"]].mean()
            lines += ["## "+title, "", _md(agg), ""]
            if tag == "sens_":
                paired_sens = []
                for (variant, arm), group in rows.groupby(["tag", "arm"]):
                    base = df[df.arm == arm].set_index("day")
                    g = group.set_index("day")
                    change, low, high, wins, ties = paired(g.EF, base.EF)
                    paired_sens.append({"variante":variant,"brazo":arm,"EF":g.EF.mean(),
                                        "visitas":g.visitas.mean(),"damage_external":g.damage_external.mean(),
                                        "delta_EF_base":change,"IC95_inf":low,"IC95_sup":high,
                                        "gana_dias":wins,"empata_dias":ties})
                lines += ["### Sensibilidad frente al mismo brazo, mismos 32 días", "",
                          _md(pd.DataFrame(paired_sens)), ""]
    prod = saved()
    prod = prod[prod.tag == "prod_2026"] if len(prod) else pd.DataFrame()
    if len(prod):
        comparison = []
        all_rows = pd.concat([df, prod], ignore_index=True)
        all_rows["mes"] = all_rows.day.str[:7]
        for month, group in all_rows.groupby("mes"):
            for model, oracle in (("ma_diaria", "oraculo_diario"),
                                  ("lgbm_diario", "oraculo_diario"),
                                  ("lgbm_directo", "oraculo_directo")):
                a = group[group.arm == model].set_index("day").EF
                b = group[group.arm == oracle].set_index("day").EF
                value, lo, hi, wins, ties = paired(a, b)
                comparison.append({"mes":month,"modelo":model,"costo_error":value,
                                   "IC95_inf":lo,"IC95_sup":hi,"gana_dias":wins,"empata_dias":ties})
        lines += ["## Costo del error por mes: 2025 frente a 2026", "",
                  _md(pd.DataFrame(comparison)), ""]
    # Pronóstico al lado del resultado; k=0, neto.
    acc = P.OUT_RESULTS / "accuracy_run3.csv"
    if acc.exists():
        a = pd.read_csv(acc).query("k == 0 and objetivo == 'neto'")
        lines += ["## Exactitud del pronóstico (neto, k=0)", "", _md(a[["mes","variante","mae","wape"]]), ""]
    lag = RESULTS / "rezagos_abril.csv"
    if lag.exists():
        lines += ["## Abril 2026: rezagos afectados por marzo incompleto", "", _md(pd.read_csv(lag)), ""]
    eff = RESULTS / "eficiencia_run3.csv"
    if eff.exists():
        lines += ["## Recursos y duración por paso", "", _md(pd.read_csv(eff)), ""]
    (RESULTS / "tablas.md").write_text("\n".join(lines).rstrip()+"\n")
    eco=df[df.arm=="ecobici"]; best=df[df.arm==fz["mejor_real"]]
    eco_ef=eco.EF.mean(); real_ef=best.EF.mean()
    text=["# Conclusiones — ecosim run 3", "", "Resultados dentro del simulador, no promesa operacional ni rutas verificadas.", "",
          f"El mejor brazo real elegido en agosto fue **{fz['mejor_real']}**. En la prueba, Ecobici obtuvo {eco_ef:,.0f} minutos-estación vacíos o llenos por día; el brazo real, {real_ef:,.0f} ({100*(1-real_ef/eco_ef):.1f}% menos).",
          "", "## Brazos con esfuerzo equiparado en selección (no en prueba)", "", _md(pd.DataFrame(readings)[["brazo","E+F_menos_pct","IC95_pct_inf","IC95_pct_sup","gana","empata","visitas_pct"]]),
          "", f"El esfuerzo se ajustó en los 15 días de selección, no en los meses de prueba. En prueba {fz['mejor_real']} hizo {100*(1-best.visitas.mean()/eco.visitas.mean()):.1f}% menos visitas que Ecobici, así que estos resultados no son estrictamente a igual esfuerzo fuera de muestra. Cuando λ=60 aún movía más que Ecobici se extrapoló por encima de la rejilla y se confirmó con otra corrida; `frozen.json` conserva intentos y error residual. La tabla muestra las visitas fuera de muestra.",
          f"El replay de Ecobici tiene neto aplicado medio {eco.replay_neto.mean():+.1f} bicis/día. Si es positivo, incorpora bicicletas externas dentro de la ventana y favorece a Ecobici frente a las políticas cerradas.", "",
          "## Pronóstico y actualización", ""]
    for model, oracle in (("ma_diaria","oraculo_diario"),("lgbm_diario","oraculo_diario"),("lgbm_directo","oraculo_directo")):
        a=df[df.arm==model].set_index("day").EF; b=df[df.arm==oracle].set_index("day").EF
        dif,lo,hi,w,t=paired(a,b)
        text.append(f"{model} menos {oracle}: costo del error {dif:,.0f} min/día (IC95 [{lo:,.0f}, {hi:,.0f}]); {w} días favorecen al modelo, {t} empatan.")
    a=df[df.arm=="oraculo_directo"].set_index("day").EF; b=df[df.arm=="oraculo_diario"].set_index("day").EF
    dif,lo,hi,w,t=paired(a,b)
    text += [f"Actualización en el oráculo (directo − diario): {dif:,.0f} min/día, IC95 [{lo:,.0f}, {hi:,.0f}].", "", "## Diciembre y enero", ""]
    for month in ("2025-12", "2026-01"):
        m = df[df.day.str.startswith(month)]
        a = m[m.arm == fz["mejor_real"]].set_index("day").EF
        b = m[m.arm == "ecobici"].set_index("day").EF
        delta, low, high, wins, ties = paired(a, b)
        text.append(f"{month}: {100*(1-a.mean()/b.mean()):.1f}% menos E+F que Ecobici; IC95 de diferencia [{low:,.0f}, {high:,.0f}] min/día, gana {wins}/{len(a)} días, empata {ties}.")
    caps_month = pd.DataFrame(stats["meses"])
    caps_month = caps_month[caps_month.ventana == "05:00"]
    text += ["", "## Topes: referencia y enero–agosto", "",
             "Caso base 67 visitas/decisión y 14 bicis/visita (referencia sep–nov); hacia adelante 47 y 19 (p95 ene–ago). Sensibilidades p99 83/24 y 62/33, respectivamente.",
             "", _md(caps_month[["mes","visitas_p95","bicis_p95","visitas_p99","bicis_p99"]]), "",
             "## Por mes y limitaciones", "", _md(table[table.brazo.isin(("ecobici",fz["mejor_real"]))][["mes","brazo","E+F","visitas","replay_neto"]]), "",
             "La variante de dañadas que sigue el feed aplica flujos externos distintos entre brazos tras recortes físicos: `damage_external` se publica junto a E+F; no aisla causalmente el efecto de dañadas.",
             "Marzo de 2026: 23–31 excluidos como verdad. De los ocho días de abril de `prod_2026`, siete tienen por lo menos un rezago 7/14 días artificialmente a cero; el 6 de abril tiene ambos. `rezagos_abril.csv` cuenta los bloques-estación afectados; no imputa viajes ni cuantifica un contrafactual inexistente.",
             "", "## Comparación con runs anteriores", "", "Run 1 fijaba dañadas y run 2 permitía bodega neta y entrega simultánea; aquí dañadas cambian en sitio, cada decisión equilibra bicis y entrega a +60 min. Por ello los porcentajes de runs 1 y 2 no son directamente comparables; no se reemplazan sus cifras históricas.", ""]
    sensitivity = saved()
    sensitivity = sensitivity[sensitivity.tag.str.startswith("sens_")] if len(sensitivity) else pd.DataFrame()
    if len(sensitivity):
        text += ["", "## Sensibilidades en los 32 días de curva", ""]
        for arm in ("oraculo_directo", fz["mejor_real"]):
            base = df[(df.arm == arm) & df.day.isin(days("curva"))].set_index("day")
            for label in ("p99", "hacia_adelante", "entrega_45", "entrega_75", "danadas_feed"):
                g = sensitivity[(sensitivity.arm == arm) & (sensitivity.tag == "sens_"+label)].set_index("day")
                if len(g):
                    dif, low, high, wins, ties = paired(g.EF, base.EF)
                    text.append(f"{arm}, {label}: {g.EF.mean():,.0f} E+F/día; Δ vs 67/14 y entrega +60 = {dif:+,.0f} (IC95 [{low:,.0f}, {high:,.0f}]); flujo externo de dañadas {g.damage_external.mean():+,.1f} bicis/día.")
    if len(prod):
        text += ["", "## 2026: solo contra oráculos y sin rebalanceo", "",
                 "El feed de 2026 no permite medir el esfuerzo/E+F de Ecobici; aquí no se simula ese brazo."]
        for month, g in prod.groupby(prod.day.str[:7]):
            for model, oracle in (("ma_diaria", "oraculo_diario"),
                                  ("lgbm_diario", "oraculo_diario"),
                                  ("lgbm_directo", "oraculo_directo")):
                left = g[g.arm == model].set_index("day").EF
                right = g[g.arm == oracle].set_index("day").EF
                value, low, high, wins, ties = paired(left, right)
                text.append(f"{month}, {model} − {oracle}: {value:,.0f} min/día (IC95 [{low:,.0f}, {high:,.0f}]); referencia 2025 en tablas.md.")
    (RESULTS / "CONCLUSIONES.md").write_text("\n".join(text)+"\n")


def main(argv=None):
    parser = argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--paso", type=int, choices=range(0,7))
    parser.add_argument("--procesos", type=int, default=2)
    parser.add_argument("--threads", type=int, default=1)
    args=parser.parse_args(argv)
    if args.procesos < 1 or args.procesos > 2 or args.threads < 1 or args.procesos*args.threads > 8:
        parser.error("máximo dos procesos; procesos × threads debe estar entre 1 y 8")
    RESULTS.mkdir(exist_ok=True)
    reconcile_benchmark()
    steps=range(1,7) if args.all else [args.paso]
    step_functions = {1: paso1, 2: paso2, 3: paso3, 4: paso4, 5: paso5, 6: paso6}
    for step in steps:
        if step == 0:
            print("Paso 0: resultados de run 2 archivados en ecosim/results/run2")
            continue
        start=time.perf_counter()
        print(f"Paso {step}, {args.procesos} procesos × {args.threads} threads", flush=True)
        step_functions[step](args.procesos,args.threads)
        minutes = (time.perf_counter()-start)/60
        efficiency = RESULTS / "eficiencia_run3.csv"
        pd.DataFrame([{"paso":step,"procesos":args.procesos,"threads_por_proceso":args.threads,
                       "duracion_min":round(minutes,2),"corridas_guardadas":len(saved())}]).to_csv(
            efficiency, mode="a", header=not efficiency.exists(), index=False)
        if step >= 3:
            tablas()
        print(f"Paso {step}: {minutes:.1f} min", flush=True)


if __name__ == "__main__":
    main()
