"""Figura, tabla y macros del ritmo del rebalanceo (apéndice de reporte-caso).

Lee docs/planner/runs/ecosim-greedy/ritmo-121dias.csv (ritmo_prueba.py: visitas y bicis aplicadas por cuarto de
hora, Ecobici medido y media móvil con α = 0) y compara las dos distribuciones en ventanas móviles de 15 min,
30 min, 1 h y 3 h dentro de cada día (una ventana por cada cuarto de hora de inicio) y en el día completo.

Uso (desde la raíz): uv run python3 report/figs/ritmo_caso.py
Escribe report/figs/caso-ritmo.pdf, report/tablas/ritmo-caso.tex y report/numeros-ritmo.tex.
"""
import io
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "report"
BLUE, GRAY, INK = "#2a78d6", "#8a8a85", "#3d3d3a"
VENT = [(1, "15 min"), (2, "30 min"), (4, "1 h"), (12, "3 h"), (78, "Día completo")]
df = pd.read_csv(ROOT / "docs/planner/runs/ecosim-greedy/ritmo-121dias.csv").sort_values(["arm", "day", "k"])
dias = df.day.nunique()
assert dias == 121 and df.groupby(["arm", "day"]).size().eq(78).all()
r = pd.read_csv(ROOT / "ecosim/results/resultados.csv")
r = r[r.tag == "prueba"].set_index(["arm", "day"])
tot = df.groupby(["arm", "day"])[["visitas", "bicis"]].sum()
# Lo aplicado cuadra con los resultados de la prueba: bicis aplicadas = bicis movidas (salvo lo recortado al recoger).
assert (tot.loc["ecobici", "bicis"] <= r.loc["ecobici", "bicis_movidas"].loc[tot.loc["ecobici"].index]).all()

def ventanas(arm, col, n):
    out = []
    for _, g in df[df.arm == arm].groupby("day"):
        v = g[col].to_numpy()
        out.append(np.convolve(v, np.ones(n), "valid"))
    return np.concatenate(out)

plt.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "Times"], "font.size": 8,
                     "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": GRAY,
                     "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK, "pdf.fonttype": 42})
fig, axes = plt.subplots(2, 5, figsize=(7.1, 3.0))
stats = {}
for r_, (col, lab) in enumerate([("visitas", "Visitas"), ("bicis", "Bicis movidas")]):
    for c, (n, vlab) in enumerate(VENT):
        e, m = ventanas("ecobici", col, n), ventanas("ma_diaria", col, n)
        stats[(col, vlab)] = {k: (np.quantile(e, q), np.quantile(m, q)) for k, q in (("p50", .5), ("p95", .95), ("max", 1))}
        # La última barra junta los valores mayores al percentil 99.5 de las dos series (cola larga de Ecobici).
        top = np.quantile(np.concatenate([e, m]), .995) if n < 78 else max(e.max(), m.max())
        edges = np.linspace(0, top * 1.001, 24 if n < 78 else 16)
        e, m = np.minimum(e, edges[-1] * .999), np.minimum(m, edges[-1] * .999)
        w = 100 / len(e)
        ax = axes[r_, c]
        ax.hist(e, bins=edges, weights=np.full(len(e), w), color=GRAY, alpha=.45, linewidth=0, label="Ecobici (medido)")
        ax.hist(m, bins=edges, weights=np.full(len(m), w), histtype="step", color=BLUE, linewidth=1.3, label="Media móvil, $\\alpha=0$")
        for v, cc in ((np.median(e), GRAY), (np.median(m), BLUE)):
            ax.axvline(v, color=cc, lw=.8, ls=(0, (3, 2)))
        ax.tick_params(length=2, labelsize=6.5)
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(3))
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        if r_ == 0: ax.set_title(vlab, fontsize=8, color=INK)
        if c == 0: ax.set_ylabel(f"{lab}, % de ventanas", fontsize=7)
h, l = axes[0, 0].get_legend_handles_labels()
fig.legend(h, l, fontsize=7, frameon=False, loc="upper center", ncol=2, bbox_to_anchor=(.5, 1.0))
fig.subplots_adjust(left=.07, right=.99, top=.84, bottom=.09, wspace=.32, hspace=.42)
buf = io.BytesIO(); fig.savefig(buf, format="pdf", metadata={"CreationDate": None, "Creator": None, "Producer": None})
(OUT / "figs/caso-ritmo.pdf").write_bytes(buf.getvalue())

f0 = lambda x: f"{x:,.0f}"
lines = [r"\begin{tabular}{@{}l" + "rr" * 6 + "@{}}", r"\toprule",
         r" & \multicolumn{6}{c}{Visitas} & \multicolumn{6}{c}{Bicis movidas} \\",
         r"\cmidrule(lr){2-7}\cmidrule(l){8-13}",
         r" & \multicolumn{2}{c}{Mediana} & \multicolumn{2}{c}{p95} & \multicolumn{2}{c}{Máximo} & \multicolumn{2}{c}{Mediana} & \multicolumn{2}{c}{p95} & \multicolumn{2}{c}{Máximo} \\",
         r"Ventana" + " & Eco. & MM" * 6 + r" \\", r"\midrule"]
for _, vlab in VENT:
    cells = [f"{f0(stats[(col, vlab)][k][0])} & {f0(stats[(col, vlab)][k][1])}" for col in ("visitas", "bicis") for k in ("p50", "p95", "max")]
    lines.append(f"{vlab} & " + " & ".join(cells) + r" \\")
lines += [r"\bottomrule", r"\end{tabular}", ""]
(OUT / "tablas/ritmo-caso.tex").write_text("\n".join(lines))

s = stats
mac = {"rtDias": dias,
       "rtQuinceEcoMax": f0(s[("visitas", "15 min")]["max"][0]), "rtQuinceMaMax": f0(s[("visitas", "15 min")]["max"][1]),
       "rtQuinceEcoP": f0(s[("visitas", "15 min")]["p95"][0]), "rtQuinceMaP": f0(s[("visitas", "15 min")]["p95"][1]),
       "rtHoraEcoP": f0(s[("visitas", "1 h")]["p95"][0]), "rtHoraMaP": f0(s[("visitas", "1 h")]["p95"][1]),
       "rtTresEcoMax": f0(s[("visitas", "3 h")]["max"][0]), "rtTresMaMax": f0(s[("visitas", "3 h")]["max"][1]),
       "rtTresBEcoP": f0(s[("bicis", "3 h")]["p95"][0]), "rtTresBMaP": f0(s[("bicis", "3 h")]["p95"][1]),
       "rtQuinceEcoMed": f0(s[("visitas", "15 min")]["p50"][0]), "rtQuinceMaMed": f0(s[("visitas", "15 min")]["p50"][1])}
(OUT / "numeros-ritmo.tex").write_text("".join(f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in mac.items()))
for k, v in stats.items(): print(k, {a: tuple(round(x) for x in b) for a, b in v.items()})
