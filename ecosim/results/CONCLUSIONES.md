# Conclusiones — ecosim run 3

Resultados dentro del simulador, no promesa operacional ni rutas verificadas.

El mejor brazo real elegido en agosto fue **ma_diaria**. En la prueba, Ecobici obtuvo 100,641 minutos-estación vacíos o llenos por día; el brazo real, 56,542 (43.8% menos).

## Brazos con esfuerzo equiparado en selección (no en prueba)

| brazo | E+F_menos_pct | IC95_pct_inf | IC95_pct_sup | gana | empata | visitas_pct |
|---|---|---|---|---|---|---|
| oraculo_diario | 63.07 | 60.65 | 65.50 | 152 | 0 | -12.45 |
| ma_diaria | 43.82 | 41.65 | 45.98 | 152 | 0 | -10.58 |
| lgbm_diario | 43.34 | 41.16 | 45.51 | 152 | 0 | -10.56 |
| oraculo_directo | 71.29 | 68.29 | 74.28 | 152 | 0 | -11.99 |
| lgbm_directo | 43.77 | 41.74 | 45.81 | 152 | 0 | -10.91 |

El esfuerzo se ajustó en los 15 días de selección, no en los meses de prueba. En prueba ma_diaria hizo 10.6% menos visitas que Ecobici, así que estos resultados no son estrictamente a igual esfuerzo fuera de muestra. Cuando λ=60 aún movía más que Ecobici se extrapoló por encima de la rejilla y se confirmó con otra corrida; `frozen.json` conserva intentos y error residual. La tabla muestra las visitas fuera de muestra.
El replay de Ecobici tiene neto aplicado medio +66.2 bicis/día. Si es positivo, incorpora bicicletas externas dentro de la ventana y favorece a Ecobici frente a las políticas cerradas.

## Pronóstico y actualización

ma_diaria menos oraculo_diario: costo del error 19,379 min/día (IC95 [18,660, 20,098]); 0 días favorecen al modelo, 0 empatan.
lgbm_diario menos oraculo_diario: costo del error 19,863 min/día (IC95 [19,044, 20,683]); 0 días favorecen al modelo, 0 empatan.
lgbm_directo menos oraculo_directo: costo del error 27,692 min/día (IC95 [26,104, 29,279]); 0 días favorecen al modelo, 0 empatan.
Actualización en el oráculo (directo − diario): -8,266 min/día, IC95 [-9,060, -7,472].

## Diciembre y enero

2025-12: 39.1% menos E+F que Ecobici; IC95 de diferencia [-39,676, -29,331] min/día, gana 31/31 días, empata 0.
2026-01: 39.7% menos E+F que Ecobici; IC95 de diferencia [-42,295, -32,892] min/día, gana 31/31 días, empata 0.

## Topes: referencia y enero–agosto

Caso base 67 visitas/decisión y 14 bicis/visita (referencia sep–nov); hacia adelante 47 y 19 (p95 ene–ago). Sensibilidades p99 83/24 y 62/33, respectivamente.

| mes | visitas_p95 | bicis_p95 | visitas_p99 | bicis_p99 |
|---|---|---|---|---|
| 2025-01 | 40 | 20 | 48 | 40 |
| 2025-02 | 45 | 20 | 66 | 38 |
| 2025-03 | 41 | 20 | 51 | 37 |
| 2025-04 | 42 | 20 | 49 | 36 |
| 2025-05 | 45 | 19 | 55 | 33 |
| 2025-06 | 50 | 17 | 59 | 28 |
| 2025-07 | 55 | 19 | 68 | 36 |
| 2025-08 | 58 | 14 | 77 | 25 |
| 2025-09 | 62 | 14 | 79 | 24 |
| 2025-10 | 66 | 14 | 81 | 24 |
| 2025-11 | 68 | 13 | 81 | 23 |

## Por mes y limitaciones

| mes | brazo | E+F | visitas | replay_neto |
|---|---|---|---|---|
| 2025-09 | ecobici | 105,247.62 | 2,147.86 | 28.62 |
| 2025-09 | ma_diaria | 54,284.86 | 2,067.83 | 0.00 |
| 2025-10 | ecobici | 107,651.35 | 2,445.81 | 87.03 |
| 2025-10 | ma_diaria | 58,328.65 | 2,228.77 | 0.00 |
| 2025-11 | ecobici | 107,668.47 | 2,428.87 | 28.53 |
| 2025-11 | ma_diaria | 58,962.40 | 2,104.37 | 0.00 |
| 2025-12 | ecobici | 88,350.16 | 2,028.23 | 58.74 |
| 2025-12 | ma_diaria | 53,846.68 | 1,697.55 | 0.00 |
| 2026-01 | ecobici | 94,810.97 | 2,131.10 | 124.29 |
| 2026-01 | ma_diaria | 57,217.42 | 1,908.10 | 0.00 |
| TOTAL | ecobici | 100,640.93 | 2,236.27 | 66.17 |
| TOTAL | ma_diaria | 56,541.50 | 1,999.77 | 0.00 |

La variante de dañadas que sigue el feed aplica flujos externos distintos entre brazos tras recortes físicos: `damage_external` se publica junto a E+F; no aisla causalmente el efecto de dañadas.
Marzo de 2026: 23–31 excluidos como verdad. De los ocho días de abril de `prod_2026`, siete tienen por lo menos un rezago 7/14 días artificialmente a cero; el 6 de abril tiene ambos. `rezagos_abril.csv` cuenta los bloques-estación afectados; no imputa viajes ni cuantifica un contrafactual inexistente.

