# Procedencia de cada cifra del reporte (reporte-caso.tex)

Regenerar: `uv run python3 report/figs/fidelidad_ecobici.py` (lee las simulaciones guardadas y las fotos; escribe `figs/fidelidad_ecobici.json`) y después `uv run python3 report/figs/numeros_run3.py`. Los dos aceptan `--check`.
Comprobar sin escribir: `uv run python3 report/figs/numeros_run3.py --check` (recalcula todo desde las fuentes y exige que macros, tablas, figuras y este archivo no cambien).

## Macros (`report/numeros-run3.tex`)

| macro | valor | origen |
|---|---:|---|
| `\rdias` | 121 | ecosim/results/resultados.csv (tag=prueba), media por día; número de días |
| `\recoEF` | 102,135 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, EF |
| `\recoE` | 84,956 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, E |
| `\recoF` | 17,178 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, F |
| `\rbaseEF` | 220,061 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=sin_rebalanceo, EF |
| `\rbaseE` | 169,276 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=sin_rebalanceo, E |
| `\rbaseF` | 50,785 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=sin_rebalanceo, F |
| `\rmaEF` | 57,802 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, EF |
| `\rmaE` | 53,774 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, E |
| `\rmaF` | 4,028 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, F |
| `\recoVis` | 2,263 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, visitas |
| `\recoBicis` | 10,276 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, bicis_movidas |
| `\rmaVis` | 2,060 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, visitas |
| `\rmaBicis` | 10,084 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, bicis_movidas |
| `\rmaPct` | 43.4 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − EF(ma_diaria)/EF(ecobici) |
| `\rmaPctLo` | 41.2 | ecosim/results/resultados.csv (tag=prueba), media por día; IC95 t pareado por día, límite inferior |
| `\rmaPctHi` | 45.6 | ecosim/results/resultados.csv (tag=prueba), media por día; IC95 t pareado por día, límite superior |
| `\rlgbmDPct` | 43.0 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − EF(lgbm_diario)/EF(ecobici) |
| `\rlgbmDEF` | 58,187 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=lgbm_diario, EF |
| `\rlgbmDirPct` | 43.7 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − EF(lgbm_directo)/EF(ecobici) |
| `\rlgbmDirEF` | 57,510 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=lgbm_directo, EF |
| `\rorDPct` | 60.0 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − EF(oraculo_diario)/EF(ecobici) |
| `\rorDEF` | 40,872 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=oraculo_diario, EF |
| `\rorDirPct` | 67.0 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − EF(oraculo_directo)/EF(ecobici) |
| `\rorDirEF` | 33,684 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=oraculo_directo, EF |
| `\rmaGana` | 121 | ecosim/results/resultados.csv (tag=prueba), media por día; días con EF(ma_diaria) < EF(ecobici) |
| `\rmaVisPct` | 9.0 | ecosim/results/resultados.csv (tag=prueba), media por día; 1 − visitas(ma_diaria)/visitas(ecobici) |
| `\rmaGap` | 16,930 | ecosim/results/resultados.csv (tag=prueba), media por día; EF(ma_diaria) − EF(oraculo_diario) |
| `\rlgbmDirGap` | 23,826 | ecosim/results/resultados.csv (tag=prueba), media por día; EF(lgbm_directo) − EF(oraculo_directo) |
| `\roracleDirectGap` | 7,187 | ecosim/results/resultados.csv (tag=prueba), media por día; EF(oraculo_diario) − EF(oraculo_directo) |
| `\rmaDesvSal` | 3,395 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, desvios_salida |
| `\rmaDesvLleg` | 311 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, desvios_llegada |
| `\rmaKm` | 0.25 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ma_diaria, km_desvio_medio |
| `\recoDesv` | 2,298 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, desvios_salida + desvios_llegada |
| `\recoKm` | 0.26 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, km_desvio_medio |
| `\recoNeto` | 51.3 | ecosim/results/resultados.csv (tag=prueba), media por día; arm=ecobici, replay_neto |
| `\rdicPct` | 39.2 | ecosim/results/resultados.csv (tag=prueba), media por día; días de 2025-12 |
| `\rdicDias` | 31 | ecosim/results/resultados.csv (tag=prueba), media por día; días de 2025-12 |
| `\rdicMes` | diciembre de 2025 | ecosim/results/resultados.csv (tag=prueba), media por día; mes 2025-12 |
| `\rdecTot` | 44,770 | ecosim/results/resultados.csv (tag=prueba), media por día; suma de decisiones de los cinco brazos con asignador |
| `\rdecPNoventaCinco` | 0.1 | ecosim/results/resultados.csv (tag=prueba), media por día; máximo por día de decision_p95_s |
| `\rnDiario` | 4 | ecosim/results/frozen.json n.diaria |
| `\rnDirecto` | 4 | ecosim/results/frozen.json n.directa |
| `\rselDias` | 31 | ecosim/results/frozen.json seleccion (número de días) |
| `\rlamMa` | 58.2 | ecosim/results/frozen.json lambda_por_brazo.ma_diaria.lambda |
| `\rlamLgbmD` | 59.2 | ecosim/results/frozen.json lambda_por_brazo.lgbm_diario.lambda |
| `\rlamLgbmDir` | 68.9 | ecosim/results/frozen.json lambda_por_brazo.lgbm_directo.lambda |
| `\rlamOrD` | 44.6 | ecosim/results/frozen.json lambda_por_brazo.oraculo_diario.lambda |
| `\rlamOrDir` | 36.3 | ecosim/results/frozen.json lambda_por_brazo.oraculo_directo.lambda |
| `\rlamDifMax` | 0.89 | ecosim/results/frozen.json lambda_por_brazo.*.diferencia_pct (máximo en valor absoluto) |
| `\rlamRondasMax` | 4 | ecosim/results/frozen.json lambda_por_brazo.*.rondas (máximo) |
| `\rlamExtension` | 0 | ecosim/results/frozen.json lambda_por_brazo.*.extension_rejilla (brazos con rejilla extendida) |
| `\rlamGridMin` | 10 | ecosim/results/frozen.json configuracion.lambda_rejilla (mínimo) |
| `\rlamGridMax` | 120 | ecosim/results/frozen.json configuracion.lambda_rejilla (máximo) |
| `\rlamGrid` | 10, 15, 20, 30, 45, 60, 75, 90, 120 | ecosim/results/frozen.json configuracion.lambda_rejilla |
| `\rmejorReal` | media móvil | ecosim/results/frozen.json mejor_real |
| `\rselEcoVis` | 2,033.9 | ecosim/results/frozen.json lambda_por_brazo.ma_diaria.visitas_ecobici |
| `\rselMaVis` | 2,051.5 | ecosim/results/frozen.json lambda_por_brazo.ma_diaria.visitas_confirmadas |
| `\rtopeVis` | 55 | ecosim/results/frozen.json topes_base.visitas_por_decision |
| `\rtopeBicis` | 17 | ecosim/results/frozen.json topes_base.bicis_por_visita |
| `\rpickMin` | 15 | ecosim/config.py PICKUP_MIN |
| `\rdelMin` | 60 | ecosim/config.py DELIVERY_MIN |
| `\rstepMin` | 15 | ecosim/config.py STEP_MIN |
| `\rlagS` | 30 | ecosim/config.py GBFS_COMMIT_LAG_S |
| `\rcovMin` | 90 | ecosim/config.py COVERAGE_MIN |
| `\rtransMin` | 45 | ecosim/config.py DELIVERY_MIN − PICKUP_MIN |
| `\rentregaCorta` | 45 | ecosim/config.py DELIVERY_SENS_MIN (mínimo) |
| `\rentregaLarga` | 75 | ecosim/config.py DELIVERY_SENS_MIN (máximo) |
| `\rventanaHoras` | 19.5 | ecosim/config.py WINDOW_MIN / 60 |
| `\rabiertaPct` | 95 | ecosim/config.py INITIAL_OPEN_RENTING_MIN |
| `\rrefDias` | 15 | ecosim/config.py RUN1_EVAL_DAYS (días de referencia de topes y validación) |
| `\rnMax` | 6 | ecosim/config.py N_GRID |
| `\rlamGridN` | 15, 30, 60 | ecosim/config.py LAMBDA_GRID_N |
| `\rtrainMeses` | 8 | ecosim/config.py FOLDS prueba_1 (meses de train_start a train_end) |
| `\rcurvaDias` | 121 | ecosim/results/corridas_run3.csv, días de tag=sens_pares_sin_regla (sensibilidades) |
| `\rsemanasHist` | 4 | ecosim/pronostico.py, range(max(0, i − 28), i) / 7 días |
| `\rcadaTicks` | 12 | ecosim/pronostico.py `_ticks` (fase módulo 12) |
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
| `\rvDias` | 121 | ecosim/results/v1_run3.csv, días (el encabezado de tablas.md dice 15, pero la tabla promedia estos días) |
| `\rvEobs` | 87,470 | ecosim/results/tablas.md, sección V1, E_obs |
| `\rvFobs` | 17,062 | ecosim/results/tablas.md, sección V1, F_obs |
| `\rvErep` | 84,956 | ecosim/results/tablas.md, sección V1, E_replay |
| `\rvFrep` | 17,178 | ecosim/results/tablas.md, sección V1, F_replay |
| `\rvRel` | 3 | ecosim/results/tablas.md, sección V1, 1 − E_replay/E_obs |
| `\rvRelF` | -1 | ecosim/results/tablas.md, sección V1, 1 − F_replay/F_obs |
| `\rsCuarenta` | $-$6,559 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_45 ma_diaria delta_EF_base |
| `\rsCuarentaLo` | $-$7,042 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_45 ma_diaria IC95_inf |
| `\rsCuarentaHi` | $-$6,076 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_45 ma_diaria IC95_sup |
| `\rsCuarentaOr` | $-$3,906 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_45 oraculo_directo delta_EF_base |
| `\rsSetenta` | $+$6,922 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_75 ma_diaria delta_EF_base |
| `\rsSetentaLo` | $+$6,348 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_75 ma_diaria IC95_inf |
| `\rsSetentaHi` | $+$7,496 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_75 ma_diaria IC95_sup |
| `\rsSetentaOr` | $+$3,476 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_entrega_75 oraculo_directo delta_EF_base |
| `\rsDan` | $+$1,253 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_danadas_feed ma_diaria delta_EF_base |
| `\rsDanLo` | $+$952 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_danadas_feed ma_diaria IC95_inf |
| `\rsDanHi` | $+$1,554 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_danadas_feed ma_diaria IC95_sup |
| `\rsDanOr` | $+$89 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_danadas_feed oraculo_directo delta_EF_base |
| `\rsSinRegla` | 4,615 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_pares_sin_regla ecobici visitas |
| `\rsSoloUno` | 2,470 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_pares_solo_1 ecobici visitas |
| `\rsEcoEF` | 102,135 | ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, sens_pares_sin_regla ecobici EF |
| `\rmaeMes` | septiembre de 2025 | ecosim/results/tablas.md, Exactitud del pronóstico: primer mes de prueba |
| `\rmaeMa` | 1.62 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, ma_diaria mae |
| `\rmaeMaW` | 0.81 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, ma_diaria wape |
| `\rmaeLgbmD` | 1.61 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, lgbm_diario mae |
| `\rmaeLgbmDW` | 0.81 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, lgbm_diario wape |
| `\rmaeLgbmDir` | 1.99 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, lgbm_directo mae |
| `\rmaeLgbmDirW` | 0.88 | ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, lgbm_directo wape |
| `\rantPeriodo` | enero a agosto de 2025 | ecosim/results/medicion/REPORT.md, encabezado de Topes (población de los topes hacia adelante) |
| `\rvisSinRegla` | 5,113.5 | ecosim/results/medicion/REPORT.md, Impacto, ventana 05:00–00:30, sin regla, visitas |
| `\rvisConRegla` | 2,491.5 | ecosim/results/medicion/REPORT.md, Impacto, ventana 05:00–00:30, cualquier tamaño, visitas |
| `\rvisQuedan` | 48.7 | ecosim/results/medicion/REPORT.md, Impacto, ventana 05:00–00:30, cualquier tamaño, % visitas |
| `\rpruebaPeriodo` | septiembre a diciembre de 2025 | ecosim/results/resultados.csv (tag=prueba), media por día; primer y último día de prueba |
| `\rselMes` | agosto de 2025 | ecosim/results/frozen.json seleccion (mes de los días) |
| `\rrefPeriodo` | septiembre a noviembre de 2025 | ecosim/config.py RUN1_EVAL_DAYS (primer y último día) |
| `\retiquetasDia` | 5,000 | data/derived/ecosim/damage_events.parquet × ecobici_moves.parquet, 152 días de prueba; cálculo en report/figs/numeros_run3.py (bicis que cambian de etiqueta por día) |
| `\renSitioPct` | 78 | data/derived/ecosim/damage_events.parquet × ecobici_moves.parquet, 152 días de prueba; cálculo en report/figs/numeros_run3.py (% de esos cambios con total de la estación igual) |
| `\rfdFeedE` | 87,470 | report/figs/fidelidad_ecobici.json prueba (E observado en las fotos; ecosim/results/medicion/ecobici_observado.csv) |
| `\rfdFeedF` | 17,062 | report/figs/fidelidad_ecobici.json prueba (F observado en las fotos) |
| `\rfdFeedEF` | 104,532 | report/figs/fidelidad_ecobici.json prueba (E+F observado en las fotos) |
| `\rfdSalidasDia` | 52,243 | report/figs/fidelidad_ecobici.json prueba (salidas por día, data.trips en 05:00–00:30) |
| `\recoDesvPct` | 4.4 | report/figs/fidelidad_ecobici.json prueba (arm=ecobici: (desvios_salida + desvios_llegada) / salidas, %) |
| `\rmaDesvPct` | 7.1 | report/figs/fidelidad_ecobici.json prueba (arm=ma_diaria: (desvios_salida + desvios_llegada) / salidas, %) |
| `\rorDirDesvPct` | 3.6 | report/figs/fidelidad_ecobici.json prueba (arm=oraculo_directo: (desvios_salida + desvios_llegada) / salidas, %) |
| `\rlgbmDirDesvPct` | 7.5 | report/figs/fidelidad_ecobici.json prueba (arm=lgbm_directo: (desvios_salida + desvios_llegada) / salidas, %) |
| `\rbaseDesvPct` | 27.0 | report/figs/fidelidad_ecobici.json prueba (arm=sin_rebalanceo: (desvios_salida + desvios_llegada) / salidas, %) |
| `\rfdEcoDesv` | 2,298 | report/figs/fidelidad_ecobici.json prueba (arm=ecobici, desvíos por día) |
| `\rcfDias` | 16 | report/figs/fidelidad_ecobici.json dias16 (días con los siete brazos simulados) |
| `\rcfPrimero` | 1 de septiembre de 2025 | report/figs/fidelidad_ecobici.json dias16 (primer día) |
| `\rcfUltimo` | 31 de diciembre de 2025 | report/figs/fidelidad_ecobici.json dias16 (último día) |
| `\rcfSalidas` | 856,176 | report/figs/fidelidad_ecobici.json brazos16 (Σ snap[k].salidas) |
| `\rcfEcoDesv` | 38,825 | report/figs/fidelidad_ecobici.json brazos16 (ecobici, Σ desvíos) |
| `\rcfEcoPct` | 4.53 | report/figs/fidelidad_ecobici.json brazos16 (ecobici, desvíos / salidas, %) |
| `\rcUnoSal` | 22 | report/figs/fidelidad_ecobici.json causa_resolucion (salidas desviadas en estación con entrega de Ecobici en el mismo cuarto, %) |
| `\rcUnoLle` | 35 | report/figs/fidelidad_ecobici.json causa_resolucion (llegadas desviadas en estación con recogida de Ecobici en el mismo cuarto, %) |
| `\rcUnoTSal` | 4.6 | report/figs/fidelidad_ecobici.json causa_resolucion (todas las salidas en estación con entrega en el mismo cuarto, %) |
| `\rcUnoTLle` | 5.3 | report/figs/fidelidad_ecobici.json causa_resolucion (todas las llegadas en estación con recogida en el mismo cuarto, %) |
| `\rcDosPct` | 5.2 | report/figs/fidelidad_ecobici.json causa_cero (salidas reales desde estación con 0 disponibles en la última foto previa, %) |
| `\rcDosMin` | 1.3 | report/figs/fidelidad_ecobici.json causa_cero (mínimo por día, %) |
| `\rcDosMax` | 8.2 | report/figs/fidelidad_ecobici.json causa_cero (máximo por día, %) |
| `\rcDosNR` | 83 | report/figs/fidelidad_ecobici.json causa_cero (de esas salidas, estación con no rentables > 0, %) |
| `\rcDosEdad` | 12.3 | report/figs/fidelidad_ecobici.json causa_cero (antigüedad mediana de la foto, min) |
| `\rcDosSalidas` | 855,867 | report/figs/fidelidad_ecobici.json causa_cero (salidas reales con foto previa) |
| `\rcTresEst` | 677 | report/figs/fidelidad_ecobici.json causa_arrastre (estaciones comparadas por cuarto, media) |
| `\rcTresSiete` | 22 | report/figs/fidelidad_ecobici.json causa_arrastre (por_hora 07:00, estaciones que difieren) |
| `\rcTresDoce` | 149 | report/figs/fidelidad_ecobici.json causa_arrastre (por_hora 12:00, estaciones que difieren) |
| `\rcTresPico` | 242 | report/figs/fidelidad_ecobici.json causa_arrastre (por_hora, máximo de estaciones que difieren) |
| `\rcTresPicoHora` | 22:00 | report/figs/fidelidad_ecobici.json causa_arrastre (por_hora, hora del máximo) |
| `\rcTresPicoDif` | 0.9 | report/figs/fidelidad_ecobici.json causa_arrastre (por_hora, diferencia media en la hora del máximo, bicis por estación) |
| `\rcTresCrudoMin` | 84 | report/figs/fidelidad_ecobici.json causa_arrastre (sin corregir la antigüedad de la foto, mínimo desde 06:00) |
| `\rcTresCrudoMax` | 496 | report/figs/fidelidad_ecobici.json causa_arrastre (sin corregir, máximo) |
| `\rcTresCrudoDifMax` | 2.2 | report/figs/fidelidad_ecobici.json causa_arrastre (sin corregir, diferencia media máxima) |
| `\rdTop` | 68 | report/figs/fidelidad_ecobici.json demanda (estaciones del decil con más viajes) |
| `\rdEst` | 677 | report/figs/fidelidad_ecobici.json demanda (estaciones) |
| `\rdTopViajes` | 25 | report/figs/fidelidad_ecobici.json demanda (salidas + llegadas en ese decil, %) |
| `\rdPicoHoras` | 08:00--08:59 y 14:00--18:59 | report/figs/fidelidad_ecobici.json demanda (horas_pico, las seis horas con más salidas) |
| `\rdPicoSal` | 45 | report/figs/fidelidad_ecobici.json demanda (salidas en horas pico, %) |
| `\rdEFTopEco` | 9,151 | report/figs/fidelidad_ecobici.json demanda (ecobici, EF_top, minutos por día muestreados cada 15 min) |
| `\rdEFTopMa` | 8,278 | report/figs/fidelidad_ecobici.json demanda (ma_diaria, EF_top, minutos por día muestreados cada 15 min) |
| `\rdEFRestoEco` | 93,901 | report/figs/fidelidad_ecobici.json demanda (ecobici, EF_resto, minutos por día muestreados cada 15 min) |
| `\rdEFRestoMa` | 50,340 | report/figs/fidelidad_ecobici.json demanda (ma_diaria, EF_resto, minutos por día muestreados cada 15 min) |
| `\rdETopEco` | 6,780 | report/figs/fidelidad_ecobici.json demanda (ecobici, E_top, minutos por día muestreados cada 15 min) |
| `\rdETopMa` | 7,489 | report/figs/fidelidad_ecobici.json demanda (ma_diaria, E_top, minutos por día muestreados cada 15 min) |
| `\rdEFTopRed` | 10 | report/figs/fidelidad_ecobici.json demanda (1 − EF_top(ma_diaria)/EF_top(ecobici), %) |
| `\rdEFRestoRed` | 46 | report/figs/fidelidad_ecobici.json demanda (1 − EF_resto(ma_diaria)/EF_resto(ecobici), %) |
| `\rdMaExtra` | 1,611 | report/figs/fidelidad_ecobici.json demanda (ma_diaria, desvíos por día menos los de ecobici) |
| `\rdMaExtraTop` | 42 | report/figs/fidelidad_ecobici.json demanda (ma_diaria, % de los desvíos extra en el decil) |
| `\rdMaExtraPico` | 60 | report/figs/fidelidad_ecobici.json demanda (ma_diaria, % de los desvíos extra en horas pico) |
| `\rdEcoPico` | 47 | report/figs/fidelidad_ecobici.json demanda (ecobici, % de sus desvíos en horas pico) |
| `\rdLgbmExtraTopMin` | 39 | report/figs/fidelidad_ecobici.json demanda (lgbm_diario y lgbm_directo, % de desvíos extra en el decil, mínimo) |
| `\rdLgbmExtraTopMax` | 41 | report/figs/fidelidad_ecobici.json demanda (lgbm_diario y lgbm_directo, % de desvíos extra en el decil, máximo) |
| `\rejEst` | 273-274 | report/figs/fidelidad_ecobici.json ejemplo (estación) |
| `\rejNombre` | Luis Donaldo Colosio -- Jesús García | report/figs/fidelidad_ecobici.json ejemplo (nombre en station_information) |
| `\rejDia` | 1 de septiembre de 2025 | report/figs/fidelidad_ecobici.json ejemplo (día) |
| `\rejDesde` | 08:45 | report/figs/fidelidad_ecobici.json ejemplo (inicio del cuarto) |
| `\rejHasta` | 09:00 | report/figs/fidelidad_ecobici.json ejemplo (fin del cuarto) |
| `\rejFotoA` | 1 | report/figs/fidelidad_ecobici.json ejemplo (foto de las 08:36, disponibles) |
| `\rejFotoAHora` | 08:36 | report/figs/fidelidad_ecobici.json ejemplo (hora del estado de la foto) |
| `\rejFotoB` | 0 | report/figs/fidelidad_ecobici.json ejemplo (foto de las 08:48, disponibles) |
| `\rejFotoBHora` | 08:48 | report/figs/fidelidad_ecobici.json ejemplo (hora del estado de la foto) |
| `\rejFotoC` | 30 | report/figs/fidelidad_ecobici.json ejemplo (foto de las 09:06, disponibles) |
| `\rejFotoCHora` | 09:06 | report/figs/fidelidad_ecobici.json ejemplo (hora del estado de la foto) |
| `\rejMovA` | 19 | report/figs/fidelidad_ecobici.json ejemplo (movimiento de Ecobici en 273-274, ecobici_moves.parquet) |
| `\rejMovAHora` | 08:36 | report/figs/fidelidad_ecobici.json ejemplo (hora del movimiento (t1)) |
| `\rejMovB` | 79 | report/figs/fidelidad_ecobici.json ejemplo (movimiento de Ecobici en 273-274, ecobici_moves.parquet) |
| `\rejMovBHora` | 09:06 | report/figs/fidelidad_ecobici.json ejemplo (hora del movimiento (t1)) |
| `\rejMovCincuenta` | 10 | report/figs/fidelidad_ecobici.json ejemplo (movimiento de Ecobici en 554) |
| `\rejMovCuarenta` | 11 | report/figs/fidelidad_ecobici.json ejemplo (movimiento de Ecobici en 547) |
| `\rejMovVecHora` | 08:48 | report/figs/fidelidad_ecobici.json ejemplo (hora de esos movimientos) |
| `\rejSal` | 40 | report/figs/fidelidad_ecobici.json ejemplo (salidas reales del cuarto desde la estación) |
| `\rejDesv` | 41 | report/figs/fidelidad_ecobici.json ejemplo (salidas desviadas en el simulador en ese cuarto) |
| `\rejDosN` | 9 | report/figs/fidelidad_ecobici.json ejemplo (cadena, desvíos a la 265) |
| `\rejDosM` | 220 | report/figs/fidelidad_ecobici.json ejemplo (cadena, distancia en línea recta a la 265, m) |
| `\rejCincoN` | 6 | report/figs/fidelidad_ecobici.json ejemplo (cadena, desvíos a la 554) |
| `\rejCincoM` | 301 | report/figs/fidelidad_ecobici.json ejemplo (cadena, distancia en línea recta a la 554, m) |
| `\rejCuatroN` | 3 | report/figs/fidelidad_ecobici.json ejemplo (cadena, desvíos a la 547) |
| `\rejCuatroM` | 591 | report/figs/fidelidad_ecobici.json ejemplo (cadena, distancia en línea recta a la 547, m) |
| `\rejCeroN` | 7 | report/figs/fidelidad_ecobici.json ejemplo (cadena, desvíos a la 029) |
| `\rejCeroM` | 907 | report/figs/fidelidad_ecobici.json ejemplo (cadena, distancia en línea recta a la 029, m) |
| `\rejVecIni` | 21 | report/figs/fidelidad_ecobici.json ejemplo (bicis simuladas en 265 al inicio del cuarto) |
| `\rejVecFin` | 0 | report/figs/fidelidad_ecobici.json ejemplo (bicis simuladas en 265 al final del cuarto) |
| `\rejNVec` | 13 | report/figs/fidelidad_ecobici.json ejemplo (cadena, estaciones distintas que reciben desvíos) |
| `\rfigPeriodo` | septiembre a noviembre de 2025 | FIG_MONTHS en report/figs/numeros_run3.py |
| `\rfigVisitas` | 210,974 | data/derived/ecosim/ecobici_moves.parquet, sep–nov 2025, sin pares; cálculo en report/figs/numeros_run3.py (visitas en la figura de tamaños) |
| `\rfigBajoTope` | 97.0 | data/derived/ecosim/ecobici_moves.parquet, sep–nov 2025, sin pares; cálculo en report/figs/numeros_run3.py (% de visitas con bicis ≤ tope) |
| `\rfigIntervalos` | 6,051 | data/derived/ecosim/ecobici_moves.parquet, sep–nov 2025, sin pares; cálculo en report/figs/numeros_run3.py (intervalos en la figura de visitas) |
| `\rfigVisBajoTope` | 87.8 | data/derived/ecosim/ecobici_moves.parquet, sep–nov 2025, sin pares; cálculo en report/figs/numeros_run3.py (% de intervalos con visitas ≤ tope) |

