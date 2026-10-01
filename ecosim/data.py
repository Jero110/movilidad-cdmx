"""Loaders auditados de ecosim: viajes, estaciones, vecinos, snapshots GBFS y
estado inicial. Hallazgos y supuestos en `ecosim/FUNDAMENTOS.md`.

Todos los tiempos que devuelve este módulo son hora local CDMX *naive*.

Construcción de cachés (idempotente, solo hace falta una vez):

    uv run python -m ecosim.data build-trips      # CSV ene–nov → parquet
    uv run python -m ecosim.data build-snapshots  # bucket GBFS → por día (05:00 → 01:00 del d+1)
    uv run python -m ecosim.data audit-night      # auditoría del feed de noche → night_audit.json
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from functools import lru_cache

import duckdb
import numpy as np
import pandas as pd

from ecosim import config as C
from ecosim import contracts as K


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET threads=4")
    return con


def _check_not_sealed(*days) -> None:
    """Error si alguna fecha (date o datetime) cae en diciembre 2025 o después."""
    for d in days:
        if isinstance(d, datetime):
            d = d.date()
        if d >= C.SEALED_FROM:
            raise ValueError(f"{d}: diciembre 2025 en adelante está sellado")


def _check_day(d: date) -> None:
    """Sello para las funciones por día: revisa la fecha y el FIN de sus
    ventanas (simulada hasta d+1 00:30, snapshots hasta d+1 01:00). Así la
    ventana del 2025-11-30, que llega al 1-dic, también queda sellada."""
    _check_not_sealed(d, C.day_bounds(d)[1], C.snapshot_bounds(d)[1])


# ======================================================================
# Viajes
# ======================================================================

def build_trips() -> dict:
    """CSV crudos 2025-01..2025-11 → `TRIPS_PARQUET` + auditoría JSON.

    Los CSV están particionados por mes de *llegada* (cada viaje aparece una
    sola vez) y en hora local. `2025-12.csv` no se lee (sellado).
    """
    files = [C.RAW_TRIPS_DIR / f for f in C.RAW_TRIP_FILES]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(missing)
    flist = ", ".join(f"'{f}'" for f in files)
    con = _con()
    con.execute(f"""
        create temp table raw as
        select trim(Bici) as bike,
               -- el ID "Temporal - 2da Sección Bosque Chapultepec" viene con comillas literales
               trim(replace(Ciclo_Estacion_Retiro, '"', '')) as o,
               trim(replace(Ciclo_EstacionArribo, '"', '')) as d,
               try_strptime(Fecha_Retiro || ' ' || Hora_Retiro, '%d/%m/%Y %H:%M:%S') as t_dep,
               try_strptime(Fecha_Arribo || ' ' || Hora_Arribo, '%d/%m/%Y %H:%M:%S') as t_arr,
               regexp_extract(filename, '[^/]+$') as src
        from read_csv([{flist}], all_varchar=true, header=true, filename=true)
    """)
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    a = {"files": C.RAW_TRIP_FILES}
    a["raw_rows"] = q("select count(*) from raw")
    a["bad_timestamp"] = q("select count(*) from raw where t_dep is null or t_arr is null")
    a["missing_field"] = q("select count(*) from raw where coalesce(bike,'')='' or coalesce(o,'')='' or coalesce(d,'')=''")
    a["negative_duration"] = q("select count(*) from raw where t_arr < t_dep")
    a["exact_duplicates"] = q("select count(*) - count(distinct (bike,o,d,t_dep,t_arr)) from raw")
    a["duplicate_bike_tdep"] = q("select count(*) - count(distinct (bike,t_dep)) from raw")
    a["crosses_midnight"] = q("select count(*) from raw where cast(t_arr as date) <> cast(t_dep as date)")
    a["gt_3h"] = q("select count(*) from raw where t_arr - t_dep > interval 3 hour")
    a["gt_1d"] = q("select count(*) from raw where t_arr - t_dep > interval 1 day")
    a["same_station"] = q("select count(*) from raw where o = d")
    a["non_numeric_station_ids"] = con.execute("""
        select id, count(*) from (select o as id from raw union all select d from raw)
        where not regexp_matches(id, '^\\d{3}(-\\d{3})?$') group by id order by 2 desc
    """).fetchall()
    a["file_month_ne_arrival_month"] = q("""
        select count(*) from raw
        where regexp_extract(src, '2025-(\\d\\d)', 1) <> strftime(t_arr, '%m')
    """)
    C.DERIVED.mkdir(parents=True, exist_ok=True)
    con.execute(f"""
        copy (
          select bike, o, d, t_dep, t_arr,
                 (t_arr - t_dep) > interval 3 hour as long
          from raw
          where t_dep is not null and t_arr is not null and t_arr >= t_dep
            and coalesce(bike,'')<>'' and coalesce(o,'')<>'' and coalesce(d,'')<>''
          qualify row_number() over (partition by bike, o, d, t_dep, t_arr) = 1
          order by t_dep
        ) to '{C.TRIPS_PARQUET}' (format parquet)
    """)
    a["kept"] = q(f"select count(*) from '{C.TRIPS_PARQUET}'")
    C.TRIPS_AUDIT.write_text(json.dumps(a, indent=2, default=str))
    return a


def _ensure_trips() -> None:
    if not C.TRIPS_PARQUET.exists():
        build_trips()


def _query_trips(where: str) -> pd.DataFrame:
    _ensure_trips()
    df = _con().execute(f"""
        select bike, o, d, t_dep, t_arr, long from '{C.TRIPS_PARQUET}'
        where {where} order by t_dep, bike
    """).df()
    return df


def _add_known(df: pd.DataFrame, known: set[str]) -> pd.DataFrame:
    df["o_known"] = df["o"].isin(known)
    df["d_known"] = df["d"].isin(known)
    return df


def trips(day) -> pd.DataFrame:
    """Viajes relevantes para la ventana [d 05:30, d+1 00:30) del día.

    Incluye todo viaje que sale dentro de la ventana, o que salió antes y llega
    dentro (entrega su bici). Los que cruzan medianoche entran solos (se usan
    timestamps completos); los que salen dentro y llegan después de 00:30
    también se devuelven (el simulador cuenta su salida y no su llegada). Sin filtro de duración (`long` = >3 h, solo
    marca). `o_known`/`d_known`: la estación aparece en los snapshots GBFS de
    ese día. Semántica acordada para estaciones desconocidas:
      * salida desde una desconocida → la salida se ignora;
      * llegada a una desconocida → la bici sale del sistema y se cuenta.
    Columnas: `contracts.TRIP_COLUMNS` + long, o_known, d_known.
    """
    d = C.as_date(day)
    _check_day(d)
    start, end = C.day_bounds(d)
    df = _query_trips(
        f"(t_dep >= '{start}' and t_dep < '{end}') or (t_dep < '{start}' and t_arr >= '{start}' and t_arr < '{end}')"
    )
    return _add_known(df, set(stations(d)["short_name"]))


def trips_range(start, end) -> pd.DataFrame:
    """Todos los viajes con salida en [start, end] (fechas inclusive), para
    entrenar modelos. `o_known/d_known` no se calculan aquí (dependen del día).
    """
    s, e = C.as_date(start), C.as_date(end)
    _check_not_sealed(s, e)
    lo = datetime.combine(s, datetime.min.time())
    hi = datetime.combine(e + timedelta(days=1), datetime.min.time())
    return _query_trips(f"t_dep >= '{lo}' and t_dep < '{hi}'")


# ======================================================================
# Snapshots GBFS
# ======================================================================

def _snapshot_path(d: date):
    return C.SNAPSHOT_DIR / f"{d.isoformat()}.parquet"


def _gbfs_url(i: int) -> str:
    year, mon = C.GBFS_MONTHS[i]
    return f"{C.GBFS_BUCKET}/{year}/{mon}.parquet"


def build_snapshots() -> dict:
    """Descarga del bucket público y guarda un parquet por día (ventana) con
    los snapshots de [d 05:00, d+1 01:00) local, para d en
    [SNAPSHOT_START, SNAPSHOT_END] = 2025-08-01..2025-11-29.

    La conversión UTC → local se hace con la zona IANA (`timezone(TZ, ts)` con
    la sesión de duckdb en UTC), no con la fecha UTC. La ventana del día d cae
    en el archivo mensual (UTC) de d y, si d es fin de mes, también en el
    siguiente; solo se leen `GBFS_MONTHS` (ago–nov), nunca Dec.parquet, y se
    filtra además por `committed_at_utc` < 2025-11-30 07:00 UTC.
    Sobrescribe el caché del run 1 (05:00–13:00) y borra el del 2025-11-30.
    """
    con = _con()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"SET s3_endpoint='{C.GBFS_S3_ENDPOINT}'; SET s3_region='auto'; SET TimeZone='UTC';")
    C.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    off = timedelta(hours=-C.UTC_OFFSET_HOURS)          # local → UTC (+6 h)
    out = {}
    for i, (year, mon) in enumerate(C.GBFS_MONTHS):
        first = date(year, datetime.strptime(mon, "%b").month, 1)
        last = min((first + timedelta(days=32)).replace(day=1) - timedelta(days=1), C.SNAPSHOT_END)
        first = max(first, C.SNAPSHOT_START)
        lo = C.snapshot_bounds(first)[0] + off          # UTC naive
        hi = C.snapshot_bounds(last)[1] + off
        _check_not_sealed(hi - timedelta(microseconds=1))
        urls = [_gbfs_url(i)] + ([_gbfs_url(i + 1)] if i + 1 < len(C.GBFS_MONTHS) else [])
        flist = ", ".join(f"'{u}'" for u in urls)
        df = con.execute(f"""
            with s as (
              select *, timezone('{C.TZ}', committed_at_utc) as t
              from read_parquet([{flist}])
              where committed_at_utc >= timestamptz '{lo}+00' and committed_at_utc < timestamptz '{hi}+00'
            )
            select station_id, short_name, name, latitude as lat, longitude as lon,
                   capacity as cap, num_bikes_available as bikes,
                   num_bikes_disabled as disabled, num_docks_available as docks,
                   num_docks_disabled as docks_disabled,
                   is_installed, is_renting, is_returning, status,
                   t, committed_at_utc, git_commit
            from s
            -- ventana [d 05:00, d+1 01:00): t − 5 h cae en [d 00:00, d 20:00)
            where hour(t - interval 5 hour) < 20
        """).df()
        wd = (df["t"] - pd.Timedelta(hours=5)).dt.date
        for d, g in df.groupby(wd):
            if not (first <= d <= last):
                continue
            p = _snapshot_path(d)
            tmp = p.with_suffix(".tmp")
            g.sort_values(["t", "short_name"]).to_parquet(tmp, index=False)
            tmp.replace(p)
            out[str(d)] = len(g)
    stale = _snapshot_path(C.SNAPSHOT_END + timedelta(days=1))   # 2025-11-30 del run 1
    if stale.exists():
        stale.unlink()
    return out


@lru_cache(maxsize=32)
def _raw_snapshots(d: date) -> pd.DataFrame:
    _check_day(d)
    p = _snapshot_path(d)
    if not p.exists():
        raise FileNotFoundError(f"{p} no existe: corre `uv run python -m ecosim.data build-snapshots`")
    return pd.read_parquet(p)


def snapshots(day) -> pd.DataFrame:
    """Snapshots GBFS crudos de la ventana del día, [d 05:00, d+1 01:00) local.

    `t` = `committed_at_utc` convertido a hora local (el feed no trae
    `last_reported` por estación; el estado del feed es ~30 s anterior a `t`,
    ver FUNDAMENTOS.md). Un renglón por (estación, commit). Solo estaciones
    con `short_name` de Ecobici (`NNN` o `NNN-NNN`): el bucket trae estaciones
    de prueba de otros sistemas. Columnas: `contracts.SNAPSHOT_COLUMNS` +
    station_id, is_installed, blank (reporte en blanco: bikes = disabled =
    docks = docks_disabled = 0, la estación no reportó nada útil).
    """
    d = C.as_date(day)
    _check_day(d)
    df = _raw_snapshots(d)
    lo, hi = (pd.Timestamp(x) for x in C.snapshot_bounds(d))
    df = df[df["short_name"].str.fullmatch(C.SHORT_NAME_RE) & (df["t"] >= lo) & (df["t"] < hi)]
    cols = K.SNAPSHOT_COLUMNS + ["station_id", "is_installed"]
    df = df[cols].drop_duplicates(["short_name", "t"]).sort_values(["t", "short_name"])
    df["blank"] = (df["bikes"] + df["disabled"] + df["docks"] + df["docks_disabled"]) == 0
    return df.reset_index(drop=True)


def snapshot_times(day) -> pd.Series:
    """Timestamps distintos de snapshot del día (uno por commit del feed)."""
    return pd.Series(sorted(snapshots(day)["t"].unique()), name="t")


def coverage(day, block_min: int = C.COVERAGE_BLOCK_MIN) -> float:
    """Fracción de bloques de `block_min` (60 por defecto) en [d 05:30,
    d+1 00:30) con ≥1 snapshot (19 bloques de 60 min; ≥ 0.90 exige 18)."""
    start, _ = C.day_bounds(day)
    t = snapshot_times(day)
    k = ((t - pd.Timestamp(start)) / pd.Timedelta(minutes=block_min)).apply(np.floor)
    n = C.WINDOW_MIN // block_min
    return float(k[(k >= 0) & (k < n)].nunique() / n)


def _stock_match(sn: pd.DataFrame, tr: pd.DataFrame, lag_s: float) -> pd.DataFrame:
    """Por intervalo (estación, commit→commit siguiente) sin reportes en
    blanco: ¿el cambio de stock (disponibles + dañadas) = llegadas − salidas
    de los viajes con hora en (t0 − lag, t1 − lag]? Columnas: short_name, t0,
    t1, ok."""
    sn = sn[~sn["blank"]].assign(stock=lambda x: x.bikes + x.disabled).sort_values(["short_name", "t"])
    lag = pd.Timedelta(seconds=lag_s)
    ev = pd.concat([
        pd.DataFrame({"s": tr["o"], "tt": tr["t_dep"] + lag, "v": -1}),
        pd.DataFrame({"s": tr["d"], "tt": tr["t_arr"] + lag, "v": 1}),
    ]).sort_values("tt")
    evg = dict(tuple(ev.groupby("s")))
    out = []
    for s, g in sn.groupby("short_name"):
        e = evg.get(s)
        tt = e["tt"].to_numpy() if e is not None else np.array([], dtype="datetime64[us]")
        cs = np.concatenate([[0], np.cumsum(e["v"].to_numpy())]) if e is not None else np.array([0])
        t = g["t"].to_numpy()
        net = cs[np.searchsorted(tt, t[1:], "right")] - cs[np.searchsorted(tt, t[:-1], "right")]
        ok = np.diff(g["stock"].to_numpy()) == net
        out.append(pd.DataFrame({"short_name": s, "t0": t[:-1], "t1": t[1:], "ok": ok}))
    return pd.concat(out, ignore_index=True)


def night_audit(days=None) -> dict:
    """Auditoría del feed GBFS por franja de 30 min de la ventana de
    snapshots [05:00, 01:00): ¿está cerrado o es poco confiable de noche?

    Por franja (etiqueta HH:MM de inicio; "00:00" y "00:30" son del día
    siguiente), promediando sobre `days` (por defecto todos los del caché):
      commits_por_dia, dias_con_commit (fracción), gap_mediana/p90/max (min,
      por hora del commit que cierra el hueco), huecos_gt30/gt60 (conteo),
      blank, no_renta, no_devuelve (fracción de renglones estación × commit),
      disponibles/danadas (suma sobre estaciones, media por commit),
      salidas_por_dia (viajes), cuadre (fracción de intervalos estación
      donde Δ(disponibles + dañadas) = llegadas − salidas, con el desfase de
      `GBFS_COMMIT_LAG_S`, por franja de t1).
    Además `min_desde_ultimo_commit` a las 00:00 y 00:30 (mediana, p90, máx).
    """
    if days is None:
        days = [C.SNAPSHOT_START + timedelta(days=i)
                for i in range((C.SNAPSHOT_END - C.SNAPSHOT_START).days + 1)]
    slot = lambda t: ((t - pd.Timedelta(hours=5)).dt.hour * 2 + (t.dt.minute >= 30))  # noqa: E731  0 = 05:00
    rows, gaps, match, stale, dep = [], [], [], [], []
    for d in map(C.as_date, days):
        sn = snapshots(d)
        k = slot(sn["t"])
        g = sn.assign(k=k).groupby("k")
        per = pd.DataFrame({
            "commits": g["t"].nunique(),
            "blank": g["blank"].mean(),
            "no_renta": g["is_renting"].apply(lambda x: (~x.astype(bool)).mean()),
            "no_devuelve": g["is_returning"].apply(lambda x: (~x.astype(bool)).mean()),
            "disponibles": g["bikes"].sum() / g["t"].nunique(),
            "danadas": g["disabled"].sum() / g["t"].nunique(),
        })
        rows.append(per.reindex(range(40)).assign(commits=lambda x: x.commits.fillna(0)))
        ts = pd.Series(sorted(sn["t"].unique()))
        gp = pd.DataFrame({"k": slot(ts.iloc[1:]).to_numpy(),
                           "gap": (ts.diff().iloc[1:] / pd.Timedelta(minutes=1)).to_numpy()})
        gaps.append(gp)
        for lab, ref in (("00:00", C.snapshot_bounds(d)[0] + timedelta(hours=19)),
                         ("00:30", C.snapshot_bounds(d)[0] + timedelta(hours=19, minutes=30))):
            prev = ts[ts <= pd.Timestamp(ref)]
            stale.append({"ref": lab, "min": (pd.Timestamp(ref) - prev.max()) / pd.Timedelta(minutes=1)})
        lo, hi = (pd.Timestamp(x) for x in C.snapshot_bounds(d))
        tr = _query_trips(f"(t_dep >= '{lo - pd.Timedelta(hours=1)}' and t_dep < '{hi}') "
                          f"or (t_arr >= '{lo - pd.Timedelta(hours=1)}' and t_arr < '{hi}')")
        m = _stock_match(sn, tr, C.GBFS_COMMIT_LAG_S)
        match.append(m.assign(k=slot(m["t1"]))[["k", "ok"]])
        td = tr["t_dep"][(tr["t_dep"] >= lo) & (tr["t_dep"] < hi)]
        dep.append(slot(td).value_counts().reindex(range(40), fill_value=0))
    n = len(rows)
    R = pd.concat(rows, keys=range(n)).rename_axis(["i", "k"]).reset_index()
    G = pd.concat(gaps)
    M = pd.concat(match)
    out = R.groupby("k").agg(
        commits_por_dia=("commits", "mean"),
        dias_con_commit=("commits", lambda x: float((x > 0).mean())),
        blank=("blank", "mean"), no_renta=("no_renta", "mean"), no_devuelve=("no_devuelve", "mean"),
        disponibles=("disponibles", "mean"), danadas=("danadas", "mean"),
    )
    gg = G.groupby("k")["gap"]
    out["gap_mediana"] = gg.median()
    out["gap_p90"] = gg.quantile(0.9)
    out["gap_max"] = gg.max()
    out["huecos_gt30"] = gg.apply(lambda x: int((x > 30).sum()))
    out["huecos_gt60"] = gg.apply(lambda x: int((x > 60).sum()))
    out["salidas_por_dia"] = pd.concat(dep, axis=1).mean(axis=1)
    out["cuadre"] = M.groupby("k")["ok"].mean()
    out["intervalos"] = M.groupby("k")["ok"].size()
    out = out.reindex(range(40))
    out.index = [f"{(5 + k // 2) % 24:02d}:{30 * (k % 2):02d}" for k in out.index]
    st = pd.DataFrame(stale).groupby("ref")["min"].describe(percentiles=[0.5, 0.9])
    return {
        "days": [str(C.as_date(d)) for d in days],
        "por_franja_30min": json.loads(out.round(4).to_json(orient="index")),
        "min_desde_ultimo_commit": json.loads(st[["50%", "90%", "max"]].round(1).to_json(orient="index")),
    }


# ======================================================================
# Estaciones y vecinos
# ======================================================================

def haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * C.EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


def _nearest_0530(d: date) -> pd.DataFrame:
    """Una fila por estación: el snapshot más cercano a las 05:30 (antes o
    después). Regla única de capacidad, compartida por `stations` e
    `initial_state`: la `cap` de ese snapshot; si es un reporte en blanco,
    la capacidad máxima reportada ese día (puede ser 0 si nunca reportó).
    `out_of_service` = en blanco a las 05:30 o capacidad 0.
    """
    raw = _raw_snapshots(d)
    raw = raw[raw["short_name"].str.fullmatch(C.SHORT_NAME_RE)].drop_duplicates(["short_name", "t"])
    start, _ = C.day_bounds(d)
    off = (raw["t"] - pd.Timestamp(start)) / pd.Timedelta(minutes=1)
    raw = raw.assign(
        offset_min=off, _abs=off.abs(),
        blank=(raw["bikes"] + raw["disabled"] + raw["docks"] + raw["docks_disabled"]) == 0,
    )
    s = (raw.sort_values(["short_name", "_abs", "t"])
            .drop_duplicates("short_name")
            .set_index("short_name"))
    cap_day = raw.groupby("short_name")["cap"].max()
    s.loc[s["blank"], "cap"] = cap_day.loc[s.index[s["blank"]]]
    s["out_of_service"] = s["blank"] | (s["cap"] == 0)
    return s.reset_index()


def stations(day) -> pd.DataFrame:
    """Estaciones presentes en los snapshots del día, con coordenadas y
    capacidad del snapshot más cercano a las 05:30 (regla de `_nearest_0530`,
    la misma de `initial_state`).

    Columnas: short_name, station_id, name, lat, lon, cap, out_of_service,
    km_from_center, coord_invalid (>50 km del centro o sin coordenadas),
    coord_dup (otra estación válida en exactamente las mismas coordenadas).
    """
    d = C.as_date(day)
    _check_day(d)
    s = _nearest_0530(d)[["short_name", "station_id", "name", "lat", "lon", "cap", "out_of_service"]]
    s["km_from_center"] = haversine_m(s["lat"], s["lon"], *C.CDMX_CENTER) / 1000
    s["coord_invalid"] = (
        s["lat"].isna() | s["lon"].isna() | (s["km_from_center"] > C.MAX_KM_FROM_CENTER)
    )
    valid = s[~s["coord_invalid"]]
    dup_keys = valid[valid.duplicated(["lat", "lon"], keep=False)][["lat", "lon"]]
    s["coord_dup"] = s.set_index(["lat", "lon"]).index.isin(dup_keys.set_index(["lat", "lon"]).index) & ~s["coord_invalid"]
    return s.sort_values("short_name").reset_index(drop=True)


def neighbors(day) -> pd.DataFrame:
    """Distancias haversine entre todas las estaciones con coordenadas
    válidas del día, ordenadas por (short_name, dist_m). Una estación no es
    vecina de sí misma. Estaciones con `coord_invalid` no aparecen (ni como
    origen ni como vecina). Columnas: short_name, nbr, dist_m, rank (1 = la
    más cercana).
    """
    s = stations(day)
    s = s[~s["coord_invalid"]].reset_index(drop=True)
    lat, lon = s["lat"].to_numpy(), s["lon"].to_numpy()
    dist = haversine_m(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    n = len(s)
    i, j = np.where(~np.eye(n, dtype=bool))
    df = pd.DataFrame({
        "short_name": s["short_name"].to_numpy()[i],
        "nbr": s["short_name"].to_numpy()[j],
        "dist_m": dist[i, j],
    })
    df = df.sort_values(["short_name", "dist_m", "nbr"], kind="stable").reset_index(drop=True)
    df["rank"] = df.groupby("short_name").cumcount() + 1
    return df


# ======================================================================
# Estado inicial
# ======================================================================

def _roll_to_start(n: pd.DataFrame, d: date) -> pd.Series:
    """Ajuste de bicis disponibles para llevar cada snapshot a las 05:30
    exactas con los viajes reales (el simulador arranca a las 05:30 y vuelve
    a entregar los viajes de [05:30, t_estado], así que el snapshot no debe
    traerlos ya incluidos):
      * t_estado ≥ 05:30: + salidas − llegadas con tiempo en [05:30, t_estado];
      * t_estado < 05:30: − salidas + llegadas con tiempo en (t_estado, 05:30).
    t_estado = t_snap (commit) − `GBFS_COMMIT_LAG_S`, la misma hora que usa
    `medicion` (el snapshot se sigue ELIGIENDO por el commit crudo).
    """
    start = pd.Timestamp(C.day_bounds(d)[0])
    # Hora del estado del snapshot = commit − GBFS_COMMIT_LAG_S (como
    # `medicion`): los viajes de esos ~30 s todavía no están en el feed.
    ts = n[["short_name", "t"]].assign(t=n["t"] - pd.Timedelta(seconds=C.GBFS_COMMIT_LAG_S))
    lo, hi = min(start, ts["t"].min()), max(start, ts["t"].max())
    tr = _query_trips(f"(t_dep >= '{lo}' and t_dep <= '{hi}') or (t_arr >= '{lo}' and t_arr <= '{hi}')")
    ev = pd.concat([
        pd.DataFrame({"short_name": tr["o"], "tt": tr["t_dep"], "v": 1}),    # salida: +1 al retroceder
        pd.DataFrame({"short_name": tr["d"], "tt": tr["t_arr"], "v": -1}),   # llegada: −1 al retroceder
    ])
    ev = ev.merge(ts, on="short_name")
    after = (ev["t"] >= start) & (ev["tt"] >= start) & (ev["tt"] <= ev["t"])
    before = (ev["t"] < start) & (ev["tt"] > ev["t"]) & (ev["tt"] < start)
    adj = ev["v"].where(after, 0) - ev["v"].where(before, 0)
    return adj.groupby(ev["short_name"]).sum().reindex(n["short_name"], fill_value=0).astype("int64")


def initial_state(day) -> pd.DataFrame:
    """Estado de cada estación a las 05:30 exactas.

    Parte del snapshot más cercano a las 05:30 (antes o después;
    `offset_min` = t_snap − 05:30 en minutos, `stale` = |offset_min| > 10;
    t_snap y la elección usan el commit crudo) y lleva las bicis disponibles
    a las 05:30 con los viajes reales entre la hora del estado del feed
    (t_snap − `GBFS_COMMIT_LAG_S`) y las 05:30 (`_roll_to_start`). Las
    dañadas y `docks_disabled` se quedan como en el snapshot; `docks` = cap − bikes − disabled − docks_disabled. Si el
    ajuste deja las bicis fuera de [0, cap − disabled − docks_disabled] se
    recorta y `roll_clipped` = True; `df.attrs["n_roll_clipped"]` las cuenta.
    `bikes_snap`/`docks_snap` guardan el valor crudo del snapshot y
    `roll_adj` el ajuste pedido (antes del recorte).

    `blank` = el snapshot elegido es un reporte en blanco (estación fuera de
    línea: no hay viajes desde ella mientras está en blanco y suele
    reaparecer ya surtida). No se salta a un snapshot posterior, porque eso
    metería a las 05:30 bicis que Ecobici puso después. Se imputa como
    estación vacía (bikes = disabled = docks_disabled = 0, docks = cap, con
    la cap de `_nearest_0530`) y no se ajusta. `out_of_service` = blank o
    cap 0.
    """
    d = C.as_date(day)
    _check_day(d)
    n = _nearest_0530(d)
    n["stale"] = n["_abs"] > C.INITIAL_STALE_MIN
    n["bikes_snap"] = n["bikes"]
    n["docks_snap"] = n["docks"]
    adj = _roll_to_start(n, d).to_numpy().copy()
    adj[n["blank"].to_numpy()] = 0
    n["roll_adj"] = adj
    room = n["cap"] - n["disabled"] - n["docks_disabled"]
    want = n["bikes_snap"] + n["roll_adj"]
    n["bikes"] = want.clip(lower=0).where(want.clip(lower=0) <= room, room).astype("int64")
    n["roll_clipped"] = n["bikes"] != want
    n["docks"] = (n["cap"] - n["bikes"] - n["disabled"] - n["docks_disabled"]).astype("int64")
    s = n.rename(columns={"t": "t_snap"})
    s = s[K.INITIAL_STATE_COLUMNS].sort_values("short_name").reset_index(drop=True)
    s.attrs["n_roll_clipped"] = int(s["roll_clipped"].sum())
    return s


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build-trips":
        print(json.dumps(build_trips(), indent=2, default=str))
    elif cmd == "build-snapshots":
        out = build_snapshots()
        print(f"{len(out)} días, {sum(out.values())} renglones")
    elif cmd == "audit-night":
        out = night_audit()
        path = C.DERIVED / "night_audit.json"
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
        print(pd.DataFrame(out["por_franja_30min"]).T.to_string())
        print(pd.DataFrame(out["min_desde_ultimo_commit"]).T.to_string())
        print("→", path)
    else:
        print(__doc__)
