# ecosim run 1 — preguntas después del run y decisiones para el run 2

Conversación del usuario con el executor al cierre del run (2026-09-28). Todos los números salen de
`ecosim/results/resultados.csv` y `ecosim/results/v1/v1_por_dia.csv` (media por día, 05:30–12:30).

## 1. ¿El simulador está bien hecho?

- **Hace lo que dicen las reglas 1.4:** 19 tests a mano, y la review escribió un simulador de
  referencia independiente que coincide exacto, desvío por desvío, en 6/6 casos (3 días × baseline y
  replay).
- **Contra la realidad (V1), a medias:** replay de Ecobici E 28,087 vs 34,878 observado (−18.5%, falla
  el criterio de 10%); F −4.1% (pasa); error de stock 0.68 bicis; desvíos 0.7% de los viajes.
- **Causa del fallo en vacías: dañadas fijas a las 05:30.** Una bici que se marca dañada durante la
  mañana sigue siendo rentable en el simulador, así que la estación no se vacía. El error por día sigue
  al aumento de dañadas: 09-03 (426 → 1,142) −52%; 10-15 (+377) −34%; 09-15 (+67) −2%; 11-24 (+95) −3%.
- Toda la comparación de brazos es simulador contra simulador (mismo sesgo para todos; no se midió si
  en igual magnitud). No es ahorro en la calle.

## 2. Qué es cada brazo (todos con el mismo asignador; solo cambia el pronóstico)

- **oracle:** viajes reales del día (techo). "V2" = oracle con bloque 60, H = 2, L = 60 (16,623);
  "mejor configuración" = H = 3 (13,798), elegida in-sample.
- **ma:** promedio del bloque en los mismos días tipo de las 4 semanas anteriores (18,822).
- **model:** LightGBM Poisson chico sin tunear (18,888) — no le gana a `ma`.
- **daily (como se implementó):** total de la mañana de `ma` repartido parejo entre bloques (19,647).
  **No es lo que el usuario pidió** (ver §6).
- Todos se emiten **una sola vez a las 05:30**; la forma es idéntica (salidas y llegadas por estación
  y bloque).

## 3. ¿Por qué tan pocos movimientos?

| | movimientos/día | bicis puestas (A) | quitadas (R) | bicis por movimiento |
|---|---|---|---|---|
| Ecobici | 2,042 (1,155 sin pares ±1) | 2,983 | 3,089 | 3.0 |
| oracle H3 | 509 | 2,008 | 1,804 | 7.5 |
| ma H3 | 646 | 2,435 | 2,111 | 7.0 |

Menos movimientos pero ~75% de las bicis movidas: visitas grandes; λ = 60 descarta movimientos chicos;
los movimientos de Ecobici incluyen ruido ±1; y **no hay camiones, rutas ni tope de bicis por
movimiento** (ventaja no justa).

## 4. Bodega (A − R), por día

- Ecobici es negativo los 15 días (−24 a −265; media −106); `ma` es positivo los 15 (+59 a +508; media
  +324). Tabla completa por día: recalcular de `resultados.csv` (`tag == "arms"`, columnas A, R,
  warehouse).
- `ma` rompe el tope de ±180 porque el asignador cuenta sus órdenes pendientes al tamaño pedido y el
  simulador recorta los retiros. Con tope 0: +4.4% E+F y sigue ganando 15/15.
- **El negativo de Ecobici es solo de la mañana.** La flota en estaciones a las 05:30 es estable
  (ago 7,072, sep 6,924, oct 6,820, nov 6,880; −3 bicis por día en promedio): lo que se saca en la
  mañana regresa fuera de la ventana. −164 de −185 por día vienen de cambios en dañadas (taller).
- La bodega de este run era **ilimitada** (regla del plan 1.4). El usuario señala que no se puede
  meter +500 bicis sin saber si existe ese suministro.

## 5. Parámetros: qué se probó

- **L:** 60 (principal), 45 y 30 para oracle/ma/model con su mejor configuración.
  ma: 18,822 / 16,917 / 14,721; oracle: 13,798 / 13,033 / 11,901.
- **λ:** grid {0, 5, 15, 30, 60, 120, 240} solo con el oracle (bloque 60, H = 2, L = 60); Kneedle →
  60, congelado para todos los brazos. Frágil: casi empate con 30; con λ = 15 el oracle da 7,952 con
  1,102 movimientos (todavía menos que Ecobici). Idea: elegir λ igualando movimientos o bicis movidas
  a Ecobici.
- **Bloques:** 60 vs 30 — 30 no ayuda. **H:** 1 inservible (~52k), 3 el mejor; H > 3 no probado.
- **Tope por hora (512):** se respeta, pero nunca pega (se usan ~90 movimientos por hora).
- **No probado:** frecuencia de re-pronóstico (el plan fijó "una sola vez a las 05:30").

## 6. Lo que el usuario quería medir y no se midió

- **daily** = un pronóstico emitido **una vez** a las 05:30 que cubre **todo el día** (05:30–00:30).
- **Los demás** = pronósticos re-emitidos durante el día. Los hiperparámetros a encontrar son
  **cada cuánto re-pronosticar** ({15 min, 1 h, 3 h}) y **para cuántas horas** ({1, 2, 3 h}).
- Re-pronosticar debe usar lo ya observado ese día (si no, `ma` da lo mismo). Horizonte del
  asignador H = horizonte del pronóstico. Selección fuera de muestra.
- `H` (cuánto mira el asignador) no es re-pronosticar: el asignador corre cada 15 min pero siempre lee
  el mismo pronóstico de las 05:30.

## 7. Decisiones del usuario para el run 2

1. **Todo se extiende a 05:30–00:30** (simulador, medición, brazos, V1, métricas). Snapshots de 24 h.
2. **Bodega de día completo:** medir cuántas bicis entran/salen del sistema en el día; bodega finita
   calibrada con eso, contada sobre lo aplicado, no sobre lo pedido.
3. **Dañadas como evento exógeno** desde GBFS (igual en todos los brazos), y el taller (dañadas que
   Ecobici recoge o regresa) separado del rebalanceo en la medición.
4. **Grid de re-pronóstico:** frecuencia × horizonte, más `daily` de día completo.
5. Pendiente de decidir en planeación: tope de bicis por movimiento (capacidad de camión), λ por
   igualación de movimientos, H > 3, tratamiento de los ±1.

Orden sugerido por el executor: primero lo que hace justa la comparación (dañadas, bodega finita, tope
por movimiento), después la búsqueda de hiperparámetros fuera de muestra, al final el pronóstico.
