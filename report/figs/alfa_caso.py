"""Tabla y macros del costo por km (α) para reporte-caso. Con α > 0 va el tope duro de 45 min por tramo; α = 0 no lleva
tope y es la media móvil de la tabla principal.

Lee las corridas de la media móvil con α y tope en los días de prueba (alfa-tope45-prueba-121dias.csv, generado por
alfa_tope_prueba.py; de ahí solo se usa α > 0), las mismas corridas sin tope (alfa-prueba-121dias.csv, de alfa_prueba.py con el greedy de
producción; α = 0 coincide día por día con resultados.csv), Ecobici de resultados.csv y la cota inferior de km de
Ecobici (eco-km-cota.csv, de eco_km_cota.py).

Uso (desde la raíz del repo): uv run python3 report/figs/alfa_caso.py
Escribe report/tablas/alfa-caso.tex y report/numeros-alfa.tex.
"""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "docs/planner/runs/ecosim-greedy"
a = pd.read_csv(RUNS / "alfa-tope45-prueba-121dias.csv")
s = pd.read_csv(RUNS / "alfa-prueba-121dias.csv")
k = pd.read_csv(RUNS / "eco-km-cota.csv")
r = pd.read_csv(ROOT / "ecosim/results/resultados.csv")
r = r[r.tag == "prueba"]
eco = r[r.arm == "ecobici"].set_index("day")
ma = r[r.arm == "ma_diaria"].set_index("day")

dias = sorted(eco.index)
assert len(dias) == 121 and set(k.day) == set(dias)
assert a.groupby("alfa").day.nunique().eq(121).all() and s.groupby("alfa").day.nunique().eq(121).all()
assert set(a.alfa) == set(s.alfa) - {0.0} and not a.duplicated(["day", "alfa"]).any()
# α = 0 sin tope tiene que ser la media móvil de la tabla principal, día por día.
z = s[s.alfa == 0].set_index("day").loc[dias]
assert (z.EF == ma.loc[dias, "EF"]).all() and (z.bicis_movidas == ma.loc[dias, "bicis_movidas"]).all()

E = {"EF": eco.EF.mean(), "bicis": eco.bicis_movidas.mean(), "km": k.km_tramos_min.mean(),
     "desv": (eco.desvios_salida + eco.desvios_llegada).mean(), "vis": eco.visitas.mean()}
agg = dict(EF=("EF", "mean"), bicis=("bicis_movidas", "mean"), km=("km_tramos", "mean"),
           desv=("desvios", "mean"), vis=("visitas", "mean"))
gs = s.groupby("alfa").agg(**agg)
# α = 0 sin tope (tabla principal); α > 0 con tope.
g = pd.concat([gs.loc[[0.0]], a[a.alfa > 0].groupby("alfa").agg(**agg)])
b = g.loc[0.0]
fmt = lambda x: f"{x:,.0f}"
pct = lambda x, y: f"{100 * (x / y - 1):+.1f}".replace("+", "$+$").replace("-", "$-$")
cols = ["EF", "vis", "bicis", "km", "desv"]
lines = [r"\begin{tabular}{@{}l" + "rrr" * 5 + "@{}}", r"\toprule",
         r" & \multicolumn{3}{c}{$\EF$} & \multicolumn{3}{c}{Visitas} & \multicolumn{3}{c}{Bicis movidas} & \multicolumn{3}{c}{Km de tramos} & \multicolumn{3}{c}{Desvíos} \\",
         r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}\cmidrule(lr){11-13}\cmidrule(l){14-16}",
         r" & Por día" + r" & vs.\ $\alpha{=}0$ & vs.\ Eco. & Por día" * 4 + r" & vs.\ $\alpha{=}0$ & vs.\ Eco. \\",
         r"\midrule",
         "Ecobici (medido) & " + " & ".join(f"{'$\\ge$' if c == 'km' else ''}{fmt(E[c])} & --- & ---" for c in cols) + r" \\", r"\midrule"]
for al, row in g.iterrows():
    cells = [f"{fmt(row[c])} & {'---' if al == 0 else pct(row[c], b[c])} & {pct(row[c], E[c])}" for c in cols]
    lines.append(f"Media móvil, $\\alpha={al:g}$ & " + " & ".join(cells) + r" \\")
lines += [r"\bottomrule", r"\end{tabular}", ""]
(ROOT / "report/tablas/alfa-caso.tex").write_text("\n".join(lines))


def tope_vs_sin(al):
    """Cambio de E+F por el tope con el mismo α: % de la media y media ± IC95 pareado por día."""
    x = a[a.alfa == al].set_index("day").loc[dias].EF
    y = s[s.alfa == al].set_index("day").loc[dias].EF
    d = x - y
    return 100 * (x.mean() / y.mean() - 1), d.mean(), 1.96 * d.std() / np.sqrt(len(d))


p = lambda al, c, ref: f"{abs(100 * (g.loc[al, c] / ref - 1)):.1f}"
sg = lambda x: f"{x:+.1f}".replace("+", "$+$").replace("-", "$-$")
sg0 = lambda x: f"{x:+,.0f}".replace("+", "$+$").replace("-", "$-$")
t10 = tope_vs_sin(10.0)
mac = {"caDias": len(dias), "caAlfas": ", ".join(f"{x:g}" for x in g.index),
       "caCeroKm": fmt(b.km), "caEcoKm": fmt(E["km"]), "caCeroEco": p(0.0, "EF", E["EF"]),
       "caUnoKm": p(1.0, "km", b.km), "caUnoEF": p(1.0, "EF", b.EF),
       "caDiezKm": p(10.0, "km", b.km), "caDiezEF": p(10.0, "EF", b.EF), "caDiezEco": p(10.0, "EF", E["EF"]),
       "caDiezBicis": p(10.0, "bicis", b.bicis), "caDiezDesv": p(10.0, "desv", b.desv), "caDiezVis": p(10.0, "vis", b.vis), "caDiezVisEco": p(10.0, "vis", E["vis"]),
       "caDiezKmEco": p(10.0, "km", E["km"]), "caEcoEmpar": f"{100 * k.emparejadas.sum() / k.recogidas.sum():.0f}",
       # lo que cambia E+F con α = 10 al poner el tope
       "caTopeDiezEF": sg(t10[0]), "caTopeDiezAbs": sg0(t10[1]), "caTopeDiezIC": fmt(t10[2])}
(ROOT / "report/numeros-alfa.tex").write_text("".join(f"\\newcommand{{\\{n}}}{{{v}}}\n" for n, v in mac.items()))
print(g.round(1).to_string()); print(gs.round(1).to_string()); print(mac)