## Comparación con runs anteriores

Run 1 fijaba dañadas y run 2 permitía bodega neta y entrega simultánea; aquí dañadas cambian en sitio, cada decisión equilibra bicis y entrega a +60 min. Por ello los porcentajes de runs 1 y 2 no son directamente comparables; no se reemplazan sus cifras históricas.


## Sensibilidades en los 32 días de curva

oraculo_directo, p99: 27,274 E+F/día; Δ vs 67/14 y entrega +60 = -1,717 (IC95 [-2,265, -1,169]); flujo externo de dañadas +0.0 bicis/día.
oraculo_directo, hacia_adelante: 30,112 E+F/día; Δ vs 67/14 y entrega +60 = +1,122 (IC95 [579, 1,664]); flujo externo de dañadas +0.0 bicis/día.
oraculo_directo, entrega_45: 25,552 E+F/día; Δ vs 67/14 y entrega +60 = -3,438 (IC95 [-4,104, -2,773]); flujo externo de dañadas +0.0 bicis/día.
oraculo_directo, entrega_75: 32,525 E+F/día; Δ vs 67/14 y entrega +60 = +3,534 (IC95 [2,802, 4,266]); flujo externo de dañadas +0.0 bicis/día.
oraculo_directo, danadas_feed: 29,011 E+F/día; Δ vs 67/14 y entrega +60 = +20 (IC95 [-287, 327]); flujo externo de dañadas -76.8 bicis/día.
ma_diaria, p99: 55,265 E+F/día; Δ vs 67/14 y entrega +60 = -2,275 (IC95 [-3,078, -1,472]); flujo externo de dañadas +0.0 bicis/día.
ma_diaria, hacia_adelante: 58,274 E+F/día; Δ vs 67/14 y entrega +60 = +735 (IC95 [188, 1,281]); flujo externo de dañadas +0.0 bicis/día.
ma_diaria, entrega_45: 50,070 E+F/día; Δ vs 67/14 y entrega +60 = -7,470 (IC95 [-8,514, -6,425]); flujo externo de dañadas +0.0 bicis/día.
ma_diaria, entrega_75: 65,034 E+F/día; Δ vs 67/14 y entrega +60 = +7,494 (IC95 [6,294, 8,694]); flujo externo de dañadas +0.0 bicis/día.
ma_diaria, danadas_feed: 58,375 E+F/día; Δ vs 67/14 y entrega +60 = +835 (IC95 [166, 1,504]); flujo externo de dañadas -75.2 bicis/día.

## 2026: solo contra oráculos y sin rebalanceo

El feed de 2026 no permite medir el esfuerzo/E+F de Ecobici; aquí no se simula ese brazo.
2026-02, ma_diaria − oraculo_diario: 19,277 min/día (IC95 [15,935, 22,620]); referencia 2025 en tablas.md.
2026-02, lgbm_diario − oraculo_diario: 19,182 min/día (IC95 [16,092, 22,273]); referencia 2025 en tablas.md.
2026-02, lgbm_directo − oraculo_directo: 21,970 min/día (IC95 [17,269, 26,670]); referencia 2025 en tablas.md.
2026-03, ma_diaria − oraculo_diario: 19,506 min/día (IC95 [15,560, 23,452]); referencia 2025 en tablas.md.
2026-03, lgbm_diario − oraculo_diario: 19,972 min/día (IC95 [15,814, 24,129]); referencia 2025 en tablas.md.
2026-03, lgbm_directo − oraculo_directo: 21,701 min/día (IC95 [13,795, 29,607]); referencia 2025 en tablas.md.
2026-04, ma_diaria − oraculo_diario: 25,663 min/día (IC95 [22,374, 28,953]); referencia 2025 en tablas.md.
2026-04, lgbm_diario − oraculo_diario: 27,148 min/día (IC95 [24,812, 29,484]); referencia 2025 en tablas.md.
2026-04, lgbm_directo − oraculo_directo: 39,919 min/día (IC95 [34,324, 45,515]); referencia 2025 en tablas.md.
2026-05, ma_diaria − oraculo_diario: 21,012 min/día (IC95 [15,766, 26,258]); referencia 2025 en tablas.md.
2026-05, lgbm_diario − oraculo_diario: 20,688 min/día (IC95 [16,220, 25,156]); referencia 2025 en tablas.md.
2026-05, lgbm_directo − oraculo_directo: 23,977 min/día (IC95 [17,956, 29,998]); referencia 2025 en tablas.md.
2026-06, ma_diaria − oraculo_diario: 20,292 min/día (IC95 [-3,132, 43,715]); referencia 2025 en tablas.md.
2026-06, lgbm_diario − oraculo_diario: 19,452 min/día (IC95 [16,586, 22,317]); referencia 2025 en tablas.md.
2026-06, lgbm_directo − oraculo_directo: 21,480 min/día (IC95 [1,372, 41,587]); referencia 2025 en tablas.md.
2026-08, ma_diaria − oraculo_diario: 19,274 min/día (IC95 [11,621, 26,927]); referencia 2025 en tablas.md.
2026-08, lgbm_diario − oraculo_diario: 19,989 min/día (IC95 [13,576, 26,402]); referencia 2025 en tablas.md.
2026-08, lgbm_directo − oraculo_directo: 27,649 min/día (IC95 [16,695, 38,603]); referencia 2025 en tablas.md.
