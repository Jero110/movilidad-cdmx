"""Escribe `report/fuentes-numeros.md`: cada número del PDF con su fuente.

1. Números de resultados y datos: vienen de macros `\\nr{llave}` que genera `numeros.py`;
   aquí se listan con el archivo y el campo de donde salen (`numeros_fuentes.json`).
2. Números escritos en el texto: parámetros de diseño, prensa y reportes de módulo, con su
   fuente (lista mantenida a mano abajo, revisada contra el `.qmd`).

uv run python report/figs/fuentes.py
"""
import json
import re

from _style import FIGS, REPO, RESULTS

REPORT = FIGS.parent
qmd = (REPORT / "reporte-final.qmd").read_text()
src = json.loads((FIGS / "numeros_fuentes.json").read_text())
used = sorted(set(re.findall(r"\\nr\{([^}]*)\}", qmd)), key=lambda k: qmd.index("\\nr{" + k + "}"))
tex_tables = {t: (REPORT / "tablas" / f"{t}.tex").exists() for t in ("brazos", "sensibilidades", "run1", "hsel")}
try:
    results_label = RESULTS.relative_to(REPO)
except ValueError:
    results_label = RESULTS

MANUAL = [
    ("20 millones de personas; transporte concesionado sin registro", "raw/notes/Caso 1 - Documentos de Google.pdf"),
    ("hasta 42 bicis por camioneta con remolque; monitoreo 24 h; balanceadores", "Expansión Política, 2026-08-31 [expansion2026] (verificado con web_fetch_exa)"),
    ("123 subzonas de 3 a 5 estaciones; órdenes por app; zonas valet", "Mexico Business News, 2026-08-12 [mbn2026] (verificado)"),
    ("17% fuera de servicio; 9,308→15,000 bicis; 687→1,111 estaciones; 'flujos de calor'", "Milenio, 2026-08-28 [milenio2026] (verificado)"),
    ("ttl = 10 s; last_updated avanza cada ~10–11 s", "curl a https://gbfs.mex.lyftbikes.com/gbfs/gbfs.json y en/station_status.json, 4 lecturas el 2026-09-29"),
    ("'aproximadamente cada 12 minutos'; repo creado 2023-07-02; Ecobici desde abril de 2024", "README y API de GitHub de MaxHalford/bike-sharing-history (2026-09-29)"),
    ("desfase +30 a +36 s; hora = commit − 30 s", "ecosim/FUNDAMENTOS.md (barrido de desfase) y config.GBFS_COMMIT_LAG_S = 30"),
    ("2.78 millones de saltos; hueco mediano 2.3 h", "wiki/Rebalanceo-Ecobici-suposicion-horario.md (análisis exploratorio previo)"),
    ("regla 1.3", "docs/planner/plans/2026-09-28-ecosim.md §1.3"),
    ("14 = p95 y 22 = p99 (mañana run 1)", "report/figs/rebalanceo_ecobici.json.run1_manana['ecobici sin ±1'] (= docs/executor/runs/2026-09-28-ecosim-run1/bicis_por_movimiento_percentiles.csv)"),
    ("632 y 74 (bodega sin/con taller)", "ecosim/results/medicion/ecobici_stats.json: tope_bodega, mean_abs_stock_warehouse; frozen.json.tope_bodega_taller"),
    ("≤500 m de desvío; L = 60/45/30; bloques de 60 min; paso de 15 min", "ecosim/config.py (DETOUR_RADIUS_M, LEAD_GRID_MIN, BLOCK_MIN, STEP_MIN)"),
    ("15 + 15 días; 11 entre semana + 4 fin de semana; cobertura ≥ 90%", "ecosim/days.json; ecosim/config.py"),
    ("f ∈ {15, 60, 180}; K = 5; clip [0.5, 2]", "ecosim/pronostico.py (REFRESH_MIN, K_SHRINK, FACTOR_CLIP)"),
    ("LightGBM: 200 árboles, tasa 0.05, 31 hojas; rezagos 7/14, media 28; entrenamiento 2025-01-01..2025-08-31", "ecosim/pronostico.py (LGB_PARAMS, FEATURES) y config.TRAIN_START/END"),
    ("22 bicis por orden (cota r_i, u_i); brecha 1e-4; μ = 1", "ecosim/asignador.py; frozen.json.max_move, mu"),
    ("42% del faltante de E de 17:30 a 21:30; huecos de 20–45 min desde las 18:00; hasta 44 min antes", "RESULTS/CONCLUSIONES.md (V1, noche) y RESULTS/v1/v1_diag_bloques.csv"),
    ("bodega explica ~1/3 de la brecha ma–daily (4 días)", "RESULTS/CONCLUSIONES.md, Panorama punto 2 (medición del reviewer de integracion2)"),
    ("MAE salidas 2.053 (daily) vs 2.10 (ma); WAPE ≈ 0.45", "ecosim/results/pronostico/REPORT.md (accuracy_by_lead.csv, evaluación)"),
    ("hubs 273-274, 271-272, 268-269, 176, 261, 014, 022", "RESULTS/donde_falla.csv"),
    ("06:30 primera orden efectiva", "05:30 + L (60 min)"),
    ("90 de 90 corridas idénticas en el replay de la app; 22 corridas en vivo evaluadas, MAE ≈ 2.2 salidas / 2.0 llegadas por estación-hora a 0 h; 232 movimientos efectivos 18:30", "worktree ecosim2-app (commit edc92a7), scripts/ecobici_mapa/screenshots/APP.md y captura 04-prediccion-que-tan-bueno.png"),
    ("feed cada 60 s; asignación cada 15 min; 4 semanas hasta 2026-08-30", "worktree ecosim2-app, scripts/ecobici_mapa/screenshots/APP.md"),
    ("72 decisiones; 3 días × 8 horas × h ∈ {1,3,6}; 23 planes", "ecosim/results/asignador/validation_summary.json"),
    ("run 1: H = 3; daily con bloque 30", "RESULTS/tablas.md (sección run 1) y RESULTS/run1/frozen.json"),
]

