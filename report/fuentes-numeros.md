# Fuentes de cada número del reporte final

Resultados del run 2 leídos de `ecosim/results` (solo lectura). Son los **números finales**: idénticos, archivo por archivo (`diff -rq`), a `ecosim/results/` del commit `6fd68f4` de `ecosim2-integration` (= `7c3f14d` de `ecosim2-integracion`). Regenerar todo:

```bash
uv run python report/figs/checks_datos.py
uv run python report/figs/rebalanceo_ecobici.py
python3 ../.ecosim2-run/heavy.py uv run python report/figs/ecobici_seleccion.py   # ~15 min
uv run python report/figs/resultados_run2.py
uv run python report/figs/numeros.py
uv run python report/figs/fuentes.py
PATH=/Library/TeX/texbin:$PATH quarto render report/reporte-final.qmd
```

## 1. Números en macros `\nr{llave}` (generados por `report/figs/numeros.py`)

| llave | valor | fuente |
|---|---|---|
| `v1.Eman` | 6.6% | v1/v1_resumen.json[ecobici|auto].mean_abs_rel_E_manana |
| `v1.F` | 5.0% | v1/v1_resumen.json[ecobici|auto].mean_abs_rel_F |
| `v1.E` | 14.1% | v1/v1_resumen.json[ecobici|auto].mean_abs_rel_E |
| `arm.daily.vseco` | 55% | resultados.csv [split=evaluacion, tag=eval, arm=daily] media por día vs Ecobici |
| `arm.daily.gana` | 15 | resultados.csv [split=evaluacion, tag=eval, arm=daily] media por día días con E+F < Ecobici |
| `arm.daily.moves` | 2,502 | resultados.csv [split=evaluacion, tag=eval, arm=daily] media por día |
| `arm.eco.moves` | 2,416 | resultados.csv [split=evaluacion, tag=eval, arm=Ecobici] media por día |
| `arm.oracle.vseco` | 74% | resultados.csv [split=evaluacion, tag=eval, arm=oracle] media por día vs Ecobici |
| `d.pordia` | 55,957 | report/figs/checks_datos.json.viajes_por_dia_media |
| `d.estaciones` | 677 | report/figs/checks_datos.json.estaciones_distintas |
| `v1.Eobs` | 99,047 | v1/v1_resumen.json[ecobici|auto].E_obs |
| `v1.Fobs` | 17,770 | v1/v1_resumen.json[ecobici|auto].F_obs |
| `m.undopct` | 46.5% | report/figs/rebalanceo_ecobici.json.pares_undo_pct_intervalos |
| `val.spearman` | 0.977 | ecosim/results/asignador/validation_summary.json.spearman_hull.mediana |
| `v1.danmae` | 0.04 | v1/v1_resumen.json[ecobici|auto].danadas_mae |
| `f.renglones` | 5,730,807 | report/figs/checks_datos.json.snapshots_renglones |
| `f.dias` | 121 | report/figs/checks_datos.json.snapshots_dias |
| `d.viajes` | 18,689,701 | report/figs/checks_datos.json.viajes |
| `d.bicis` | 7,966 | report/figs/checks_datos.json.bicis_distintas |
| `d.pordiamed` | 60,924 | report/figs/checks_datos.json.viajes_por_dia_mediana |
| `d.wd` | 63,707 | report/figs/checks_datos.json.viajes_por_dia_por_tipo.weekday |
| `d.we` | 38,766 | report/figs/checks_datos.json.viajes_por_dia_por_tipo.weekend |
| `d.hol` | 31,366 | report/figs/checks_datos.json.viajes_por_dia_por_tipo.holiday |
| `d.pordiamindia` | 2025-01-01 | report/figs/checks_datos.json.viajes_por_dia_min |
| `d.pordiamaxdia` | 2025-02-27 | report/figs/checks_datos.json.viajes_por_dia_max |
| `d.pordiamin` | 16,643 | report/figs/checks_datos.json.viajes_por_dia_min |
| `d.pordiamax` | 75,347 | report/figs/checks_datos.json.viajes_por_dia_max |
| `d.nsem` | 47 | report/figs/checks_datos.json.viajes_por_semana.n_semanas |
| `d.sem` | 393,809 | report/figs/checks_datos.json.viajes_por_semana.media |
| `d.semmin` | 322,605 | report/figs/checks_datos.json.viajes_por_semana.min |
| `d.semmax` | 449,325 | report/figs/checks_datos.json.viajes_por_semana.max |
| `d.maxhoraen` | 2025-01-28 18:00 | report/figs/checks_datos.json.max_viajes_por_hora |
| `d.maxhora` | 7,377 | report/figs/checks_datos.json.max_viajes_por_hora |
| `d.durmed` | 11.9 | report/figs/checks_datos.json.duracion_mediana_min |
| `d.durp95` | 36.7 | report/figs/checks_datos.json.duracion_p95_min |
| `d.v0000` | 223 | report/figs/checks_datos.json.viajes_00_00_a_00_30_por_dia |
| `d.v0030` | 0.6 | report/figs/checks_datos.json.viajes_00_30_a_05_00_por_dia |
| `f.commits` | 70 | report/figs/checks_datos.json.commits_por_dia_media |
| `f.huecomed` | 15.9 | report/figs/checks_datos.json.hueco_mediana_min |
| `f.huecop90` | 23.6 | report/figs/checks_datos.json.hueco_p90_min |
| `f.hueco1821` | 35.6 | report/figs/checks_datos.json.hueco_mediana_18_21 |
| `f.huecoresto` | 15.3 | report/figs/checks_datos.json.hueco_mediana_resto |
| `f.rentapre` | 99.8% | report/figs/checks_datos.json.cierre.renta_00_00_00_30 |
| `f.rentapost` | 1.4% | report/figs/checks_datos.json.cierre.renta_00_30_01_00 |
| `f.disppre` | 5,442 | report/figs/checks_datos.json.cierre.disponibles_00_00_00_30 |
| `f.disppost` | 91 | report/figs/checks_datos.json.cierre.disponibles_00_30_01_00 |
| `d.picowd` | 5,550 | report/figs/checks_datos.json.perfil_hora_entre_semana_pico |
| `d.picowdh` | 18 | report/figs/checks_datos.json.perfil_hora_entre_semana_pico |
| `f.danpre` | 1,404 | report/figs/checks_datos.json.cierre.danadas_00_00_00_30 |
| `f.danpost` | 6,826 | report/figs/checks_datos.json.cierre.danadas_00_30_01_00 |
| `m.danos` | 2,562 | ecosim/results/medicion/ecobici_stats.json.mean_danos |
| `m.rep` | 1,865 | ecosim/results/medicion/ecobici_stats.json.mean_reparaciones |
| `m.taller` | 657 | report/figs/rebalanceo_ecobici.json.taller_retiro_por_dia_media |
| `m.dias` | 120 | report/figs/rebalanceo_ecobici.json.n_dias_medidos |
| `m.movs` | 2,246 | report/figs/rebalanceo_ecobici.json.bodega.movs_por_dia_media |
| `m.movsundo` | 4,404 | ecosim/results/medicion/ecobici_stats.json.mean_undo_moves |
| `m.A` | 5,674 | report/figs/rebalanceo_ecobici.json.bodega.A_media |
| `m.R` | 5,043 | report/figs/rebalanceo_ecobici.json.bodega.R_media |
| `m.pctman` | 44% | report/figs/rebalanceo_ecobici.json.pct_movs_05_30_12_30 |
| `m.pcttar` | 35% | report/figs/rebalanceo_ecobici.json.pct_movs_12_30_18_30 |
| `m.pctnoc` | 20% | report/figs/rebalanceo_ecobici.json.pct_movs_18_30_00_30 |
| `bpm.r2.p95` | 14 | report/figs/rebalanceo_ecobici.json.run2_dia_completo_principal_todos.p95 |
| `bpm.r2.p99` | 24 | report/figs/rebalanceo_ecobici.json.run2_dia_completo_principal_todos.p99 |
| `bpm.r2.rank22` | 98.7 | report/figs/rebalanceo_ecobici.json.run2_dia_completo_principal_todos.rango_percentil_de_22 |
| `bpm.r2.gt42` | 0.26% | report/figs/rebalanceo_ecobici.json.run2_dia_completo_principal_todos.pct_mayor_42 |
| `m.topehora` | 246 | report/figs/rebalanceo_ecobici.json.tope_hora.p95_ceil |
| `m.bodega` | 632 | report/figs/rebalanceo_ecobici.json.bodega.tope_ceil |
| `m.topehoramed` | 113 | report/figs/rebalanceo_ecobici.json.tope_hora.mediana |
| `m.topehoramax` | 1,165 | report/figs/rebalanceo_ecobici.json.tope_hora.max |
| `m.bodegamean` | 631.0 | report/figs/rebalanceo_ecobici.json.bodega.media_abs_A_menos_R |
| `m.bodegapos` | 120 | report/figs/rebalanceo_ecobici.json.bodega.dias_positivos |
| `m.stockAR` | $-$25.6 | ecosim/results/medicion/ecobici_stats.json.mean_stock_warehouse |
| `m.bodegataller` | 74 | ecosim/results/medicion/ecobici_stats.json.mean_abs_stock_warehouse |
| `sel.lam` | 60 | frozen.json.lam |
| `lam.5.EF` | 13,079 | frozen.json.curva[retiro=sigma,lam=5].EF |
| `lam.5.moves` | 3,272 | frozen.json.curva[...lam=5].moves |
| `lam.60.EF` | 33,917 | frozen.json.curva[retiro=sigma,lam=60].EF |
| `lam.60.moves` | 1,642 | frozen.json.curva[...lam=60].moves |
| `der.lamratio` | 2.6 | E+F λ60 / λ5 (frozen.json.curva) |
| `der.lammoves` | 2.0 | movs λ5 / λ60 (frozen.json.curva) |
| `selEco.moves` | 2,315 | report/figs/ecobici_seleccion.csv (media arm=ecobici) |
| `lam.30.moves` | 2,338 | frozen.json.curva[...lam=30].moves |
| `lam.30.EF` | 17,424 | frozen.json.curva[retiro=sigma,lam=30].EF |
| `selEco.EF` | 90,034 | report/figs/ecobici_seleccion.csv (media arm=ecobici) |
| `time.lam5sel` | 333 | resultados.csv [split=seleccion, lam=5] máx decision_s_max |
| `time.mainmax` | 3.9 | resultados.csv [tag=eval, políticas] máx decision_s_max |
| `m.topehoraundo` | 474 | ecosim/results/medicion/ecobici_stats.json.tope_hora_con_undo |
| `m.eventos` | 494,190 | ecosim/results/medicion/ecobici_stats.json.damage_events_total |
| `sel.retirogana` | 84 | frozen.json.retiro_eleccion.gana_en |
| `sel.retirode` | 105 | frozen.json.retiro_eleccion.de |
| `runs.decisions` | 99,495 | resultados.csv suma de n_decisions |
| `runs.dec_s` | 0.34 | resultados.csv media de decision_s_mean |
| `v1.Fman` | 2.8% | v1/v1_resumen.json[ecobici|auto].mean_abs_rel_F_manana |
| `v1.stockmae` | 0.51 | v1/v1_resumen.json[ecobici|auto].stock_mae |
| `v1.Etar` | 12.0% | v1/v1_resumen.json[ecobici|auto].mean_abs_rel_E_tarde |
| `v1.Enoc` | 23.4% | v1/v1_resumen.json[ecobici|auto].mean_abs_rel_E_noche |
| `v1.t1E` | 5.3% | v1/v1_resumen.json[ecobici_t1|auto].mean_abs_rel_E |
| `rep.t1pct` | 9% | v1_por_dia.csv t1 / t0 − 1 |
| `rep.t1` | 111,457 | v1/v1_por_dia.csv [label=ecobici_t1|auto] media EF |
| `rep.undopct` | 4% | v1_por_dia.csv 1 − undo / principal |
| `rep.undo` | 97,946 | v1/v1_por_dia.csv [label=ecobici_undo|auto] media EF |
| `v1.fijasEman` | 18.5% | v1/v1_resumen.json[ecobici_stock|fixed].mean_abs_rel_E_manana |
| `sel.hmax` | 6 | frozen.json.h_max |
| `sel.daily.H` | 5 | frozen.json.best.daily.H |
| `sel.daily.EF` | 39,460 | frozen.json.best.daily.EF_seleccion |
| `sel.ma.f` | 15 | frozen.json.best.ma.f |
| `sel.ma.H` | 3 | frozen.json.best.ma.H |
| `sel.ma.EF` | 45,411 | frozen.json.best.ma.EF_seleccion |
| `sel.model.f` | 15 | frozen.json.best.model.f |
| `sel.model.H` | 3 | frozen.json.best.model.H |
| `sel.model.EF` | 45,258 | frozen.json.best.model.EF_seleccion |
| `arm.daily.rmin` | 47% | resultados.csv [split=evaluacion, tag=eval, arm=daily] media por día vs Ecobici, peor día |
| `arm.daily.rmax` | 62% | resultados.csv [split=evaluacion, tag=eval, arm=daily] media por día vs Ecobici, mejor día |
| `arm.daily.bikes` | 12,375 | resultados.csv [split=evaluacion, tag=eval, arm=daily] media por día |
| `arm.eco.bikes` | 11,067 | resultados.csv [split=evaluacion, tag=eval, arm=Ecobici] media por día |
| `der.dailymovs` | 4% | daily vs Ecobici movimientos |
| `der.dailybikes` | 12% | daily vs Ecobici bicis movidas |
| `arm.ma.recbod` | 639 | resultados.csv [split=evaluacion, tag=eval, arm=ma] media por día recorte_bodega |
| `arm.daily.recbod` | 203 | resultados.csv [split=evaluacion, tag=eval, arm=daily] media por día recorte_bodega |
| `L30.oracle` | 22,056 | resultados.csv [tag=eval_L, arm=oracle, L=30] |
| `L30.model` | 39,253 | resultados.csv [tag=eval_L, arm=model, L=30] |
| `gap.daily.oracle` | 19,707 | resultados.csv daily − oracle |
| `df.h0` | 3,157 | ef_por_bloque.csv [block=0].oracle |
| `df.h0pct` | 12% | ef_por_bloque.csv / resultados.csv |
| `sens.sens_bodega_taller.daily` | 55,286 | resultados.csv [tag=sens_bodega_taller, arm=daily] |
| `sens.sens_mm14.daily` | 48,018 | resultados.csv [tag=sens_mm14, arm=daily] |
| `der.tope474` | 1.4% | máx |sens_tope_con_undo / eval − 1| (oracle, daily) |
| `sens.sens_lam_min_seleccion.oracle` | 19,816 | resultados.csv [tag=sens_lam_min_seleccion, arm=oracle] |
| `sens.sens_lam_min_seleccion.daily` | 45,465 | resultados.csv [tag=sens_lam_min_seleccion, arm=daily] |
| `sens.sens_lam_min_seleccion.daily.moves` | 3,378 | resultados.csv [tag=sens_lam_min_seleccion, arm=daily] moves |
| `df.top20pct` | 1.6% | donde_falla.json |
| `df.hub.EF` | 655 | donde_falla.csv [273-274, 07:30].EF |
| `df.hub.EF_oracle` | 572 | donde_falla.csv [273-274, 07:30].EF_oracle |
| `df.hub.EF_ecobici` | 112 | donde_falla.csv [273-274, 07:30].EF_ecobici |
| `df.hub.sal_pron` | 878 | donde_falla.csv [273-274, 07:30].sal_pron |
| `df.hub.sal_real` | 1,065 | donde_falla.csv [273-274, 07:30].sal_real |
| `df.hub.dias_censurado` | 7 | donde_falla.csv [273-274, 07:30].dias_censurado |
| `arm.oracle.manana` | 12,015 | resultados.csv [split=evaluacion, tag=eval, arm=oracle] media por día |
| `run1.oracle` | 13,798 | docs/executor/runs/2026-09-28-ecosim-run1/NOTAS-post-run.md y results/run1/tablas.md |
| `arm.daily.manana` | 16,376 | resultados.csv [split=evaluacion, tag=eval, arm=daily] media por día |
| `run1.daily` | 19,647 | docs/executor/runs/2026-09-28-ecosim-run1/NOTAS-post-run.md y results/run1/tablas.md |
| `run1.ecobici` | 34,325 | docs/executor/runs/2026-09-28-ecosim-run1/NOTAS-post-run.md y results/run1/tablas.md |
| `arm.eco.manana` | 38,611 | resultados.csv [split=evaluacion, tag=eval, arm=Ecobici] media por día |
| `der.run1min` | 16 | 1 − diag_como_run1.manana / eval.manana (oracle, daily), mínimo |
| `der.run1max` | 18 | 1 − diag_como_run1.manana / eval.manana (oracle, daily), máximo |
| `sens.diag_como_run1.oracle.manana` | 9,889 | resultados.csv [tag=diag_como_run1, arm=oracle] EF_manana |
| `sens.diag_como_run1.daily.manana` | 13,719 | resultados.csv [tag=diag_como_run1, arm=daily] EF_manana |
| `der.L30min` | 17 | 1 − L30/L60 por brazo (resultados.csv), mínimo |
| `der.L30max` | 26 | 1 − L30/L60 por brazo (resultados.csv), máximo |
| `der.bodtaller` | 19% | sens_bodega_taller.daily / eval.daily − 1 |
| `val.nge` | 69 | validation_summary.json.spearman_hull.n_ge_0.8 |
| `val.n` | 72 | validation_summary.json.n_decisiones |

