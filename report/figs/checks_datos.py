"""Checks de validación de los datos (sección Datos del reporte).

Lee el caché auditado de viajes (ene–nov 2025, `data/derived/ecosim/trips_2025_01_11.parquet`)
y el caché de snapshots GBFS de 24 h (ago–nov 2025, `data/derived/ecosim/snapshots/`).
Diciembre 2025 no se lee (sellado). Escribe `checks_datos.json` y las figuras
`viajes_por_dia.png`, `viajes_por_hora.png`, `feed_cadencia.png`.

uv run python report/figs/checks_datos.py
"""
import json
import re

import numpy as np
import pandas as pd

from _style import C1, C2, C3, DATA, FIGS, GRAY, plt, save

import sys
sys.path.insert(0, str(FIGS.parent.parent))
from ecosim.days import day_type  # noqa: E402  (solo lectura: festivos MX)

out = {}

# ---------------------------------------------------------------- viajes
t = pd.read_parquet(DATA / "trips_2025_01_11.parquet")
t = t[(t.t_dep >= "2025-01-01") & (t.t_dep < "2025-12-01")]
num = re.compile(r"^\d{3}(-\d{3})?$")
out["viajes"] = int(len(t))
st = pd.unique(pd.concat([t.o, t.d]))
out["estaciones_distintas"] = int(sum(bool(num.match(str(s))) for s in st))
out["estaciones_no_numericas"] = sorted(str(s) for s in st if not num.match(str(s)))
out["bicis_distintas"] = int(t.bike.nunique())
dur = (t.t_arr - t.t_dep).dt.total_seconds() / 60
out["duracion_mediana_min"] = float(dur.median())
out["duracion_p95_min"] = float(dur.quantile(.95))
out["viajes_mas_de_1_dia"] = int((dur > 1440).sum())

day = t.t_dep.dt.normalize()
per_day = day.value_counts().sort_index()
per_day.index = pd.to_datetime(per_day.index)
full = pd.date_range("2025-01-01", "2025-11-30")
per_day = per_day.reindex(full, fill_value=0)
out["dias"] = len(per_day)
out["viajes_por_dia_media"] = float(per_day.mean())
out["viajes_por_dia_mediana"] = float(per_day.median())
out["viajes_por_dia_min"] = [str(per_day.idxmin().date()), int(per_day.min())]
out["viajes_por_dia_max"] = [str(per_day.idxmax().date()), int(per_day.max())]
types = pd.Series([day_type(d.date()) for d in per_day.index], index=per_day.index)
out["viajes_por_dia_por_tipo"] = {k: float(v) for k, v in per_day.groupby(types).mean().items()}
out["dias_por_tipo"] = {k: int(v) for k, v in types.value_counts().items()}
dow = per_day.groupby(per_day.index.dayofweek).mean()
out["viajes_por_dia_semana"] = {["lun", "mar", "mié", "jue", "vie", "sáb", "dom"][i]: float(v) for i, v in dow.items()}
# semanas completas lunes-domingo
wk = per_day.resample("W-SUN").agg(["sum", "count"])
wk = wk[wk["count"] == 7]["sum"]
out["viajes_por_semana"] = {"media": float(wk.mean()), "min": [str(wk.idxmin().date()), int(wk.min())],
                            "max": [str(wk.idxmax().date()), int(wk.max())], "n_semanas": int(len(wk))}
month = per_day.groupby(per_day.index.month).sum()
out["viajes_por_mes"] = {int(k): int(v) for k, v in month.items()}

hour = t.t_dep.dt.floor("h").value_counts()
out["max_viajes_por_hora"] = [str(hour.idxmax()), int(hour.max())]
hr = t.t_dep.dt.hour.to_numpy()
is_wd = (t.t_dep.dt.normalize().map(types) == "weekday").to_numpy()
prof = pd.DataFrame({"h": hr, "wd": is_wd}).groupby(["wd", "h"]).size().unstack(0).reindex(range(24)).fillna(0)
nd = types.value_counts()
prof[True] /= nd.get("weekday", 1)
prof[False] /= (nd.sum() - nd.get("weekday", 0))
out["perfil_hora_entre_semana_pico"] = [int(prof[True].idxmax()), float(prof[True].max())]
out["perfil_hora_fin_semana_pico"] = [int(prof[False].idxmax()), float(prof[False].max())]
out["viajes_00_00_a_00_30_por_dia"] = float(((t.t_dep.dt.hour == 0) & (t.t_dep.dt.minute < 30)).sum() / len(per_day))
out["viajes_00_30_a_05_00_por_dia"] = float((((t.t_dep.dt.hour == 0) & (t.t_dep.dt.minute >= 30)) |
                                             (t.t_dep.dt.hour.between(1, 4))).sum() / len(per_day))