out = ["# Fuentes de cada número del reporte final",
       "",
       f"Resultados del run 2 leídos de `{results_label}` (solo lectura). Son los **números finales**: idénticos, archivo por archivo (`diff -rq`), a `ecosim/results/` del commit `6fd68f4` de `ecosim2-integration` (= `7c3f14d` de `ecosim2-integracion`). Regenerar todo:",
       "",
       "```bash",
       "uv run python report/figs/checks_datos.py",
       "uv run python report/figs/rebalanceo_ecobici.py",
       "python3 ../.ecosim2-run/heavy.py uv run python report/figs/ecobici_seleccion.py   # ~15 min",
       "uv run python report/figs/resultados_run2.py",
       "uv run python report/figs/numeros.py",
       "uv run python report/figs/fuentes.py",
       "PATH=/Library/TeX/texbin:$PATH quarto render report/reporte-final.qmd",
       "```",
       "",
       "## 1. Números en macros `\\nr{llave}` (generados por `report/figs/numeros.py`)",
       "",
       "| llave | valor | fuente |", "|---|---|---|"]
for k in used:
    v, s = src.get(k, ("¿?", "NO DEFINIDA"))
    out.append(f"| `{k}` | {v} | {s} |")
out += ["", "## 2. Tablas generadas", "",
        "| tabla | archivo | fuente |", "|---|---|---|",
        "| Tabla brazos | `report/tablas/brazos.tex` | resultados.csv [split=evaluacion, tag=eval], media por día; `resultados_run2.json` |",
        "| Tabla sensibilidades | `report/tablas/sensibilidades.tex` | resultados.csv [tag=sens_* y diag_*] |",
        "| Tabla h (selección) | `report/tablas/hsel.tex` | frozen.json.h_busqueda.EF |",
        "| Tabla run 1 | `report/tablas/run1.tex` | run 1: NOTAS-post-run.md / results/run1/tablas.md; run 2: resultados.csv EF_manana |",
        "| Tabla de checks | en el .qmd con macros `d.*`, `f.*` | `report/figs/checks_datos.json` |",
        "", "## 3. Figuras", "",
        "| figura | script | datos |", "|---|---|---|",
        "| viajes_por_hora.png, viajes_por_dia.png, feed_cadencia.png | checks_datos.py | trips_2025_01_11.parquet; snapshots/ |",
        "| rebalanceo_por_hora.png, bicis_por_movimiento.png | rebalanceo_ecobici.py | ecobici_moves.parquet |",
        "| lambda_curva.png, brazos_franja.png | resultados_run2.py | frozen.json, resultados.csv, ecobici_seleccion.csv |",
        "| app/01..04-*.png | worker ecosim2-app (commit edc92a7) | capturas finales; 03--04 usan `daily` por defecto |",
        "", "## 4. Números escritos en el texto (no macros)", "",
        "| número(s) | fuente |", "|---|---|"]
out += [f"| {a} | {b} |" for a, b in MANUAL]
missing = [k for k in used if k not in src]
out += ["", f"Macros usadas: {len(used)}; sin fuente: {len(missing)} {missing if missing else ''}"]
(REPORT / "fuentes-numeros.md").write_text("\n".join(out) + "\n")
print(f"{len(used)} macros usadas, {len(missing)} sin fuente")
