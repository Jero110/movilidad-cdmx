# Tope de bicis por movimiento: evidencia y justificación

Análisis hecho al cierre del run 1 (2026-09-28) para decidir el tope del run 2.

## Qué es

Un **movimiento** es una orden en una estación e intervalo: poner o quitar N bicis. En el run 1 no
había límite a N: una sola orden podía mover 74 bicis de golpe. Ecobici no puede hacer eso, porque cada
visita la hace una camioneta con capacidad finita.

## Datos

Los mismos 15 días de evaluación (`ecosim/days.json`), 05:30–12:30. Ecobici = movimientos medidos desde
GBFS (`ecobici_moves.parquet`, hora corregida −30 s). Nuestros brazos = órdenes efectivamente
aplicadas por el simulador, en la mejor configuración del run 1 (bloque 60, H = 3, L = 60, λ = 60, μ = 1,
tope por hora 512, tope de bodega 180). Archivos:

- `bicis_por_movimiento_distribucion.csv` y `bicis_por_movimiento_percentiles.csv` (esta carpeta);
- datos crudos, un renglón por movimiento: `data/derived/ecosim_run1/bicis_por_movimiento.parquet`
  (columnas `arm`, `day`, `n` = |bicis|).

### Distribución (número de movimientos en los 15 días, y %)

| bicis en el movimiento | Ecobici (todo) | Ecobici sin ±1 | nuestro `ma` | nuestro oracle |
|---|---|---|---|---|
| 1 | 19,674 (63.2%) | 6,079 (34.7%) | 1,442 (14.9%) | 1,580 (20.7%) |
| 2 | 2,390 (7.7%) | 2,390 (13.6%) | 1,947 (20.1%) | 1,261 (16.5%) |
| 3 | 814 (2.6%) | 814 (4.6%) | 1,225 (12.6%) | 724 (9.5%) |
| 4 | 1,562 (5.0%) | 1,562 (8.9%) | 796 (8.2%) | 487 (6.4%) |
| 5 | 1,864 (6.0%) | 1,864 (10.6%) | 563 (5.8%) | 367 (4.8%) |
| 6–10 | 3,349 (10.8%) | 3,349 (19.1%) | 1,673 (17.3%) | 1,323 (17.3%) |
| 11–14 | 710 (2.3%) | 710 (4.0%) | 679 (7.0%) | 631 (8.3%) |
| 15–20 | 510 (1.6%) | 510 (2.9%) | 647 (6.7%) | 651 (8.5%) |
| 21–30 | 201 (0.6%) | 201 (1.1%) | 541 (5.6%) | 468 (6.1%) |
| 31–42 | 43 (0.1%) | 43 (0.2%) | 111 (1.1%) | 103 (1.3%) |
| 43–60 | 13 (0.0%) | 13 (0.1%) | 35 (0.4%) | 20 (0.3%) |
| 61+ | 3 (0.0%) | 3 (0.0%) | 26 (0.3%) | 24 (0.3%) |
| **total** | 31,133 | 17,538 | 9,685 | 7,639 |
| **por día** | 2,076 | 1,169 | 646 | 509 |
| **media bicis por movimiento** | 3.0 | 4.5 | 7.0 | 7.5 |
| **bicis movidas por día** | 6,187 | 5,280 | 4,546 | 3,812 |

### Percentiles de bicis por movimiento

| | p50 | p90 | p95 | p99 | máx |
|---|---|---|---|---|---|
| Ecobici (todo) | 1 | 7 | 10 | 20 | 72 |
| Ecobici sin ±1 | 3 | 10 | 14 | 22 | 72 |
| nuestro `ma` | 4 | 18 | 24 | 35 | 74 |
| nuestro oracle | 4 | 19 | 24 | 36 | 74 |

### Qué porcentaje de los movimientos pasa de cada tope

| tope | Ecobici sin ±1 | `ma` | oracle |
|---|---|---|---|
| 42 | 0.1% | 0.6% | 0.6% |
| 22 | 1.0% | 5.8% | 6.2% |
| 14 | 4.4% | 14.0% | 16.6% |

## Lectura

- Ecobici hace sobre todo visitas chicas: 63% de sus movimientos son de 1 bici (35% sin los pares ±1).
- Nosotros concentramos: menos visitas (646 contra 1,169 por día sin ±1) con más bicis cada una (p95 24
  contra 14), y en total **movemos menos bicis que Ecobici** (4,546 contra 5,280 por día).
- Los dos hacemos visitas muy grandes en hubs (271-272, 266-267…): Ecobici llega a 72 y nosotros a 74.

## Decisión recomendada

**Tope principal: 42 bicis por movimiento. Sensibilidades: 22 (p99 de Ecobici sin ±1) y 14 (p95).**

1. **42 es el único número con base física:** capacidad de una camioneta de Ecobici con remolque
   (Expansión Política, 2026-08-31; ver `wiki/Rebalanceo-Ecobici-fuentes-operativas-2026.md`).
   14 o 22 no son límites físicos: copian cómo *decide* operar Ecobici, no lo que *puede* hacer.
2. **Ecobici respeta ese límite en la práctica:** 99.7% de sus movimientos son de ≤ 42 bicis. En hubs
   lo rebasa (16 movimientos de 43+ en 15 días), así que un tope bajo castigaría justo donde Ecobici
   también hace visitas grandes.
3. **La ventaja no viene de mover más bicis:** movemos menos en total. Concentrar en menos visitas es
   una estrategia válida si la camioneta lo carga.
4. **Las sensibilidades cierran la duda:** si con 14 seguimos ganando, el resultado no depende de las
   visitas grandes.

## Limitación que sigue abierta

El tope por visita no limita el esfuerzo total. La restricción real es la flota: camionetas × paradas
por hora × capacidad. La Silla Rota reporta que las camionetas bajaron de 7–8 a 2–3 recorridos por turno,
pero no hay dato público del número de camionetas. Hasta tenerlo, el esfuerzo total se acota solo con el
tope de movimientos por hora medido de Ecobici, y esto se declara como limitación.
