"""Medición run 3 de Ecobici: total anclado menos flujo de viajes.

Los tiempos del feed son commit − 30 s. Los movimientos (incluidos los pares)
se conservan en parquet; para el esfuerzo principal se filtra ``~par``.
Uso: uv run python -m ecosim.medicion
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ecosim import config as C, contracts as K, data

RESULTS = C.REPO_ROOT / "ecosim/results/medicion"
MOVES_PARQUET = C.DERIVED / "ecobici_moves.parquet"
DAMAGE_PARQUET = C.DERIVED / "damage_events.parquet"


def state_time(snaps: pd.DataFrame, lag_s: float = C.GBFS_COMMIT_LAG_S) -> pd.DataFrame:
    """Conserva el commit crudo y asigna la hora estimada del estado."""
    return snaps.assign(t_commit=snaps["t"], t=snaps["t"] - pd.Timedelta(seconds=lag_s))


def _undo_starts(iv: pd.DataFrame, max_size: int | None = None) -> pd.Series:
    """Empareja de izquierda a derecha sin traslapes; no cruza blancos."""
    d = iv["delta"].where(~iv["blank_touch"], 0)
    keys = [iv["short_name"]] if "day" not in iv else [iv["day"], iv["short_name"]]
    nxt = d.groupby(keys, sort=False).shift(-1)
    # También se prohíbe el segundo intervalo si toca un blanco.
    clean_next = (~iv["blank_touch"]).groupby(keys, sort=False).shift(-1, fill_value=False)
    cand = d.ne(0) & nxt.eq(-d) & clean_next
    if max_size is not None:
        cand &= d.abs().le(max_size)
    new_station = iv["short_name"].ne(iv["short_name"].shift())
    if "day" in iv:
        new_station |= iv["day"].ne(iv["day"].shift())
    run = (~cand | new_station).cumsum()
    pos = cand.astype("int64").groupby(run).cumsum() - 1
    return cand & pos.mod(2).eq(0)


def _undo_both(iv: pd.DataFrame, starts: pd.Series) -> pd.Series:
    keys = [iv["short_name"]] if "day" not in iv else [iv["day"], iv["short_name"]]
    return starts | starts.groupby(keys, sort=False).shift(1, fill_value=False)


def intervals(snaps: pd.DataFrame, trips: pd.DataFrame, day, start_hm: str = "05:00") -> pd.DataFrame:
    """Intervalos de fotos consecutivas; t0 < viaje <= t1, sin cierre nocturno.

    Para 05:00 parte de la primera foto abierta (no de la etiqueta de cierre).
    Para la comprobación histórica 05:30 se elige la foto de commit más cercana.
    """
    start, end = (pd.Timestamp(x) for x in C.day_bounds(day))
    start = start.normalize() + pd.Timedelta(hours=int(start_hm[:2]), minutes=int(start_hm[3:]))
    s = snaps[["short_name", "t", "bikes", "disabled"]].copy()
    s["blank"] = snaps["blank"].to_numpy() if "blank" in snaps else False
    s["t_pick"] = pd.to_datetime(snaps["t_commit"] if "t_commit" in snaps else snaps["t"]).to_numpy()
    s["t"] = pd.to_datetime(s["t"])
    s = s.sort_values(["short_name", "t"]).reset_index(drop=True)
    if start_hm == "05:00" and "is_renting" in snaps:
        rent = snaps.groupby("t_commit" if "t_commit" in snaps else "t")["is_renting"].agg(["mean", "size"])
        valid = rent[(rent["mean"] >= C.INITIAL_OPEN_RENTING_MIN) &
                     (rent["size"] >= rent["size"].max() * .95) & (rent.index >= start)]
        if valid.empty:
            raise ValueError(f"{day}: no hay foto abierta")
        s = s[s["t_pick"] >= valid.index[0]].copy()
    off = (s["t_pick"] - start).abs()
    first = (s.assign(_off=off).sort_values(["short_name", "_off", "t_pick"])
             .drop_duplicates("short_name").set_index("short_name")["t"])
    g = s.groupby("short_name", sort=False)
    iv = pd.DataFrame({"short_name": s["short_name"], "t0": s["t"], "t1": g["t"].shift(-1),
                       "t1_pick": g["t_pick"].shift(-1), "avail0": s["bikes"],
                       "avail1": g["bikes"].shift(-1), "dis0": s["disabled"],
                       "dis1": g["disabled"].shift(-1),
                       "blank_touch": s["blank"] | g["blank"].shift(-1, fill_value=False)})
    iv = iv[iv["t1"].notna() & (iv["t0"] >= iv["short_name"].map(first)) & (iv["t1_pick"] < end)].copy()
    iv = iv.drop(columns="t1_pick")
    for col in ("avail1", "dis1"):
        iv[col] = iv[col].astype("int64")
    iv["stock0"] = iv["avail0"] + iv["dis0"]
    iv["stock1"] = iv["avail1"] + iv["dis1"]
    ev = pd.concat([pd.DataFrame({"short_name": trips["o"].astype(str), "te": pd.to_datetime(trips["t_dep"]), "kind": "departures"}),
                    pd.DataFrame({"short_name": trips["d"].astype(str), "te": pd.to_datetime(trips["t_arr"]), "kind": "arrivals"})], ignore_index=True)
    ev = ev[ev["short_name"].isin(s["short_name"])].sort_values("te")
    grid = s[["short_name", "t"]].rename(columns={"t": "t0"}).sort_values("t0")
    ev["te"] = ev["te"].astype("datetime64[ns]")
    grid["t0"] = grid["t0"].astype("datetime64[ns]")
    ev = pd.merge_asof(ev, grid, left_on="te", right_on="t0", by="short_name",
                       direction="backward", allow_exact_matches=False)
    cnt = (ev.dropna(subset=["t0"]).groupby(["short_name", "t0", "kind"]).size()
           .unstack("kind", fill_value=0).reindex(columns=["arrivals", "departures"], fill_value=0))
    iv = iv.merge(cnt, left_on=["short_name", "t0"], right_index=True, how="left")
    iv[["arrivals", "departures"]] = iv[["arrivals", "departures"]].fillna(0).astype("int64")
    iv["delta"] = (iv["stock1"] - iv["stock0"] - iv["arrivals"] + iv["departures"]).astype("int64")
    iv = iv.sort_values(["short_name", "t0"]).reset_index(drop=True)
    iv["par_start"] = _undo_starts(iv)
    iv["par"] = _undo_both(iv, iv["par_start"])
    return iv


def moves_from_intervals(iv: pd.DataFrame) -> pd.DataFrame:
    m = iv[~iv["blank_touch"] & iv["delta"].ne(0)]
    cols = (["day"] if "day" in iv else []) + K.ECOBICI_MOVES_COLUMNS
    return K.validate_ecobici_moves(m[cols].reset_index(drop=True))


def damage_events(iv: pd.DataFrame) -> pd.DataFrame:
    """Cambio observado en el contador de no rentables, aplicado en t1."""
    diff = iv["dis1"] - iv["dis0"]
    parts = []
    for kind, sel in (("sube", diff > 0), ("baja", diff < 0)):
        x = iv[sel & ~iv["blank_touch"]]
        p = x[["short_name", "t1"]].rename(columns={"t1": "t"}).copy()
        if "day" in iv:
            p.insert(0, "day", x["day"].to_numpy())
        p["kind"] = kind
        p["n"] = diff.loc[x.index].abs().astype("int64").to_numpy()
        parts.append(p)
    return K.validate_damage_events(pd.concat(parts, ignore_index=True).sort_values(["t", "short_name"]).reset_index(drop=True))


def main_moves(moves: pd.DataFrame, with_pairs: bool = False) -> pd.DataFrame:
    return moves if with_pairs else moves[~moves["par"]]


def flow_stats(moves: pd.DataFrame) -> dict:
    d = moves["delta"]
    a, r = int(d.clip(lower=0).sum()), int((-d).clip(lower=0).sum())
    return {"visitas": len(moves), "A": a, "R": r, "neto": a - r}


def day_summary(iv: pd.DataFrame, day=None) -> dict:
    m = moves_from_intervals(iv)
    return {**flow_stats(main_moves(m)), "sin_regla": flow_stats(m),
            "pares": int(iv["par_start"].sum()), "blank_touch": int(iv["blank_touch"].sum())}


def observed_ef(snaps: pd.DataFrame, day, lo_min: float = 0, hi_min: float = C.WINDOW_MIN) -> pd.DataFrame:
    """Minutos-estación observados como escalón de feed; blancos aparte."""
    start, end = (pd.Timestamp(x) for x in C.day_bounds(day))
    lo, hi = start + pd.Timedelta(minutes=lo_min), start + pd.Timedelta(minutes=hi_min)
    s = snaps[["short_name", "t", "bikes", "docks"]].copy()
    s["blank"] = snaps["blank"].to_numpy() if "blank" in snaps else False
    commit = pd.to_datetime(snaps["t_commit"] if "t_commit" in snaps else snaps["t"])
    s = s[(commit < end).to_numpy()].sort_values(["short_name", "t"])
    next_t = s.groupby("short_name")["t"].shift(-1).fillna(hi)
    mins = (next_t.clip(lower=lo, upper=hi) - s["t"].clip(lower=lo, upper=hi)) / pd.Timedelta(minutes=1)
    first = s.groupby("short_name")["t"].min().clip(lower=lo, upper=hi)
    return pd.DataFrame([{"E": float(mins[~s["blank"] & s["bikes"].eq(0)].sum()),
                          "F": float(mins[~s["blank"] & s["docks"].eq(0)].sum()),
                          "blank_min": float(mins[s["blank"]].sum()),
                          "unobs_min": float(((first - lo) / pd.Timedelta(minutes=1)).sum()),
                          "stations": int(s["short_name"].nunique())}])


def _day_trips(d) -> pd.DataFrame:
    return data.trips_range(d - timedelta(days=1), d + timedelta(days=1))


def measure_day(day, start_hm: str = "05:00") -> pd.DataFrame:
    d = C.as_date(day)
    iv = intervals(state_time(data.snapshots(d)), _day_trips(d), d, start_hm)
    iv.insert(0, "day", d.isoformat())
    return iv


def impact_table(iv: pd.DataFrame, n_days: int) -> pd.DataFrame:
    """Comparación de umbrales de emparejamiento sobre los mismos intervalos."""
    order = (["day"] if "day" in iv else []) + ["short_name", "t0"]
    iv = iv.sort_values(order).reset_index(drop=True)
    rows = []
    for name, size in (("sin regla", 0), ("±1", 1), ("hasta ±2", 2), ("hasta ±3", 3), ("cualquier tamaño", None)):
        par = _undo_both(iv, _undo_starts(iv, size)) if size != 0 else pd.Series(False, index=iv.index)
        m = moves_from_intervals(iv[~par].copy())
        rows.append({"regla": name, **{k: v / n_days for k, v in flow_stats(m).items()}})
    t = pd.DataFrame(rows)
    t["% visitas"] = 100 * t["visitas"] / t["visitas"].iloc[0]
    t["% bicis"] = 100 * t["A"] / t["A"].iloc[0]
    return t


def hourly_visits(iv: pd.DataFrame) -> pd.DataFrame:
    """Visitas por hora de reloj, con y sin pares, para auditoría."""
    m = moves_from_intervals(iv)
    rows = []
    for label, frame in (("sin regla", m), ("cualquier tamaño", main_moves(m))):
        h = frame.groupby(["day", frame["t0"].dt.floor("h")]).size()
        rows.append({"regla": label, "horas": len(h), "media": float(h.mean()),
                     "p95": float(h.quantile(.95)), "p99": float(h.quantile(.99))})
    return pd.DataFrame(rows)


def damage_classification(iv: pd.DataFrame) -> pd.DataFrame:
    """Dañadas observadas según dirección del total, sin atribuir taller."""
    x = iv[~iv["blank_touch"]].copy()
    x["cambio"] = x["dis1"] - x["dis0"]
    x = x[x["cambio"].ne(0)]
    x["tipo"] = np.where(x["cambio"] > 0, "sube", "baja")
    x["total"] = np.select([x["delta"] > 0, x["delta"] < 0], ["sube", "baja"], default="igual")
    return x.groupby(["tipo", "total"], as_index=False).agg(bicis=("cambio", lambda s: int(s.abs().sum())))


def visit_caps(iv: pd.DataFrame) -> dict:
    """p95/p99 por foto, normalizado a 15 min; tamaño por visita sin normalizar."""
    m = moves_from_intervals(iv)
    m = main_moves(m)
    lengths = iv[~iv["blank_touch"]].groupby(["day", "t0"])["t1"].first().reset_index()
    lengths["minutes"] = (lengths["t1"] - lengths["t0"]) / pd.Timedelta(minutes=1)
    counts = m.groupby(["day", "t0"]).size().rename("visitas").reset_index()
    photos = lengths.merge(counts, on=["day", "t0"], how="left").fillna({"visitas": 0})
    rate = photos["visitas"] * 15 / photos["minutes"]
    # El wiki muestra percentiles redondeados al entero más cercano (no techo):
    # en sus 15 días los cuantiles crudos de visitas son 67.1605 y 83.2370.
    return {"dias": int(iv["day"].nunique()), "fotos": len(photos),
            "visitas_p95": int(round(np.percentile(rate, 95))),
            "visitas_p99": int(round(np.percentile(rate, 99))),
            "bicis_p95": int(round(np.percentile(m["delta"].abs(), 95))),
            "bicis_p99": int(round(np.percentile(m["delta"].abs(), 99))),
            "mediana_min_entre_fotos": float(photos["minutes"].median())}


def pair_evidence(iv: pd.DataFrame, trips: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """Tasa por actividad, viajes cerca de foto intermedia y ejemplos verificables."""
    v = iv[~iv["blank_touch"]].sort_values(["day", "short_name", "t0"]).reset_index(drop=True).copy()
    g = v.groupby(["day", "short_name"], sort=False)
    v["viajes2"] = v["arrivals"] + v["departures"] + g["arrivals"].shift(-1) + g["departures"].shift(-1)
    v["next_delta"] = g["delta"].shift(-1)
    # No cruzar intervalos omitidos/blancos: el par viene de la serie original.
    valid = v["next_delta"].notna()
    pairs = v[valid].copy()
    pairs["grupo"] = pd.cut(pairs["viajes2"], [-1, 0, 2, 5, 10, np.inf], labels=["0", "1–2", "3–5", "6–10", ">10"])
    rates = pairs.groupby("grupo", observed=False).agg(intervalos=("delta", "size"), pares=("par_start", "sum"))
    rates["por_1000"] = 1000 * rates["pares"] / rates["intervalos"]
    # _day_trips trae días adyacentes: al juntar días de evaluación algunos
    # viajes se repiten. La deduplicación evita falsos tamaños de ejemplo.
    trips = trips.drop_duplicates(["bike", "o", "d", "t_dep", "t_arr"])
    ev = pd.concat([trips.assign(short_name=trips["o"], te=trips["t_dep"], tipo="salida"),
                    trips.assign(short_name=trips["d"], te=trips["t_arr"], tipo="llegada")], ignore_index=True)
    by = {st: x.sort_values("te") for st, x in ev.groupby("short_name")}
    times = {st: x["te"].to_numpy(dtype="datetime64[ns]") for st, x in by.items()}
    secs, near = [], []
    examples = []
    for row in pairs.itertuples():
        x = by.get(row.short_name)
        if x is None or x.empty:
            secs.append(np.nan); near.append(0); continue
        t = pd.Timestamp(row.t1)
        a = times[row.short_name]
        point = np.datetime64(t, "ns")
        pos = int(np.searchsorted(a, point))
        closest = min((abs((a[j] - point) / np.timedelta64(1, "s"))
                       for j in (pos - 1, pos) if 0 <= j < len(a)), default=np.nan)
        secs.append(float(closest))
        s = abs(int(row.delta))
        near.append(int(np.searchsorted(a, point + np.timedelta64(120, "s"), side="right") -
                        np.searchsorted(a, point - np.timedelta64(120, "s"), side="left")))
        if row.par_start and s in (1, 2, 3) and not any(e["size"] == s for e in examples):
            tipo = "salida" if row.delta > 0 else "llegada"
            exact = x[(x["tipo"] == tipo) & (x["te"] > t - pd.Timedelta(seconds=90)) & (x["te"] <= t)]
            if len(exact) == s:
                examples.append({"size": s, "day": row.day, "station": row.short_name,
                                 "photos": [str(row.t0), str(row.t1), str(v.loc[row.Index + 1, "t1"])],
                                 "deltas": [int(row.delta), int(v.loc[row.Index + 1, "delta"])],
                                 "trips": [{"bike": str(r.bike), "o": str(r.o), "d": str(r.d),
                                            "t_dep": str(r.t_dep), "t_arr": str(r.t_arr)} for r in exact.itertuples()]})
    pairs["near_s"] = secs
    pairs["near_120"] = near
    close = pd.DataFrame([{"grupo": lab, "n": int(mask.sum()), "viaje_30s_%": float((pairs.loc[mask, "near_s"] <= 30).mean() * 100),
                           "viaje_120s_%": float((pairs.loc[mask, "near_s"] <= 120).mean() * 100),
                           "mediana_s": float(pairs.loc[mask, "near_s"].median())}
                          for lab, mask in (("pares ±1", pairs["par_start"] & pairs["delta"].abs().eq(1)),
                                            ("otras fotos", ~pairs["par_start"]))])
    return rates.reset_index(), close, sorted(examples, key=lambda e: e["size"])


def pair_rates_by_size(iv: pd.DataFrame, sizes=(1, 2, 3)) -> pd.DataFrame:
    """Pares por 1,000 pares de intervalos según viajes y tamaño."""
    x = iv[~iv["blank_touch"]].sort_values(["day", "short_name", "t0"]).reset_index(drop=True)
    count = x["arrivals"] + x["departures"]
    x["viajes2"] = count + count.groupby([x["day"], x["short_name"]], sort=False).shift(-1)
    x = x[x["viajes2"].notna()].copy()
    x["grupo"] = pd.cut(x["viajes2"], [-1, 0, 2, 5, 10, np.inf],
                        labels=["0", "1–2", "3–5", "6–10", ">10"])
    rows = []
    for label, group in x.groupby("grupo", observed=False):
        for size in sizes:
            n = int((group["par_start"] & group["delta"].abs().eq(size)).sum())
            rows.append({"viajes": label, "tamaño": size, "pares": n,
                         "por_1000": 1000 * n / len(group) if len(group) else 0.0})
    return pd.DataFrame(rows)


def _markdown(df: pd.DataFrame, decimals: int = 1) -> str:
    """Tabla Markdown sin dependencia opcional tabulate."""
    names = list(df.columns)
    def fmt(v):
        if isinstance(v, (float, np.floating)):
            return f"{v:,.{decimals}f}"
        return str(v)
    header = "| " + " | ".join(names) + " |"
    return "\n".join([header, "|" + "---|" * len(names)] +
                     ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False, name=None)])


def large_visit_audit(iv: pd.DataFrame) -> dict:
    """Audita visitas ≥20 frente a huecos, primera foto y retorno de un blanco."""
    x = iv[~iv["blank_touch"] & iv["delta"].abs().ge(20) & ~iv["par"]]
    first = iv.groupby(["day", "short_name"], sort=False).cumcount().eq(0)
    after_blank = iv.groupby(["day", "short_name"], sort=False)["blank_touch"].shift(1, fill_value=False)
    return {"visitas_ge20": int(len(x)),
            "tras_hueco_30min": int(((x["t1"] - x["t0"]) > pd.Timedelta(minutes=30)).sum()),
            "primera_foto_estacion": int(first.loc[x.index].sum()),
            "despues_de_blanco": int(after_blank.loc[x.index].sum())}


def _selected_days() -> set[str]:
    js = json.loads(C.DAYS_JSON.read_text())
    days = set()
    for key in ("seleccion", "curva"):
        days.update(x["day"] for x in js[key])
    for key in ("prueba", "prod_2026"):
        for group in js[key].values():
            days.update(x["day"] for x in group)
    return days


def run() -> dict:
    RESULTS.mkdir(parents=True, exist_ok=True)
    selected = _selected_days()
    eligible = {str(d.date()) for d in pd.date_range("2025-01-01", "2025-11-30")
                if data.coverage(d.date()) >= C.COVERAGE_MIN}
    days = sorted(selected | eligible | set(C.RUN1_EVAL_DAYS))
    moves, damage, obs, caps_iv, eval_iv, eval_old = [], [], [], [], [], []
    monthly, large_rows = [], []
    month_0500, month_0530, month = [], [], None

    def flush_month():
        if not month_0500:
            return
        for window, frames in (("05:00", month_0500), ("05:30", month_0530)):
            frame = pd.concat(frames, ignore_index=True)
            monthly.append({"mes": month, "ventana": window, **visit_caps(frame)})
            large_rows.append({"mes": month, "ventana": window, **large_visit_audit(frame)})
    for ds in days:
        d = C.as_date(ds)
        sn = state_time(data.snapshots(d))
        # Desde febrero de 2026 se guardaron solo la foto inicial y cambios de
        # dañadas: no existe serie densa para inferir movimientos ni E/F diario.
        sparse = d >= date(2026, 2, 1)
        if sparse:
            iv = intervals(sn, _day_trips(d), d)
            iv.insert(0, "day", ds)
            damage.append(damage_events(iv))
            continue
        tr_day = _day_trips(d)
        iv = intervals(sn, tr_day, d)
        iv.insert(0, "day", ds)
        if ds in eligible:
            if month != ds[:7]:
                flush_month()
                month_0500, month_0530, month = [], [], ds[:7]
            month_0500.append(iv)
            historical = intervals(sn, tr_day, d, "05:30")
            historical.insert(0, "day", ds)
            month_0530.append(historical)
            if ds in C.RUN1_EVAL_DAYS:
                eval_old.append(historical)
        if ds in selected:
            moves.append(moves_from_intervals(iv))
            damage.append(damage_events(iv))
            obs.append({"day": ds, **observed_ef(sn, d).iloc[0].to_dict()})
        if "2025-01-01" <= ds <= "2025-08-31" and data.coverage(d) >= C.COVERAGE_MIN:
            caps_iv.append(iv)
        if ds in C.RUN1_EVAL_DAYS:
            eval_iv.append(iv)
        print(ds, flush=True)
    flush_month()
    C.DERIVED.mkdir(parents=True, exist_ok=True)
    pd.concat(moves, ignore_index=True).to_parquet(MOVES_PARQUET, index=False)
    pd.concat(damage, ignore_index=True).to_parquet(DAMAGE_PARQUET, index=False)
    pd.DataFrame(obs).to_csv(RESULTS / "ecobici_observado.csv", index=False)
    cap = visit_caps(pd.concat(caps_iv, ignore_index=True))
    e = pd.concat(eval_iv, ignore_index=True)
    old = pd.concat(eval_old, ignore_index=True)
    reference = visit_caps(old)
    stats = {"recalculados": {"p95": {"visitas_por_decision": cap["visitas_p95"], "bicis_por_visita": cap["bicis_p95"]},
                               "p99": {"visitas_por_decision": cap["visitas_p99"], "bicis_por_visita": cap["bicis_p99"]},
                               "poblacion": "enero–agosto 2025, cobertura ≥90%", "ventana": "05:00–00:30",
                               "dias": cap["dias"], "fotos": cap["fotos"]},
             "referencia_wiki": {"p95": {"visitas_por_decision": 67, "bicis_por_visita": 14},
                                 "p99": {"visitas_por_decision": 83, "bicis_por_visita": 24},
                                 "poblacion": "15 RUN1_EVAL_DAYS septiembre–noviembre 2025", "ventana": "05:30–00:30",
                                 "dias": reference["dias"], "fotos": reference["fotos"]},
             "referencia_reproducida": reference, "meses": monthly}
    (RESULTS / "ecobici_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n")
    impact = impact_table(old, 15)
    current = impact_table(e, 15)
    tr = pd.concat([_day_trips(C.as_date(ds)) for ds in C.RUN1_EVAL_DAYS], ignore_index=True)
    rates, close, examples = pair_evidence(old, tr)
    size_rates = pair_rates_by_size(old)
    real = {"visitas_p95": 67, "bicis_p95": 14, "visitas_p99": 83, "bicis_p99": 24}
    comparison = pd.DataFrame([{"tope": k, "recalculado": cap[k], "referencia": ref,
                                 "diferencia": cap[k] - ref} for k, ref in real.items()])
    summary = pd.DataFrame([{"ventana": window, **flow_stats(main_moves(moves_from_intervals(frame)))}
                            for window, frame in (("05:30–00:30", old), ("05:00–00:30", e))])
    for col in ("visitas", "A", "R", "neto"):
        summary[col] /= 15
    both = pd.concat(caps_iv, ignore_index=True)
    caps_neto = both.groupby("day").apply(lambda x: flow_stats(main_moves(moves_from_intervals(x)))["neto"], include_groups=False)
    eval_neto = e.groupby("day").apply(lambda x: flow_stats(main_moves(moves_from_intervals(x)))["neto"], include_groups=False)
    monthly_df = pd.DataFrame(monthly)
    jan = monthly_df[(monthly_df['mes'] == '2025-01') & (monthly_df['ventana'] == '05:00')].iloc[0]
    nov = monthly_df[(monthly_df['mes'] == '2025-11') & (monthly_df['ventana'] == '05:00')].iloc[0]
    text = f"""# Medición de Ecobici — run 3