## Tablas y figuras

| artefacto | origen |
|---|---|
| `tablas/principal-run3.tex` | resultados.csv (tag=prueba), IC95 t pareado por día |
| `tablas/brazos-run3.tex` | frozen.json lambda_por_brazo |
| `tablas/pares-run3.tex` | medicion/REPORT.md, Impacto, ventana 05:00–00:30 |
| `tablas/fidelidad-run3.tex` | fidelidad_ecobici.json `prueba` (días de prueba): E y F de ecobici_observado.csv y resultados.csv; desvíos de resultados.csv / salidas de data.trips |
| `tablas/causa-cero-run3.tex` | fidelidad_ecobici.json `causa_cero.por_dia` (16 días) |
| `tablas/desvios16-run3.tex` | fidelidad_ecobici.json `brazos16` (16 días, Σ snap[k]) |
| `figs/tamanos-run3.pdf` | ecobici_moves.parquet, sep–nov 2025, sin pares; tope de frozen.json |
| `figs/visitas-tope-run3.pdf` | ecobici_moves.parquet, sep–nov 2025, visitas por intervalo × 15 / minutos del intervalo |
| `figs/ef-brazos-run3.pdf` | resultados.csv (tag=prueba), media de EF por brazo |

## Cifras externas o ilustrativas escritas en prosa

