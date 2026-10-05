# Medición de Ecobici — run 3

Regla: `delta = Δdisponibles + Δdañadas − (llegadas − salidas)` por estación y fotos consecutivas; hora de estado = commit −30 s. Pares opuestos inmediatos de **cualquier tamaño** se marcan `par`, se excluyen del esfuerzo y suman cero. `damage_events.parquet` contiene solo cambios observados `sube`/`baja` en t1. Las no rentables del feed no son necesariamente bicis averiadas.

## Topes (enero–agosto 2025, cobertura ≥90%, 230 días)

Visitas por foto normalizadas a 15 min (visitas × 15 / duración real); tamaño absoluto por visita, sin pares. 15237 fotos, mediana de 16.1 min entre fotos.

| tope | recalculado | referencia | diferencia |
|---|---|---|---|
| visitas_p95 | 47 | 67 | -20 |
| bicis_p95 | 19 | 14 | 5 |
| visitas_p99 | 62 | 83 | -21 |
| bicis_p99 | 33 | 24 | 9 |

La referencia usa septiembre–noviembre y 05:30; el caso base usa enero–agosto y 05:00. La diferencia de población no se corrige ajustando la regla. El p95 de visitas de los 15 días de referencia sin redondear es 67.1605 y el p99 83.2370, ambos redondeados al entero más cercano.

### Evolución mensual de los topes (días de cobertura ≥90%)

| mes | ventana | dias | fotos | visitas_p95 | visitas_p99 | bicis_p95 | bicis_p99 |
|---|---|---|---|---|---|---|---|
| 2025-01 | 05:00 | 31 | 2172 | 40 | 48 | 20 | 40 |
| 2025-01 | 05:30 | 31 | 2110 | 41 | 48 | 20 | 40 |
| 2025-02 | 05:00 | 28 | 1977 | 45 | 66 | 20 | 38 |
| 2025-02 | 05:30 | 28 | 1921 | 45 | 68 | 20 | 38 |
| 2025-03 | 05:00 | 31 | 2145 | 41 | 51 | 20 | 37 |
| 2025-03 | 05:30 | 31 | 2083 | 41 | 51 | 20 | 37 |
| 2025-04 | 05:00 | 30 | 2039 | 42 | 49 | 20 | 36 |
| 2025-04 | 05:30 | 30 | 1981 | 42 | 49 | 20 | 36 |
| 2025-05 | 05:00 | 30 | 2008 | 45 | 55 | 19 | 33 |
| 2025-05 | 05:30 | 30 | 1955 | 46 | 55 | 19 | 33 |
| 2025-06 | 05:00 | 30 | 1975 | 50 | 59 | 17 | 28 |
| 2025-06 | 05:30 | 30 | 1916 | 51 | 61 | 17 | 28 |
| 2025-07 | 05:00 | 19 | 891 | 55 | 68 | 19 | 36 |
| 2025-07 | 05:30 | 19 | 872 | 56 | 68 | 19 | 36 |
| 2025-08 | 05:00 | 31 | 2030 | 58 | 77 | 14 | 25 |
| 2025-08 | 05:30 | 31 | 1980 | 58 | 78 | 14 | 25 |
| 2025-09 | 05:00 | 29 | 1991 | 62 | 79 | 14 | 24 |
| 2025-09 | 05:30 | 29 | 1933 | 62 | 79 | 14 | 24 |
| 2025-10 | 05:00 | 31 | 2107 | 66 | 81 | 14 | 24 |
| 2025-10 | 05:30 | 31 | 2047 | 66 | 81 | 14 | 24 |
| 2025-11 | 05:00 | 30 | 1988 | 68 | 81 | 13 | 23 |
| 2025-11 | 05:30 | 30 | 1930 | 68 | 81 | 13 | 23 |

### Auditoría de visitas grandes (≥20 bicis, sin pares)

