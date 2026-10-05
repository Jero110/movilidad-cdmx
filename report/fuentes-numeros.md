# Procedencia de cada cifra del reporte final

Regenerar: `uv run python3 report/figs/numeros_run3.py`.
Comprobar sin escribir: `uv run python3 report/figs/numeros_run3.py --check` (recalcula todo desde las fuentes y exige que macros, tablas, figuras y este archivo no cambien).

## Macros (`report/numeros-run3.tex`)

| macro | valor | origen |
|---|---:|---|
| `\rdias` | 152 | ecosim/results/resultados.csv (tag=prueba), media por día; número de días |
| `\recoEF` | 100,641 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, EF |
| `\recoE` | 83,178 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, E |
| `\recoF` | 17,463 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, F |
| `\rbaseEF` | 217,943 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=sin_rebalanceo, EF |
| `\rbaseE` | 167,463 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=sin_rebalanceo, E |
| `\rbaseF` | 50,480 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=sin_rebalanceo, F |
| `\rmaEF` | 56,542 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, EF |
| `\rmaE` | 50,853 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, E |
| `\rmaF` | 5,688 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, F |
| `\recoVis` | 2,236 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, visitas |
| `\recoBicis` | 10,217 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, bicis_movidas |
| `\rmaVis` | 2,000 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, visitas |
| `\rmaBicis` | 8,670 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, bicis_movidas |
| `\rmaPct` | 43.8 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − EF(ma_diaria)/EF(ecobici) |
| `\rmaPctLo` | 41.7 | ecosim/results/resultados.csv (tag=prueba), media por día; IC95 t pareado por día, límite inferior |
| `\rmaPctHi` | 46.0 | ecosim/results/resultados.csv (tag=prueba), media por día; IC95 t pareado por día, límite superior |
| `\rlgbmDPct` | 43.3 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − EF(lgbm_diario)/EF(ecobici) |
| `\rlgbmDEF` | 57,026 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=lgbm_diario, EF |
| `\rlgbmDirPct` | 43.8 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − EF(lgbm_directo)/EF(ecobici) |
| `\rlgbmDirEF` | 56,588 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=lgbm_directo, EF |
| `\rorDPct` | 63.1 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − EF(oraculo_diario)/EF(ecobici) |
| `\rorDEF` | 37,162 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=oraculo_diario, EF |
| `\rorDirPct` | 71.3 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − EF(oraculo_directo)/EF(ecobici) |
| `\rorDirEF` | 28,897 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=oraculo_directo, EF |
| `\rmaGana` | 152 | ecosim/results/resultados.csv (tag=prueba), media por día; días con EF(ma_diaria) < EF(ecobici) |
| `\rmaVisPct` | 10.6 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − visitas(ma_diaria)/visitas(ecobici) |
| `\rmaGap` | 19,379 | ecosim/results/resultados.csv (tag=prueba), media por día; EF(ma_diaria) − EF(oraculo_diario) |
| `\rlgbmDirGap` | 27,692 | ecosim/results/resultados.csv (tag=prueba), media por día; EF(lgbm_directo) − EF(oraculo_directo) |
| `\roracleDirectGap` | 8,266 | ecosim/results/resultados.csv (tag=prueba), media por día; EF(oraculo_diario) − EF(oraculo_directo) |
| `\rmaDesvSal` | 3,374 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, desvios_salida |
| `\rmaDesvLleg` | 661 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, desvios_llegada |
| `\rmaKm` | 0.25 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, km_desvio_medio |
| `\recoDesv` | 2,306 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, desvios_salida + desvios_llegada |
| `\recoKm` | 0.26 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, km_desvio_medio |
| `\recoNeto` | 66.2 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, replay_neto |
| `\rdicPct` | 39.1 | ecosim/results/resultados.csv (tag=prueba), media por día; días de 2025-12 |
| `\rdicDias` | 31 | ecosim/results/resultados.csv (tag=prueba), media por día; días de 2025-12 |
| `\rdicMes` | diciembre de 2025 | ecosim/results/resultados.csv (tag=prueba), media por día; mes 2025-12 |
| `\renePct` | 39.7 | ecosim/results/resultados.csv (tag=prueba), media por día; días de 2026-01 |
| `\reneDias` | 31 | ecosim/results/resultados.csv (tag=prueba), media por día; días de 2026-01 |
| `\reneMes` | enero de 2026 | ecosim/results/resultados.csv (tag=prueba), media por día; mes 2026-01 |
| `\rdecTot` | 56,240 | ecosim/results/resultados.csv (tag=prueba), media por día; suma de decisiones de los cinco brazos con asignador |
| `\rdecLim` | 0 | ecosim/results/resultados.csv (tag=prueba), media por día; suma de decisiones_limite_10s |
| `\rdecFallback` | 0 | ecosim/results/resultados.csv (tag=prueba), media por día; suma de fallbacks |
| `\rdecPNoventaCinco` | 1.1 | ecosim/results/resultados.csv (tag=prueba), media por día; máximo por día de decision_p95_s |
| `\rnDiario` | 4 | ecosim/results/frozen.json n.diaria |
| `\rnDirecto` | 4 | ecosim/results/frozen.json n.directa |
| `\rselDias` | 15 | ecosim/results/frozen.json seleccion (número de días) |
| `\rlamMa` | 72.9 | ecosim/results/frozen.json lambda_por_brazo.ma_diaria.lambda |
| `\rlamLgbmD` | 72.7 | ecosim/results/frozen.json lambda_por_brazo.lgbm_diario.lambda |
| `\rlamLgbmDir` | 88.9 | ecosim/results/frozen.json lambda_por_brazo.lgbm_directo.lambda |
| `\rlamOrD` | 57.6 | ecosim/results/frozen.json lambda_por_brazo.oraculo_diario.lambda |
| `\rlamOrDir` | 39.1 | ecosim/results/frozen.json lambda_por_brazo.oraculo_directo.lambda |
| `\rlamExtrap` | 3 | ecosim/results/frozen.json lambda_por_brazo.*.tipo_ajuste = extrapolación (ver CONCLUSIONES.md) |
| `\rselEcoVis` | 2,065.6 | ecosim/results/frozen.json lambda_por_brazo.ma_diaria.visitas_ecobici |
| `\rselMaVis` | 2,107.3 | ecosim/results/frozen.json lambda_por_brazo.ma_diaria.visitas_confirmadas |
| `\rtopeVis` | 67 | ecosim/results/frozen.json topes_base.visitas_por_decision |
| `\rtopeBicis` | 14 | ecosim/results/frozen.json topes_base.bicis_por_visita |
| `\rtopeVisNN` | 83 | ecosim/results/frozen.json topes_p99.visitas_por_decision |
| `\rtopeBicisNN` | 24 | ecosim/results/frozen.json topes_p99.bicis_por_visita |
| `\rtopeVisAnt` | 47 | ecosim/results/frozen.json topes_hacia_adelante.visitas_por_decision |
| `\rtopeBicisAnt` | 19 | ecosim/results/frozen.json topes_hacia_adelante.bicis_por_visita |
| `\rpickMin` | 15 | ecosim/config.py PICKUP_MIN |
| `\rdelMin` | 60 | ecosim/config.py DELIVERY_MIN |
| `\rstepMin` | 15 | ecosim/config.py STEP_MIN |
| `\rlagS` | 30 | ecosim/config.py GBFS_COMMIT_LAG_S |
| `\rcovMin` | 90 | ecosim/config.py COVERAGE_MIN |
| `\rselLab` | 11 | ecosim/config.py N_SEL_WEEKDAY |
| `\rselFin` | 4 | ecosim/config.py N_SEL_WEEKEND |
| `\rtransMin` | 45 | ecosim/config.py DELIVERY_MIN − PICKUP_MIN |
| `\rentregaCorta` | 45 | ecosim/config.py DELIVERY_SENS_MIN (mínimo) |
| `\rentregaLarga` | 75 | ecosim/config.py DELIVERY_SENS_MIN (máximo) |
| `\rventanaHoras` | 19.5 | ecosim/config.py WINDOW_MIN / 60 |
| `\rabiertaPct` | 95 | ecosim/config.py INITIAL_OPEN_RENTING_MIN |
| `\rrefDias` | 15 | ecosim/config.py RUN1_EVAL_DAYS (días de referencia de topes y validación) |
| `\rnMax` | 6 | ecosim/config.py N_GRID |
| `\rlamGridN` | 15, 30, 60 | ecosim/config.py LAMBDA_GRID_N |
| `\rtrainMeses` | 8 | ecosim/config.py FOLDS prueba_1 (meses de train_start a train_end) |
| `\rlimiteS` | 10 | ecosim/run.py, Asignador(time_limit=...) |
| `\rcurvaDias` | 32 | ecosim/days.json curva (días de las sensibilidades) |
| `\rsemanasHist` | 4 | ecosim/pronostico.py, range(max(0, i − 28), i) / 7 días |
| `\rcadaTicks` | 12 | ecosim/pronostico.py `_ticks` (fase módulo 12) |
| `\rbrechaExp` | -4 | ecosim/asignador.py Asignador(mip_rel_gap=1e-4), exponente |
| `\rlgbArboles` | 200 | ecosim/pronostico.py LGB_PARAMS n_estimators |
| `\rlgbTasa` | 0.05 | ecosim/pronostico.py LGB_PARAMS learning_rate |
| `\rlgbHojas` | 31 | ecosim/pronostico.py LGB_PARAMS num_leaves |
| `\rlgbMinHoja` | 50 | ecosim/pronostico.py LGB_PARAMS min_child_samples |
| `\rlgbFilas` | 0.8 | ecosim/pronostico.py LGB_PARAMS subsample |
| `\rlgbCols` | 0.9 | ecosim/pronostico.py LGB_PARAMS colsample_bytree |
| `\rviajes` | 18,689,701 | report/figs/checks_datos.json viajes (ene–nov 2025) |
| `\rdatosPeriodo` | enero y noviembre de 2025 | report/figs/checks_datos.json viajes_por_mes (meses) y viajes_por_dia_min (año) |
| `\rviajesDia` | 55,957 | report/figs/checks_datos.json viajes_por_dia_media |
| `\restaciones` | 677 | report/figs/checks_datos.json estaciones_distintas |
| `\rbicisDistintas` | 7,966 | report/figs/checks_datos.json bicis_distintas |
| `\rdurMed` | 12 | report/figs/checks_datos.json duracion_mediana_min |
| `\rdurP` | 37 | report/figs/checks_datos.json duracion_p95_min |
| `\rhuecoMed` | 15.9 | report/figs/checks_datos.json hueco_mediana_min |
| `\rhuecoTarde` | 35.6 | report/figs/checks_datos.json hueco_mediana_18_21 |
| `\rhuecoResto` | 15.3 | report/figs/checks_datos.json hueco_mediana_resto |
| `\rcalleMed` | 5.3 | ecosim/results/flota/rutas_osm_resumen.csv p50, calle km |
| `\rpicoMed` | 24 | ecosim/results/flota/rutas_osm_resumen.csv p50, min a pico 13.5 km/h |
| `\rpicoNoventa` | 44 | ecosim/results/flota/rutas_osm_resumen.csv p90, min a pico 13.5 km/h |
| `\rpicoNN` | 61 | ecosim/results/flota/rutas_osm_resumen.csv p99, min a pico 13.5 km/h |
| `\rvEobs` | 82,473 | ecosim/results/tablas.md, sección V1, E_obs |
| `\rvFobs` | 15,929 | ecosim/results/tablas.md, sección V1, F_obs |
| `\rvErep` | 80,270 | ecosim/results/tablas.md, sección V1, E_replay |
| `\rvFrep` | 15,548 | ecosim/results/tablas.md, sección V1, F_replay |
| `\rvRel` | 3 | ecosim/results/tablas.md, sección V1, 1 − E_replay/E_obs |
| `\rvRelF` | 2 | ecosim/results/tablas.md, sección V1, 1 − F_replay/F_obs |
| `\rsCuarenta` | $-$7,470 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_45 ma_diaria delta_EF_base |
| `\rsCuarentaLo` | $-$8,514 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_45 ma_diaria IC95_inf |
| `\rsCuarentaHi` | $-$6,425 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_45 ma_diaria IC95_sup |
| `\rsCuarentaOr` | $-$3,438 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_45 oraculo_directo delta_EF_base |
| `\rsSetenta` | $+$7,494 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_75 ma_diaria delta_EF_base |
| `\rsSetentaLo` | $+$6,294 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_75 ma_diaria IC95_inf |
| `\rsSetentaHi` | $+$8,694 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_75 ma_diaria IC95_sup |
| `\rsSetentaOr` | $+$3,534 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_75 oraculo_directo delta_EF_base |
| `\rsNN` | $-$2,275 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_p99 ma_diaria delta_EF_base |
| `\rsNNLo` | $-$3,078 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_p99 ma_diaria IC95_inf |
| `\rsNNHi` | $-$1,472 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_p99 ma_diaria IC95_sup |
| `\rsNNOr` | $-$1,717 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_p99 oraculo_directo delta_EF_base |
| `\rsAnt` | $+$735 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_hacia_adelante ma_diaria delta_EF_base |
| `\rsAntLo` | $+$188 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_hacia_adelante ma_diaria IC95_inf |
| `\rsAntHi` | $+$1,281 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_hacia_adelante ma_diaria IC95_sup |
| `\rsAntOr` | $+$1,122 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_hacia_adelante oraculo_directo delta_EF_base |
| `\rsDan` | $+$835 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_danadas_feed ma_diaria delta_EF_base |
| `\rsDanLo` | $+$166 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_danadas_feed ma_diaria IC95_inf |
| `\rsDanHi` | $+$1,504 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_danadas_feed ma_diaria IC95_sup |
| `\rsDanOr` | $+$20 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_danadas_feed oraculo_directo delta_EF_base |
| `\rsSinRegla` | 4,927 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_pares_sin_regla ecobici visitas |
| `\rsSoloUno` | 2,623 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_pares_solo_1 ecobici visitas |
| `\rsEcoEF` | 102,802 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_pares_sin_regla ecobici EF |
| `\rmaeMes` | septiembre de 2025 | ecosim/results/tablas.md, Exactitud del pronóstico: primer mes de prueba |
| `\rmaeMa` | 1.62 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, ma_diaria mae |
| `\rmaeMaW` | 0.81 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, ma_diaria wape |
| `\rmaeLgbmD` | 1.61 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, lgbm_diario mae |
| `\rmaeLgbmDW` | 0.81 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, lgbm_diario wape |
| `\rmaeLgbmDir` | 1.99 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, lgbm_directo mae |
| `\rmaeLgbmDirW` | 0.88 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, lgbm_directo wape |
| `\rspearman` | 0.964 | ecosim/results/asignador/REPORT.md, mediana de Spearman |
| `\rantPeriodo` | enero a agosto de 2025 | ecosim/results/medicion/REPORT.md, encabezado de Topes (población de los topes hacia adelante) |
| `\rvisSinRegla` | 5,113.5 | ecosim/results/medicion/REPORT.md, Impacto, ventana 05:00–00:30, sin regla, visitas |
| `\rvisConRegla` | 2,491.5 | ecosim/results/medicion/REPORT.md, Impacto, ventana 05:00–00:30, cualquier tamaño, visitas |
| `\rvisQuedan` | 48.7 | ecosim/results/medicion/REPORT.md, Impacto, ventana 05:00–00:30, cualquier tamaño, % visitas |
| `\rpruebaPeriodo` | septiembre de 2025 a enero de 2026 | ecosim/results/resultados.csv (tag=prueba), media por día; primer y último día de prueba |
| `\rselMes` | agosto de 2025 | ecosim/results/frozen.json seleccion (mes de los días) |
| `\rrefPeriodo` | septiembre a noviembre de 2025 | ecosim/config.py RUN1_EVAL_DAYS (primer y último día) |
| `\retiquetasDia` | 4,800 | data/derived/ecosim/damage_events.parquet × ecobici_moves.parquet, 152 días de prueba; cálculo en report/figs/numeros_run3.py (bicis que cambian de etiqueta por día) |
| `\renSitioPct` | 77 | data/derived/ecosim/damage_events.parquet × ecobici_moves.parquet, 152 días de prueba; cálculo en report/figs/numeros_run3.py (% de esos cambios con total de la estación igual) |
| `\rfigPeriodo` | septiembre a noviembre de 2025 | FIG_MONTHS en report/figs/numeros_run3.py |
| `\rfigVisitas` | 210,974 | data/derived/ecosim/ecobici_moves.parquet, sep–nov 2025, sin pares; cálculo en report/figs/numeros_run3.py (visitas en la figura de tamaños) |
| `\rfigBajoTope` | 95.5 | data/derived/ecosim/ecobici_moves.parquet, sep–nov 2025, sin pares; cálculo en report/figs/numeros_run3.py (% de visitas con bicis ≤ tope) |
| `\rfigIntervalos` | 6,051 | data/derived/ecosim/ecobici_moves.parquet, sep–nov 2025, sin pares; cálculo en report/figs/numeros_run3.py (intervalos en la figura de visitas) |
| `\rfigVisBajoTope` | 95.7 | data/derived/ecosim/ecobici_moves.parquet, sep–nov 2025, sin pares; cálculo en report/figs/numeros_run3.py (% de intervalos con visitas ≤ tope) |