fig, ax = plt.subplots()
ax.plot(per_day.index, per_day.values / 1000, color=C1, lw=1.2)
ax.plot(per_day.index, per_day.rolling(7, center=True).mean() / 1000, color=C2)
ax.set_ylabel("miles de viajes")
ax.set_title("Viajes por día, 2025")
ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%b"))
save(fig, "viajes_por_dia")

fig, ax = plt.subplots()
ax.plot(prof.index, prof[True] / 1000, color=C1, label="entre semana")
ax.plot(prof.index, prof[False] / 1000, color=C3, label="fin de semana/festivo")
ax.set_xlabel("hora de salida")
ax.set_ylabel("miles de viajes")
ax.set_title("Viajes por hora (media por día)")
ax.set_xticks(range(0, 24, 3))
ax.legend(fontsize=20, frameon=False)
save(fig, "viajes_por_hora")

# ---------------------------------------------------------------- feed GBFS
files = sorted((DATA / "snapshots").glob("2025-*.parquet"))
assert all(f.stem < "2025-12-01" for f in files)
gaps, close, n_rows, commits_day, stations_day = [], [], 0, [], []
for f in files:
    s = pd.read_parquet(f, columns=["short_name", "t", "is_renting", "bikes", "disabled"])
    n_rows += len(s)
    c = np.sort(s.t.unique())
    commits_day.append(len(c))
    stations_day.append(s.short_name.nunique())
    tt = pd.to_datetime(c)
    g = pd.DataFrame({"t": tt[1:], "gap": np.diff(tt) / pd.Timedelta(minutes=1)})
    gaps.append(g)
    agg = s.groupby("t").agg(renta=("is_renting", "mean"), disp=("bikes", "sum"), dan=("disabled", "sum"))
    close.append(agg)
gaps = pd.concat(gaps)
close = pd.concat(close)
out["snapshots_dias"] = len(files)
out["snapshots_renglones"] = int(n_rows)
out["commits_por_dia_media"] = float(np.mean(commits_day))
out["estaciones_por_dia"] = [int(min(stations_day)), int(max(stations_day))]
out["hueco_mediana_min"] = float(gaps.gap.median())
out["hueco_p90_min"] = float(gaps.gap.quantile(.9))
gaps["h"] = gaps.t.dt.hour
gh = gaps.groupby("h").gap.median()
out["hueco_mediana_por_hora"] = {int(k): float(v) for k, v in gh.items()}
out["hueco_mediana_18_21"] = float(gaps[gaps.h.between(18, 21)].gap.median())
out["hueco_mediana_resto"] = float(gaps[~gaps.h.between(18, 21)].gap.median())
# cierre de las 00:30: commits entre 00:00 y 00:30 vs entre 00:30 y 01:00
ct = close.index.to_series()
m = ct.dt.hour == 0
pre, post = close[m & (ct.dt.minute < 30)], close[m & (ct.dt.minute >= 30)]
out["cierre"] = {
    "renta_00_00_00_30": float(pre.renta.mean()), "renta_00_30_01_00": float(post.renta.mean()),
    "disponibles_00_00_00_30": float(pre.disp.mean()), "disponibles_00_30_01_00": float(post.disp.mean()),
    "danadas_00_00_00_30": float(pre.dan.mean()), "danadas_00_30_01_00": float(post.dan.mean()),
}

fig, ax = plt.subplots()
order = list(range(5, 24)) + [0]
ax.bar(range(len(order)), [gh.get(h, np.nan) for h in order], color=[C2 if 18 <= h <= 21 else C1 for h in order])
ax.axhline(15, color=GRAY, ls="--", lw=1.5)
ax.set_xticks(range(0, len(order), 3))
ax.set_xticklabels([f"{order[i]:02d}" for i in range(0, len(order), 3)])
ax.set_xlabel("hora del día")
ax.set_ylabel("minutos")
ax.set_title("Hueco mediano entre snapshots")
save(fig, "feed_cadencia")

(FIGS / "checks_datos.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(json.dumps(out, indent=1, ensure_ascii=False))
