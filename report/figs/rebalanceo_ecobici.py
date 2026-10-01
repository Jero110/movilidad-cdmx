"""Rebalanceo de Ecobici medido desde GBFS: tope de bicis por movimiento (14/22/42),
rebalanceo a lo largo del día, tope por hora (246) y tope de bodega (632).

Recalcula desde `data/derived/ecosim/ecobici_moves.parquet` (salida de ecosim/medicion.py,
hora corregida −30 s) y, para el run 1, desde la copia congelada
`data/derived/ecosim_run1/bicis_por_movimiento.parquet` (solo lectura).
Escribe `rebalanceo_ecobici.json`, `bicis_por_movimiento.png` y `rebalanceo_por_hora.png`.

uv run python report/figs/rebalanceo_ecobici.py
"""
import json

import numpy as np
import pandas as pd

from _style import C1, C2, C3, DATA, FIGS, GRAY, REPO, plt, save

days = json.loads((REPO / "ecosim" / "days.json").read_text())
EVAL = [d["day"] for d in days["evaluacion"]]
SEL = [d["day"] for d in days["seleccion"]]
out = {}


def pct(a):
    a = np.abs(np.asarray(a))
    return {f"p{q}": float(np.percentile(a, q)) for q in (50, 90, 95, 99)} | {
        "max": int(a.max()), "n": int(len(a)), "media": float(a.mean()),
        "pct_mayor_14": float((a > 14).mean() * 100), "pct_mayor_22": float((a > 22).mean() * 100),
        "pct_mayor_42": float((a > 42).mean() * 100),
        "rango_percentil_de_22": float((a <= 22).mean() * 100),
        "rango_percentil_de_14": float((a <= 14).mean() * 100)}


# ---------- run 1 (mañana 05:30–12:30, 15 días de evaluación; copia congelada)
r1 = pd.read_parquet(REPO / "data" / "derived" / "ecosim_run1" / "bicis_por_movimiento.parquet")
out["run1_manana"] = {arm: pct(g["n"]) for arm, g in r1.groupby("arm")}

# ---------- run 2 (día completo 05:30–00:30)
mv = pd.read_parquet(DATA / "ecobici_moves.parquet")
assert mv["day"].max() < "2025-12-01"
main = mv[mv.delta_rebal.ne(0) & ~mv.undo]
withu = mv[mv.delta_rebal.ne(0)]
out["run2_dia_completo_principal_todos"] = pct(main.delta_rebal)
out["run2_dia_completo_principal_eval"] = pct(main[main.day.isin(EVAL)].delta_rebal)
out["run2_dia_completo_principal_sel"] = pct(main[main.day.isin(SEL)].delta_rebal)
out["run2_dia_completo_con_undo_todos"] = pct(withu.delta_rebal)
h = main.t0.dt.hour + main.t0.dt.minute / 60
man = main[(h >= 5.5) & (h < 12.5)]
out["run2_manana_principal_eval"] = pct(man[man.day.isin(EVAL)].delta_rebal)
out["n_dias_medidos"] = int(mv.day.nunique())

# ---------- tope por hora: movimientos principales con t0 en ventanas de 60 min, inicio cada 15 min
wins = []
for d, g in main.groupby("day"):
    start = pd.Timestamp(d) + pd.Timedelta(hours=5, minutes=30)
    t0 = g.t0.sort_values().to_numpy()
    for k in range(0, 18 * 60 + 1, 15):          # 05:30 … 23:30
        s = start + pd.Timedelta(minutes=k)
        e = s + pd.Timedelta(minutes=60)
        wins.append(int(((t0 >= s.to_datetime64()) & (t0 < e.to_datetime64())).sum()))
wins = np.array(wins)
out["tope_hora"] = {"p95_ceil": int(np.ceil(np.percentile(wins, 95))), "mediana": float(np.median(wins)),
                    "max": int(wins.max()), "p99_ceil": int(np.ceil(np.percentile(wins, 99))), "n_ventanas": len(wins)}