## Tablas y figuras

| artefacto | origen |
|---|---|
| `tablas/principal-run3.tex` | resultados.csv (tag=prueba), IC95 t pareado por día |
| `tablas/brazos-run3.tex` | frozen.json lambda_por_brazo |
| `tablas/pares-run3.tex` | medicion/REPORT.md, Impacto, ventana 05:00–00:30 |
| `figs/tamanos-run3.pdf` | ecobici_moves.parquet, sep–nov 2025, sin pares; tope de frozen.json |
| `figs/visitas-tope-run3.pdf` | ecobici_moves.parquet, sep–nov 2025, visitas por intervalo × 15 / minutos del intervalo |
| `figs/ef-brazos-run3.pdf` | resultados.csv (tag=prueba), media de EF por brazo |

## Cifras externas o ilustrativas escritas en prosa

- 1,990 de 4,139 (48.1%) eligen "no siempre hay bicis disponibles" como principal desventaja; 114 eligen "no siempre hay espacios para anclar": Encuesta ECOBICI 2025, pregunta 18, `encuesta2025` en referencias.bib (https://ecobici.cdmx.gob.mx/wp-content/uploads/2026/02/Encuesta-ECOBICI-2025-1.pdf); leído de la gráfica de dona, la suma de las seis respuestas da 4,139.
- 1,425 de 4,139 (34.4%) encuentran bici cerca de su origen 6 o menos de cada 10 veces (1,037 "difícil" + 388 "muy difícil"); 2,652 (64.1%) encuentran anclaje 8 o más de cada 10 veces (1,750 + 902): Encuesta ECOBICI 2025, preguntas 13 y 14, misma URL.
- 05:00 a 00:30 y devolución las 24 h: Términos y condiciones de Ecobici, `ecobici_horario`.
- 13.5 km/h en hora pico; 34 min 29 s por 10 km: TomTom Traffic Index 2025, `tomtom2025`.
- hasta 42 bicis por camioneta con remolque; unas 9,300 bicicletas en 687 estaciones: Expansión Política 2026-08-31, `expansion2026` (título y cuerpo de la nota).
- un traslado de 15 minutos se duplica o triplica en hora pico: Expansión Política 2026-08-31, `expansion2026`.
- a las 8:30 doce personas esperan bici en Buenavista y diez minutos después dieciocho: Expansión Política 2026-02-13, `expansion2026cronica`.
- estaciones sin bicis entre las 18:00 y las 19:00: El Universal 2026-08-19, `eluniversal2026`.
- campo `ttl` = 10 s del feed en vivo: https://gbfs.mex.lyftbikes.com/gbfs/gbfs.json, consultado el 2026-10-05.
- total de bicis ancladas a las 04:45 sin crecimiento sostenido: ecosim/results/flota/stock_0445.csv (sin cifra en el texto).
- 11:48:28, 12:08:14, 12:07:09, bici 4201141, estación 002 (ejemplo de par): ecosim/results/medicion/REPORT.md, Ejemplos con viajes exactos.
- Ejemplos de 5/2, 4/3, 8/2 bicis en la sección de no rentables: ejemplo ilustrativo, no es dato medido.
