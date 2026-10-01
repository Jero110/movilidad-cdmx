"""Figuras y números de resultados del run 2 (curva de λ y brazos por franja).

Lee los resultados del run 2 (solo lectura) de `RESULTS` (`_style.py`; por defecto
`.worktrees/ecosim2-integracion/ecosim/results`): `frozen.json`, `resultados.csv`, y el replay de
Ecobici en días de selección que calcula `ecobici_seleccion.py` (`ecobici_seleccion.csv`).
Escribe `lambda_curva.png`, `brazos_franja.png` y `resultados_run2.json`.

uv run python report/figs/resultados_run2.py
"""
import json

import numpy as np
import pandas as pd

from _style import C1, C2, C3, C4, FIGS, GRAY, RESULTS, plt, save

frozen = json.loads((RESULTS / "frozen.json").read_text())
res = pd.read_csv(RESULTS / "resultados.csv", dtype={"day": str})
sel = pd.read_csv(FIGS / "ecobici_seleccion.csv", dtype={"day": str})
out = {"results_dir": str(RESULTS)}

# ------------------------------------------------ curva de λ (oracle, f60, H=2, L=60, selección)
cur = pd.DataFrame(frozen["curva"])
eco_sel = sel[sel.arm == "ecobici"][["EF", "moves", "bikes_moved"]].mean()
base_sel = sel[sel.arm == "baseline"]["EF"].mean()
out["seleccion_ecobici"] = eco_sel.to_dict()
out["seleccion_baseline_EF"] = float(base_sel)
out["lambda_congelado"] = frozen["lam"]
out["retiro"] = frozen["retiro"]
c = cur[cur.retiro == frozen["retiro"]].sort_values("lam")
out["curva_lambda"] = c[["lam", "EF", "moves", "bikes_moved"]].to_dict(orient="records")

fig, ax = plt.subplots()
ax.plot(c.moves / 1000, c.EF / 1000, "o-", color=C1, ms=9)
for _, r in c.iterrows():
    if r.lam in (240, 120):
        continue
    off = {5: (4, -34), 15: (8, 12), 30: (-30, 16), 0: (-50, 12)}.get(int(r.lam), (8, 8))
    ax.annotate(f"λ={r.lam:g}", (r.moves / 1000, r.EF / 1000), textcoords="offset points",
                xytext=off, fontsize=20, color=C1)
k = c[c.lam == frozen["lam"]].iloc[0]
ax.plot(k.moves / 1000, k.EF / 1000, "o", ms=18, mfc="none", mec=C2, mew=3)
ax.plot(eco_sel.moves / 1000, eco_sel.EF / 1000, "*", ms=26, color=C2)
ax.annotate("Ecobici", (eco_sel.moves / 1000, eco_sel.EF / 1000), textcoords="offset points",
            xytext=(10, -28), fontsize=22, color=C2)
ax.set_ylim(0, 100)
ax.set_xlabel("movimientos por día (miles)")
ax.set_ylabel("E+F (miles)")
ax.set_title("Curva de λ (oracle, selección)")
save(fig, "lambda_curva")

# ------------------------------------------------ brazos en evaluación
ev = res[(res.split == "evaluacion") & (res.tag == "eval")]
eco = ev[(ev.arm == "ecobici") & (ev.damage == "auto")].set_index("day")["EF"]
arms = [("ecobici", "Ecobici"), ("daily", "daily"), ("ma", "ma"), ("model", "model"), ("oracle", "oracle")]
rows = {}
for a, lab in arms:
    g = ev[(ev.arm == a) & (ev.damage == "auto")]
    assert g.day.nunique() == 15, (a, g.day.nunique())
    rows[lab] = {
        "EF": g.EF.mean(), "E": g.E.mean(), "F": g.F.mean(),
        "manana": g.EF_manana.mean(), "tarde": g.EF_tarde.mean(), "noche": g.EF_noche.mean(),
        "moves": g.moves.mean(), "bikes_moved": g.bikes_moved.mean(),
        "gana": int((g.set_index("day").EF < eco).sum()) if a != "ecobici" else None,
        "vs_ecobici_pct": float((g.EF.mean() / eco.mean() - 1) * 100),
        "rango_vs_ecobici_pct": [float(((g.set_index("day").EF / eco) - 1).min() * 100),
                                 float(((g.set_index("day").EF / eco) - 1).max() * 100)],
        "f": None if a == "ecobici" else float(g.f.iloc[0]), "H": None if a == "ecobici" else float(g.H.iloc[0]),
        "recorte_bodega": float(g.recorte_bodega.mean()) if "recorte_bodega" in g else None,
        "recorte_por_movimiento_max": float(g.recorte_por_movimiento.max()),
        "fallbacks": float(g.fallbacks.sum()) if a != "ecobici" else None,
    }
base = ev[(ev.arm == "baseline") & (ev.damage == "auto")]
rows["no hacer nada"] = {"EF": base.EF.mean(), "manana": base.EF_manana.mean(), "tarde": base.EF_tarde.mean(),
                         "noche": base.EF_noche.mean(), "moves": 0}
out["brazos_eval"] = rows

fig, ax = plt.subplots()
labs = ["oracle", "daily", "ma", "model", "Ecobici"]
y = np.arange(len(labs))[::-1]
left = np.zeros(len(labs))
for fr, col, name in (("manana", C1, "mañana"), ("tarde", C3, "tarde"), ("noche", C4, "noche")):
    v = np.array([rows[l][fr] for l in labs]) / 1000
    ax.barh(y, v, left=left, color=col, label=name)
    left += v
for yi, l in zip(y, labs):
    ax.text(left[list(y).index(yi)] + 1, yi, f"{rows[l]['EF'] / 1000:.1f}k", va="center", fontsize=20)
ax.set_yticks(y)
ax.set_yticklabels(labs)
ax.set_xlim(0, 125)
ax.set_xlabel("E+F por día (miles de minutos-estación)")
ax.set_title("Brazos en evaluación")
ax.legend(fontsize=20, frameon=False, loc="upper right")
save(fig, "brazos_franja")

(FIGS / "resultados_run2.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=float))
print(json.dumps(out, indent=1, ensure_ascii=False, default=float))