| mes | ventana | visitas_ge20 | tras_hueco_30min | primera_foto_estacion | despues_de_blanco |
|---|---|---|---|---|---|
| 2025-01 | 05:00 | 3276 | 1011 | 0 | 0 |
| 2025-01 | 05:30 | 3276 | 1011 | 2 | 0 |
| 2025-02 | 05:00 | 2573 | 714 | 0 | 0 |
| 2025-02 | 05:30 | 2571 | 714 | 2 | 0 |
| 2025-03 | 05:00 | 2401 | 686 | 1 | 0 |
| 2025-03 | 05:30 | 2400 | 686 | 0 | 0 |
| 2025-04 | 05:00 | 2336 | 807 | 0 | 0 |
| 2025-04 | 05:30 | 2336 | 807 | 0 | 0 |
| 2025-05 | 05:00 | 2178 | 797 | 0 | 0 |
| 2025-05 | 05:30 | 2179 | 797 | 2 | 0 |
| 2025-06 | 05:00 | 1870 | 730 | 1 | 0 |
| 2025-06 | 05:30 | 1869 | 730 | 0 | 0 |
| 2025-07 | 05:00 | 1493 | 694 | 3 | 0 |
| 2025-07 | 05:30 | 1489 | 694 | 0 | 0 |
| 2025-08 | 05:00 | 1585 | 526 | 1 | 0 |
| 2025-08 | 05:30 | 1584 | 526 | 0 | 0 |
| 2025-09 | 05:00 | 1504 | 452 | 0 | 0 |
| 2025-09 | 05:30 | 1504 | 452 | 1 | 0 |
| 2025-10 | 05:00 | 1886 | 593 | 2 | 0 |
| 2025-10 | 05:30 | 1884 | 593 | 0 | 0 |
| 2025-11 | 05:00 | 1525 | 529 | 3 | 0 |
| 2025-11 | 05:30 | 1522 | 529 | 1 | 0 |

Los conteos de hueco, primera foto y blanco pueden traslaparse. Una visita de tamaño grande tras intervalo largo mide el neto del intervalo, no necesariamente una única parada de camión. Enero tiene p95 de 40 visitas frente a 68 en noviembre; en enero las visitas p95 mueven 20 bicis frente a 13 en noviembre. La subida de frecuencia y caída de tamaño son graduales, no las causa el cambio de ventana: cada mes 05:00 y 05:30 difieren a lo sumo en unas pocas visitas. Los huecos de más de 30 min sí concentran parte de los movimientos ≥20; no se filtran.

## Comprobación de sanidad (15 días de evaluación)

| ventana | visitas | A | R | neto |
|---|---|---|---|---|
| 05:30–00:30 | 2,482.0 | 5,539.1 | 5,559.7 | -20.6 |
| 05:00–00:30 | 2,491.5 | 5,568.4 | 5,584.6 | -16.2 |

Referencias para 05:30: 2,482 visitas, 5,539 metidas, 5,560 sacadas, neto −21 bicis/día. Con pares incluidos el neto no cambia. En los 15 días de evaluación (05:00), |A − R| medio por día = 54.3 bicis (<60); en los 230 días de topes de enero–agosto es 60.7 bicis (ligeramente >60, no se oculta). La medición no infiere taller ni crea flota con ninguna regla.

## Impacto de la regla, ventana 05:30–00:30 (media por día)

| regla | visitas | A | R | neto | % visitas | % bicis |
|---|---|---|---|---|---|---|
| sin regla | 5,095.2 | 7,009.2 | 7,029.8 | -20.6 | 100.0 | 100.0 |
| ±1 | 2,713.2 | 5,818.2 | 5,838.8 | -20.6 | 53.3 | 83.0 |
| hasta ±2 | 2,533.3 | 5,638.3 | 5,658.9 | -20.6 | 49.7 | 80.4 |
| hasta ±3 | 2,503.7 | 5,593.9 | 5,614.5 | -20.6 | 49.1 | 79.8 |
| cualquier tamaño | 2,482.0 | 5,539.1 | 5,559.7 | -20.6 | 48.7 | 79.0 |

## Impacto, ventana 05:00–00:30 (media por día)