- 1,990 de 4,139 respuestas eligen "no siempre hay bicis disponibles" como principal desventaja; 114 eligen "no siempre hay espacios para anclar": Encuesta ECOBICI 2025, pregunta 18, `encuesta2025` en referencias.bib (https://ecobici.cdmx.gob.mx/wp-content/uploads/2026/02/Encuesta-ECOBICI-2025-1.pdf).
- 34.56 millones de viajes en un día entre semana: EOD 2017 del INEGI, `inegi2017`.
- 9,308 bicicletas; BikeSantiago y BikeItaú, 3,500 cada uno: El Universal 2025-07-23 con cifras de Semovi, `eluniversal2025semovi`.
- unas 687 estaciones: Expansión Política 2026-08-31, `expansion2026`.
- más de 19.4 millones de viajes y 284,289 personas usuarias en 2025: Ecobici 2025-12-22, `ecobici2025balance`.
- 05:00 a 00:30: Términos y condiciones de Ecobici, `ecobici_horario`.
- 13.5 km/h en hora pico: TomTom Traffic Index 2025, `tomtom2025`.
- trayectos de hasta unos 70 minutos en hora pico: consulta en Waze (sin fuente archivada; cifra de contexto).
- el feed se actualiza cada diez segundos: campo `ttl` = 10 s de https://gbfs.mex.lyftbikes.com/gbfs/gbfs.json.
- 12:07:09, bici 4201141, estación 002 (ejemplo de par): ecosim/results/medicion/REPORT.md, Ejemplos con viajes exactos.
- estación con cinco disponibles y dos no rentables (Tabla de etiquetas): ejemplo ilustrativo, no es dato medido.
