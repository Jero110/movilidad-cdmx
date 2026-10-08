"""Tabla y macros del costo por km (α) para reporte-caso.

Lee las corridas de la media móvil con α en los días de prueba (alfa-prueba-121dias.csv, generado por
alfa_prueba.py con el greedy de producción; α = 0 coincide día por día con resultados.csv), Ecobici de
resultados.csv y la cota inferior de km de Ecobici (eco-km-cota.csv, de eco_km_cota.py).

Uso (desde la raíz del repo): uv run python3 report/figs/alfa_caso.py
Escribe report/tablas/alfa-caso.tex y report/numeros-alfa.tex.
"""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "docs/planner/runs/ecosim-greedy"
a = pd.read_csv(RUNS / "alfa-prueba-121dias.csv")
k = pd.read_csv(RUNS / "eco-km-cota.csv")
r = pd.read_csv(ROOT / "ecosim/results/resultados.csv")
r = r[r.tag == "prueba"]
eco = r[r.arm == "ecobici"].set_index("day")
ma = r[r.arm == "ma_diaria"].set_index("day")

dias = sorted(eco.index)
assert len(dias) == 121 and a.groupby("alfa").day.nunique().eq(121).all() and set(k.day) == set(dias)
# α = 0 tiene que ser la media móvil de la tabla principal, día por día.
z = a[a.alfa == 0].set_index("day").loc[dias]
assert (z.EF == ma.loc[dias, "EF"]).all() and (z.bicis_movidas == ma.loc[dias, "bicis_movidas"]).all()
assert (z.desvios == ma.loc[dias, "desvios_salida"] + ma.loc[dias, "desvios_llegada"]).all()

E = {"EF": eco.EF.mean(), "bicis": eco.bicis_movidas.mean(), "km": k.km_tramos_min.mean(),
     "desv": (eco.desvios_salida + eco.desvios_llegada).mean()}
g = a.groupby("alfa").agg(EF=("EF", "mean"), bicis=("bicis_movidas", "mean"), km=("km_tramos", "mean"),
                          desv=("desvios", "mean"), vis=("visitas", "mean"))
b = g.loc[0.0]
fmt = lambda x: f"{x:,.0f}"
pct = lambda x, y: f"{100 * (x / y - 1):+.1f}".replace("+", "$+$").replace("-", "$-$")
cols = ["EF", "bicis", "km", "desv"]
lines = [r"\begin{tabular}{@{}l" + "rrr" * 4 + "@{}}", r"\toprule",
         r" & \multicolumn{3}{c}{$\EF$} & \multicolumn{3}{c}{Bicis movidas} & \multicolumn{3}{c}{Km de tramos} & \multicolumn{3}{c}{Desvíos} \\",
         r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}\cmidrule(l){11-13}",
         r" & Por día & vs.\ $\alpha{=}0$ & vs.\ Eco. & Por día & vs.\ $\alpha{=}0$ & vs.\ Eco. & Por día & vs.\ $\alpha{=}0$ & vs.\ Eco. & Por día & vs.\ $\alpha{=}0$ & vs.\ Eco. \\",
         r"\midrule",
         "Ecobici (medido) & " + " & ".join(f"{'$\\ge$' if c == 'km' else ''}{fmt(E[c])} & --- & ---" for c in cols) + r" \\", r"\midrule"]
for al, row in g.iterrows():
    cells = [f"{fmt(row[c])} & {'---' if al == 0 else pct(row[c], b[c])} & {pct(row[c], E[c])}" for c in cols]
    lines.append(f"Media móvil, $\\alpha={al:g}$ & " + " & ".join(cells) + r" \\")
lines += [r"\bottomrule", r"\end{tabular}", ""]
(ROOT / "report/tablas/alfa-caso.tex").write_text("\n".join(lines))

p = lambda al, c, ref: f"{abs(100 * (g.loc[al, c] / ref - 1)):.1f}"
mac = {"caDias": len(dias), "caAlfas": ", ".join(f"{x:g}" for x in g.index),
       "caCeroKm": fmt(b.km), "caEcoKm": fmt(E["km"]),
       "caUnoKm": p(1.0, "km", b.km), "caUnoEF": p(1.0, "EF", b.EF),
       "caDiezKm": p(10.0, "km", b.km), "caDiezEF": p(10.0, "EF", b.EF), "caDiezEco": p(10.0, "EF", E["EF"]),
       "caDiezBicis": p(10.0, "bicis", b.bicis), "caDiezDesv": p(10.0, "desv", b.desv), "caDiezVis": p(10.0, "vis", b.vis),
       "caDiezKmEco": p(10.0, "km", E["km"]), "caEcoEmpar": f"{100 * k.emparejadas.sum() / k.recogidas.sum():.0f}"}
(ROOT / "report/numeros-alfa.tex").write_text("".join(f"\\newcommand{{\\{n}}}{{{v}}}\n" for n, v in mac.items()))
print(g.round(1).to_string()); print(mac)
