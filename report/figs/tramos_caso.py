"""Tiempo de los tramos donante → receptor (logística) para reporte-caso.

El tope duro de 45 min solo va con α > 0; α = 0 es la media móvil de la tabla principal, sin tope.

Entradas, en docs/planner/runs/ecosim-greedy: alfa-tope45-tramos-121dias.csv.gz (alfa_tope_prueba.py con tope de 45, α = 1, 3, 5 y 10 con
tope, un renglón por tramo), tramos-prueba-121dias.csv.gz (tramos_prueba.py, α = 0 y 10 sin
tope; α = 10 sin tope solo entra al texto como motivo del tope) y osrm-matriz.npz (osrm_matriz.py, distancia por calle en
OSRM sin tráfico).

Tiempo de un tramo = km por calle x minutos por km de TomTom (Traffic Index 2025, Ciudad de México) según la hora de salida
(emisión + 15 min): 4.17 de 07:00 a 09:59, 4.73 de 17:00 a 19:59 y 3.45 el resto (promedio de todo el día). Las horas pico
son un supuesto nuestro; TomTom no da horas fijas para la ciudad.

Uso (desde la raíz del repo): uv run python3 report/figs/tramos_caso.py
Escribe report/tablas/tramos-caso.tex, report/tablas/tramos-apendice-caso.tex y report/numeros-tramos.tex.
"""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "docs/planner/runs/ecosim-greedy"
MPK_AM, MPK_PM, MPK_DIA = 4.17, 4.73, 3.45
TOPE_MIN = 45
DIAS = 121
ALFAS = {0.0: "Cero", 1.0: "Uno", 3.0: "Tres", 5.0: "Cinco", 10.0: "Diez"}

M = np.load(RUNS / "osrm-matriz.npz")
IX = {s: i for i, s in enumerate(M["ids"])}
ROAD = M["dist_m"] / 1000

u = pd.read_csv(RUNS / "alfa-tope45-tramos-121dias.csv.gz", dtype={"donante": str, "receptor": str})
t = pd.read_csv(RUNS / "tramos-prueba-121dias.csv.gz", dtype={"donante": str, "receptor": str})
t["road_km"] = ROAD[t.donante.map(IX).to_numpy(), t.receptor.map(IX).to_numpy()]
assert set(u.alfa) == set(ALFAS) - {0.0} and u.groupby("alfa").day.nunique().eq(DIAS).all()
assert t.groupby("alfa").day.nunique().eq(DIAS).all()


def minutos(d):
    h = (pd.to_datetime(d.issued_at) + pd.Timedelta(minutes=15)).dt.hour
    mpk = np.where((h >= 7) & (h < 10), MPK_AM, np.where((h >= 17) & (h < 20), MPK_PM, MPK_DIA))
    return d.road_km * mpk


def stats(d):
    m = minutos(d)
    cerca = d.km >= 0.2  # el cociente calle / recta no tiene sentido por debajo de 200 m
    return dict(n=len(d), km=d.road_km.mean(), rodeo=(d.road_km[cerca] / d.km[cerca]).mean(), min=m.mean(),
                p95=m.quantile(.95), p99=m.quantile(.99), max=m.max(), n45=(m > 45).sum(), n60=(m > 60).sum(),
                p45=100 * (m > 45).mean(), p60=100 * (m > 60).mean(), kmmax=d.road_km.max())


SIN = {al: stats(t[t.alfa == al]) for al in (0.0, 10.0)}
S = {al: SIN[0.0] if al == 0 else stats(u[u.alfa == al]) for al in ALFAS}
assert all(S[al]["max"] <= TOPE_MIN + 1e-9 for al in ALFAS if al > 0)

f0 = lambda x: f"{x:,.0f}"
f1 = lambda x: f"{x:.1f}"
f2 = lambda x: f"{x:.2f}"
head = [r"\begin{tabular}{@{}l" + "r" * len(ALFAS) + "@{}}", r"\toprule",
        r"$\alpha$ & " + " & ".join(f"{al:g}" for al in ALFAS) + r" \\", r"\midrule"]
tail = [r"\bottomrule", r"\end{tabular}", ""]
row = lambda name, g: f"{name} & " + " & ".join(g(S[al]) for al in ALFAS) + r" \\"
body = [("Calle / línea recta", lambda s: f2(s["rodeo"])),
        ("Minutos, promedio", lambda s: f0(s["min"])),
        ("Minutos, percentil 95", lambda s: f0(s["p95"])),
        ("Minutos, máximo", lambda s: f0(s["max"])),
        (r"Más de 45 min, \%", lambda s: f1(s["p45"]))]
(ROOT / "report/tablas/tramos-caso.tex").write_text("\n".join(head + [row(n, g) for n, g in body] + tail))

body2 = [("Tramos en 121 días", lambda s: f0(s["n"])),
         ("Tramos por día", lambda s: f0(s["n"] / DIAS)),
         ("Km por calle, promedio", lambda s: f1(s["km"])),
         ("Km por calle, máximo", lambda s: f1(s["kmmax"])),
         ("Minutos, percentil 99", lambda s: f0(s["p99"])),
         ("Más de 45 min, tramos", lambda s: f0(s["n45"])),
         ("Más de 45 min, por día", lambda s: f1(s["n45"] / DIAS)),
         ("Más de 60 min, tramos", lambda s: f0(s["n60"])),
         ("Más de 60 min, por día", lambda s: f1(s["n60"] / DIAS))]
(ROOT / "report/tablas/tramos-apendice-caso.tex").write_text("\n".join(head + [row(n, g) for n, g in body2] + tail))

mac = {"tfMpkAm": f"{MPK_AM}", "tfMpkPm": f"{MPK_PM}", "tfMpkDia": f"{MPK_DIA}",
       "tfLimMin": f"{TOPE_MIN}", "tfLimKm": f1(TOPE_MIN / MPK_PM), "tfEstaciones": f0(len(IX))}
for al, k in ALFAS.items():
    s = S[al]
    mac.update({f"tf{k}Rodeo": f2(s["rodeo"]), f"tf{k}Max": f0(s["max"]), f"tf{k}KmMax": f1(s["kmmax"]),
                f"tf{k}PCuarentaCinco": f1(s["p45"]), f"tf{k}Min": f0(s["min"]), f"tf{k}PNoventaCinco": f0(s["p95"]),
                f"tf{k}NoDia": f0(s["n45"] / DIAS), f"tf{k}Km": f1(s["km"]), f"tf{k}PSesenta": f2(s["p60"]),
                f"tf{k}SesentaDia": f1(s["n60"] / DIAS)})
for al, k in ((0.0, "Cero"), (10.0, "Diez")):
    s = SIN[al]
    mac.update({f"tfSin{k}Max": f0(s["max"]), f"tfSin{k}KmMax": f1(s["kmmax"]), f"tfSin{k}Min": f0(s["min"]),
                f"tfSin{k}PCuarentaCinco": f1(s["p45"]), f"tfSin{k}PSesenta": f2(s["p60"]),
                f"tfSin{k}SesentaDia": f1(s["n60"] / DIAS), f"tfSin{k}PNoventaCinco": f0(s["p95"])})
(ROOT / "report/numeros-tramos.tex").write_text("".join(f"\\newcommand{{\\{n}}}{{{v}}}\n" for n, v in mac.items()))
print(pd.DataFrame({**{f"tope {al:g}": S[al] for al in ALFAS}, **{f"sin {al:g}": SIN[al] for al in SIN}}).round(2).to_string())