# ---------- tope de bodega: promedio diario de |A − R| del rebalanceo principal
g = main.groupby("day").delta_rebal
A = g.apply(lambda x: x[x > 0].sum())
R = g.apply(lambda x: -x[x < 0].sum())
wh = (A - R)
out["bodega"] = {"media_abs_A_menos_R": float(wh.abs().mean()), "tope_ceil": int(np.ceil(wh.abs().mean())),
                 "dias_positivos": int((wh > 0).sum()), "n_dias": int(len(wh)),
                 "A_media": float(A.mean()), "R_media": float(R.mean()),
                 "movs_por_dia_media": float(main.groupby("day").size().mean()),
                 "movs_por_dia_media_eval": float(main[main.day.isin(EVAL)].groupby("day").size().mean())}
tall = mv.groupby("day").taller_retiro.sum()
out["taller_retiro_por_dia_media"] = float(tall.mean())
out["pares_undo_pct_intervalos"] = float(mv.undo.mean() * 100)

# ---------- rebalanceo por hora del día (bloques de 60 min desde 05:30, media por día)
blk = ((main.t0 - (pd.to_datetime(main.day) + pd.Timedelta(hours=5, minutes=30))) // pd.Timedelta(minutes=60))
main = main.assign(blk=blk.clip(lower=0), A=main.delta_rebal.clip(lower=0), R=(-main.delta_rebal).clip(lower=0))
nd = main.day.nunique()
byb = main.groupby("blk").agg(moves=("delta_rebal", "size"), A=("A", "sum"), R=("R", "sum")) / nd
byb = byb[byb.index < 19]
out["por_bloque"] = byb.round(1).reset_index().to_dict(orient="list")
tot = byb.moves.sum()
out["pct_movs_05_30_12_30"] = float(byb.moves[byb.index < 7].sum() / tot * 100)
out["pct_movs_12_30_18_30"] = float(byb.moves[(byb.index >= 7) & (byb.index < 13)].sum() / tot * 100)
out["pct_movs_18_30_00_30"] = float(byb.moves[byb.index >= 13].sum() / tot * 100)
out["pct_bicis_05_30_12_30"] = float((byb.A + byb.R)[byb.index < 7].sum() / (byb.A + byb.R).sum() * 100)

fig, ax = plt.subplots()
x = np.arange(len(byb))
ax.bar(x - 0.2, byb.A, 0.4, color=C1, label="bicis puestas (A)")
ax.bar(x + 0.2, byb.R, 0.4, color=C2, label="bicis quitadas (R)")
labels = [(pd.Timestamp("2025-01-01 05:30") + pd.Timedelta(hours=int(i))).strftime("%H:%M") for i in byb.index]
ax.set_xticks(x[::3])
ax.set_xticklabels(labels[::3])
ax.set_xlabel("bloque de 60 min")
ax.set_ylabel("bicis por día")
ax.set_title("Rebalanceo de Ecobici por hora")
ax.legend(fontsize=20, frameon=False)
save(fig, "rebalanceo_por_hora")

fig, ax = plt.subplots()
a = np.abs(main.delta_rebal.to_numpy())
bins = np.arange(0.5, 61.5, 1)
ax.hist(np.clip(a, None, 60), bins=bins, color=C1)
ax.set_yscale("log")
for v, c in ((14, C3), (22, C2), (42, GRAY)):
    ax.axvline(v + 0.5, color=c, ls="--", lw=2.5)
    ax.text(v + 1.2, ax.get_ylim()[1] * 0.3, str(v), color=c, fontsize=22)
ax.set_xlabel("bicis en el movimiento (≥ 60 en 60)")
ax.set_ylabel("movimientos")
ax.set_title("Tamaño de las visitas de Ecobici")
save(fig, "bicis_por_movimiento")

(FIGS / "rebalanceo_ecobici.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(json.dumps({k: v for k, v in out.items() if k != "por_bloque"}, indent=1, ensure_ascii=False))