## 2. Tablas generadas

| tabla | archivo | fuente |
|---|---|---|
| Tabla brazos | `report/tablas/brazos.tex` | resultados.csv [split=evaluacion, tag=eval], media por día; `resultados_run2.json` |
| Tabla sensibilidades | `report/tablas/sensibilidades.tex` | resultados.csv [tag=sens_* y diag_*] |
| Tabla h (selección) | `report/tablas/hsel.tex` | frozen.json.h_busqueda.EF |
| Tabla run 1 | `report/tablas/run1.tex` | run 1: NOTAS-post-run.md / results/run1/tablas.md; run 2: resultados.csv EF_manana |
| Tabla de checks | en el .qmd con macros `d.*`, `f.*` | `report/figs/checks_datos.json` |

## 3. Figuras

| figura | script | datos |
|---|---|---|
| viajes_por_hora.png, viajes_por_dia.png, feed_cadencia.png | checks_datos.py | trips_2025_01_11.parquet; snapshots/ |
| rebalanceo_por_hora.png, bicis_por_movimiento.png | rebalanceo_ecobici.py | ecobici_moves.parquet |
| lambda_curva.png, brazos_franja.png | resultados_run2.py | frozen.json, resultados.csv, ecobici_seleccion.csv |
| app/01..04-*.png | worker ecosim2-app (commit edc92a7) | capturas finales; 03--04 usan `daily` por defecto |