Regla: `delta = Δdisponibles + Δdañadas − (llegadas − salidas)` por estación y fotos consecutivas; hora de estado = commit −30 s. Pares opuestos inmediatos de **cualquier tamaño** se marcan `par`, se excluyen del esfuerzo y suman cero. `damage_events.parquet` contiene solo cambios observados `sube`/`baja` en t1. Las no rentables del feed no son necesariamente bicis averiadas.

## Topes (enero–agosto 2025, cobertura ≥90%, {cap['dias']} días)

Visitas por foto normalizadas a 15 min (visitas × 15 / duración real); tamaño absoluto por visita, sin pares. {cap['fotos']} fotos, mediana de {cap['mediana_min_entre_fotos']:.1f} min entre fotos.

{_markdown(comparison, 0)}

La referencia usa septiembre–noviembre y 05:30; el caso base usa enero–agosto y 05:00. La diferencia de población no se corrige ajustando la regla. El p95 de visitas de los 15 días de referencia sin redondear es 67.1605 y el p99 83.2370, ambos redondeados al entero más cercano.

### Evolución mensual de los topes (días de cobertura ≥90%)

{_markdown(pd.DataFrame(monthly)[['mes', 'ventana', 'dias', 'fotos', 'visitas_p95', 'visitas_p99', 'bicis_p95', 'bicis_p99']], 0)}