| regla | visitas | A | R | neto | % visitas | % bicis |
|---|---|---|---|---|---|---|
| sin regla | 5,113.5 | 7,042.9 | 7,059.1 | -16.2 | 100.0 | 100.0 |
| ±1 | 2,722.9 | 5,847.6 | 5,863.8 | -16.2 | 53.2 | 83.0 |
| hasta ±2 | 2,542.9 | 5,667.6 | 5,683.8 | -16.2 | 49.7 | 80.5 |
| hasta ±3 | 2,513.3 | 5,623.2 | 5,639.4 | -16.2 | 49.1 | 79.8 |
| cualquier tamaño | 2,491.5 | 5,568.4 | 5,584.6 | -16.2 | 48.7 | 79.1 |

## Evidencia de pares (15 días de evaluación)

Pares por actividad de viajes en dos intervalos consecutivos de la estación:

| grupo | intervalos | pares | por_1000 |
|---|---|---|---|
| 0 | 106784 | 45 | 0.42 |
| 1–2 | 171965 | 2405 | 13.99 |
| 3–5 | 170317 | 5324 | 31.26 |
| 6–10 | 126294 | 6228 | 49.31 |
| >10 | 82484 | 5597 | 67.86 |

El mismo gradiente desglosado para tamaños 1, 2 y 3:

| viajes | tamaño | pares | por_1000 |
|---|---|---|---|
| 0 | 1 | 39 | 0.37 |
| 0 | 2 | 2 | 0.02 |
| 0 | 3 | 0 | 0.00 |
| 1–2 | 1 | 2337 | 13.59 |
| 1–2 | 2 | 60 | 0.35 |
| 1–2 | 3 | 3 | 0.02 |
| 3–5 | 1 | 4977 | 29.22 |
| 3–5 | 2 | 298 | 1.75 |
| 3–5 | 3 | 37 | 0.22 |
| 6–10 | 1 | 5620 | 44.50 |
| 6–10 | 2 | 474 | 3.75 |
| 6–10 | 3 | 86 | 0.68 |
| >10 | 1 | 4892 | 59.31 |
| >10 | 2 | 515 | 6.24 |
| >10 | 3 | 96 | 1.16 |

Cercanía del viaje a la foto intermedia (las otras fotos sirven de contraste):

| grupo | n | viaje_30s_% | viaje_120s_% | mediana_s |
|---|---|---|---|---|
| pares ±1 | 17865 | 43.3 | 77.8 | 40.0 |
| otras fotos | 638245 | 11.5 | 34.6 | 224.0 |

### Ejemplos con viajes exactos

- **±1**, estación 002, 2025-09-03: fotos 2025-09-03 11:48:28 → 2025-09-03 12:08:14 → 2025-09-03 12:25:18; movimientos [1, -1]. Viajes: bici 4201141 (002 → 192-193, salida 2025-09-03 12:07:09, llegada 2025-09-03 12:22:03).
- **±2**, estación 023, 2025-09-03: fotos 2025-09-03 22:07:16 → 2025-09-03 22:26:44 → 2025-09-03 22:36:59; movimientos [2, -2]. Viajes: bici 6089221 (023 → 118, salida 2025-09-03 22:25:37, llegada 2025-09-03 22:33:03); bici 8070161 (023 → 073, salida 2025-09-03 22:25:39, llegada 2025-09-03 22:34:01).
- **±3**, estación 195, 2025-09-15: fotos 2025-09-15 13:48:29 → 2025-09-15 14:07:34 → 2025-09-15 14:24:21; movimientos [3, -3]. Viajes: bici 4481933 (195 → 052, salida 2025-09-15 14:06:54, llegada 2025-09-15 14:58:10); bici 3021250 (195 → 192-193, salida 2025-09-15 14:07:20, llegada 2025-09-15 14:13:23); bici 2022929 (195 → 192-193, salida 2025-09-15 14:07:25, llegada 2025-09-15 14:13:21).

## Limitaciones

Solo se ve el neto por estación e intervalo. Los pares explican desfase de relojes, no se prueba la causa individual. Desde febrero de 2026 el caché guarda solo primera foto y cambios de dañadas: hay eventos de etiqueta, pero **no** movimientos ni E/F diarios observables; no se imputan como ceros. E/F de días completos es un escalón del feed (blancos y tramos sin foto se contabilizan aparte).