## 4. Números escritos en el texto (no macros)

| número(s) | fuente |
|---|---|
| 20 millones de personas; transporte concesionado sin registro | raw/notes/Caso 1 - Documentos de Google.pdf |
| hasta 42 bicis por camioneta con remolque; monitoreo 24 h; balanceadores | Expansión Política, 2026-08-31 [expansion2026] (verificado con web_fetch_exa) |
| 123 subzonas de 3 a 5 estaciones; órdenes por app; zonas valet | Mexico Business News, 2026-08-12 [mbn2026] (verificado) |
| 17% fuera de servicio; 9,308→15,000 bicis; 687→1,111 estaciones; 'flujos de calor' | Milenio, 2026-08-28 [milenio2026] (verificado) |
| ttl = 10 s; last_updated avanza cada ~10–11 s | curl a https://gbfs.mex.lyftbikes.com/gbfs/gbfs.json y en/station_status.json, 4 lecturas el 2026-09-29 |
| 'aproximadamente cada 12 minutos'; repo creado 2023-07-02; Ecobici desde abril de 2024 | README y API de GitHub de MaxHalford/bike-sharing-history (2026-09-29) |
| desfase +30 a +36 s; hora = commit − 30 s | ecosim/FUNDAMENTOS.md (barrido de desfase) y config.GBFS_COMMIT_LAG_S = 30 |
| 2.78 millones de saltos; hueco mediano 2.3 h | wiki/Rebalanceo-Ecobici-suposicion-horario.md (análisis exploratorio previo) |
| regla 1.3 | docs/planner/plans/2026-09-28-ecosim.md §1.3 |
| 14 = p95 y 22 = p99 (mañana run 1) | report/figs/rebalanceo_ecobici.json.run1_manana['ecobici sin ±1'] (= docs/executor/runs/2026-09-28-ecosim-run1/bicis_por_movimiento_percentiles.csv) |
| 632 y 74 (bodega sin/con taller) | ecosim/results/medicion/ecobici_stats.json: tope_bodega, mean_abs_stock_warehouse; frozen.json.tope_bodega_taller |
| ≤500 m de desvío; L = 60/45/30; bloques de 60 min; paso de 15 min | ecosim/config.py (DETOUR_RADIUS_M, LEAD_GRID_MIN, BLOCK_MIN, STEP_MIN) |
| 15 + 15 días; 11 entre semana + 4 fin de semana; cobertura ≥ 90% | ecosim/days.json; ecosim/config.py |
| f ∈ {15, 60, 180}; K = 5; clip [0.5, 2] | ecosim/pronostico.py (REFRESH_MIN, K_SHRINK, FACTOR_CLIP) |
| LightGBM: 200 árboles, tasa 0.05, 31 hojas; rezagos 7/14, media 28; entrenamiento 2025-01-01..2025-08-31 | ecosim/pronostico.py (LGB_PARAMS, FEATURES) y config.TRAIN_START/END |
| 22 bicis por orden (cota r_i, u_i); brecha 1e-4; μ = 1 | ecosim/asignador.py; frozen.json.max_move, mu |
| 42% del faltante de E de 17:30 a 21:30; huecos de 20–45 min desde las 18:00; hasta 44 min antes | RESULTS/CONCLUSIONES.md (V1, noche) y RESULTS/v1/v1_diag_bloques.csv |
| bodega explica ~1/3 de la brecha ma–daily (4 días) | RESULTS/CONCLUSIONES.md, Panorama punto 2 (medición del reviewer de integracion2) |
| MAE salidas 2.053 (daily) vs 2.10 (ma); WAPE ≈ 0.45 | ecosim/results/pronostico/REPORT.md (accuracy_by_lead.csv, evaluación) |
| hubs 273-274, 271-272, 268-269, 176, 261, 014, 022 | RESULTS/donde_falla.csv |
| 06:30 primera orden efectiva | 05:30 + L (60 min) |
| 90 de 90 corridas idénticas en el replay de la app; 22 corridas en vivo evaluadas, MAE ≈ 2.2 salidas / 2.0 llegadas por estación-hora a 0 h; 232 movimientos efectivos 18:30 | worktree ecosim2-app (commit edc92a7), scripts/ecobici_mapa/screenshots/APP.md y captura 04-prediccion-que-tan-bueno.png |
| feed cada 60 s; asignación cada 15 min; 4 semanas hasta 2026-08-30 | worktree ecosim2-app, scripts/ecobici_mapa/screenshots/APP.md |
| 72 decisiones; 3 días × 8 horas × h ∈ {1,3,6}; 23 planes | ecosim/results/asignador/validation_summary.json |
| run 1: H = 3; daily con bloque 30 | RESULTS/tablas.md (sección run 1) y RESULTS/run1/frozen.json |

Macros usadas: 152; sin fuente: 0 