### Auditoría de visitas grandes (≥20 bicis, sin pares)

{_markdown(pd.DataFrame(large_rows), 0)}

Los conteos de hueco, primera foto y blanco pueden traslaparse. Una visita de tamaño grande tras intervalo largo mide el neto del intervalo, no necesariamente una única parada de camión. Enero tiene p95 de {jan['visitas_p95']} visitas frente a {nov['visitas_p95']} en noviembre; en enero las visitas p95 mueven {jan['bicis_p95']} bicis frente a {nov['bicis_p95']} en noviembre. La subida de frecuencia y caída de tamaño son graduales, no las causa el cambio de ventana: cada mes 05:00 y 05:30 difieren a lo sumo en unas pocas visitas. Los huecos de más de 30 min sí concentran parte de los movimientos ≥20; no se filtran.

## Comprobación de sanidad (15 días de evaluación)

{_markdown(summary)}

Referencias para 05:30: 2,482 visitas, 5,539 metidas, 5,560 sacadas, neto −21 bicis/día. Con pares incluidos el neto no cambia. En los 15 días de evaluación (05:00), |A − R| medio por día = {eval_neto.abs().mean():.1f} bicis (<60); en los 230 días de topes de enero–agosto es {caps_neto.abs().mean():.1f} bicis (ligeramente >60, no se oculta). La medición no infiere taller ni crea flota con ninguna regla.

## Impacto de la regla, ventana 05:30–00:30 (media por día)

{_markdown(impact)}

## Impacto, ventana 05:00–00:30 (media por día)

{_markdown(current)}

## Evidencia de pares (15 días de evaluación)

Pares por actividad de viajes en dos intervalos consecutivos de la estación:

{_markdown(rates, 2)}

El mismo gradiente desglosado para tamaños 1, 2 y 3:

{_markdown(size_rates, 2)}

Cercanía del viaje a la foto intermedia (las otras fotos sirven de contraste):

{_markdown(close)}

### Ejemplos con viajes exactos

"""
    for ex in examples:
        text += (f"- **±{ex['size']}**, estación {ex['station']}, {ex['day']}: fotos " + " → ".join(ex["photos"]) +
                 f"; movimientos {ex['deltas']}. Viajes: " + "; ".join(
                     f"bici {t['bike']} ({t['o']} → {t['d']}, salida {t['t_dep']}, llegada {t['t_arr']})" for t in ex["trips"]) + ".\n")
    text += "\n## Limitaciones\n\nSolo se ve el neto por estación e intervalo. Los pares explican desfase de relojes, no se prueba la causa individual. Desde febrero de 2026 el caché guarda solo primera foto y cambios de dañadas: hay eventos de etiqueta, pero **no** movimientos ni E/F diarios observables; no se imputan como ceros. E/F de días completos es un escalón del feed (blancos y tramos sin foto se contabilizan aparte).\n"
    (RESULTS / "REPORT.md").write_text(text)
    return {**cap, "eval_0530": summary.iloc[0].to_dict(), "eval_0500": summary.iloc[1].to_dict(),
            "mean_abs_neto_eval": float(eval_neto.abs().mean()),
            "mean_abs_neto_cap_days": float(caps_neto.abs().mean()), "examples": len(examples)}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
