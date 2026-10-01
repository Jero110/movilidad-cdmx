# Medición de Ecobici, run 2 (día completo 05:30–00:30)

Código: `ecosim/medicion.py`. Datos: `data/derived/ecosim/ecobici_moves.parquet` (556,606
intervalos con delta ≠ 0, hora corregida −30 s), `damage_events.parquet` (494,190
eventos) y las sensibilidades `ecobici_moves_raw.parquet` / `damage_events_raw.parquet` (hora cruda) y
`ecobici_moves_avail.parquet` (solo disponibles). Tablas: `ecobici_stats.json`, `ecobici_por_dia.csv`,
`ecobici_por_hora.csv`, `ecobici_ventanas.csv`, `ecobici_observado.csv` (E/F GBFS de los 30 días).

**Regla.** delta = stock(t1) − stock(t0) − llegadas + salidas, stock = disponibles + dañadas, sin umbral
(plan run 1, 1.3). Entran intervalos desde el snapshot inicial (el más cercano a 05:30) hasta el último
commit antes de las 00:30: el intervalo que cruza las 00:30 es el **cierre del sistema** (las ancladas pasan
a "dañadas") y no genera nada. **Principal = rebalanceo sin taller (`delta_rebal`) y sin pares ±1.**

**Regla de taller (decisión del executor: manda el caso (d) del plan).** Con Δdañadas = dañadas(t1) − dañadas(t0):
daño = Δdañadas si > 0. Lo que baja de dañadas se reparte: si delta > 0, todo es `taller_retiro` (se llevan
dañadas y ponen bicis); si delta < 0, taller = min(−Δdañadas, −delta) y el resto es reparación; si delta = 0,
todo es reparación en sitio. delta_rebal = delta + taller_retiro. Así el simulador reconstruye exacto
disponibles y dañadas de cada snapshot (test `test_decomposition_reconstructs_state`).
**Ambigüedad declarada:** con delta > 0 y dañadas que bajan, "reparación en sitio + poner" y "taller + poner"
dan el mismo snapshot; la regla elige taller. Y "daño" incluye bicis que llegan ya dañadas (para el
simulador es lo mismo: llega y se daña).

## Números por día (media)

| | todos (120 días) | evaluación (15) | selección (15) | todos, hora cruda |
|---|---|---|---|---|
| movimientos (principal) | 2,245.6 | 2,450.7 | 2,342.0 | 3,396.7 |
| estaciones tocadas | 556.5 | 570.7 | 559.9 | 588.3 |
| A (bicis metidas) | 5,673.7 | 6,033.3 | 5,945.9 | 6,632.3 |
| R (bicis sacadas) | 5,042.7 | 5,336.5 | 5,316.3 | 5,894.9 |
| min(A, R) | 5,042.7 | 5,336.5 | 5,316.3 | 5,894.9 |
| bodega A − R | +631.0 | +696.8 | +629.6 | +737.4 |
| \|A − R\| | 631.0 | 696.8 | 629.6 | 737.4 |
| máx \|bodega acumulada\| en el día | 676.7 | 730.4 | 662.5 | 784.3 |
| bicis de taller retiradas | 656.7 | 717.4 | 670.2 | 763.7 |
| daños | 2,562.2 | 2,719.2 | 2,584.7 | 2,562.2 |
| reparaciones | 1,864.6 | 1,936.2 | 1,898.2 | 1,757.6 |
| movimientos con ±1 (sensibilidad) | 4,403.7 | 4,832.7 | 4,568.5 | 8,008.3 |
| A − R con ±1 | +631.0 | +696.8 | +629.6 | +737.4 |
| pares ±1 | 1,079.0 | 1,191.0 | 1,113.3 | 2,305.8 |
| intervalos delta ≠ 0 (def. run 1) | 4,638.4 | 5,095.2 | 4,814.3 | 8,250.0 |
| A − R con taller (def. run 1) | -25.6 | -20.6 | -40.6 | -26.2 |

**Tope por hora** (movimientos principales con t0 en ventanas de 60 min que empiezan cada 15 min, 05:30 …
23:30; 8,760 pares día × ventana): **p95 = 246**, mediana 113, máximo
1165. Con ±1: p95 = 474. Definición del run 1 (todo delta ≠ 0): 492.
Hora cruda: 353.

**Tope de bodega** = promedio diario de |A − R| del rebalanceo principal, redondeado hacia arriba:
**632** (631.0). Con ±1: 632. Leyendo la rama ambigua del
taller como reparación: 426 (ver Limitaciones). Máximo de la bodega
acumulada dentro del día: media 676.7, p95 1097,
máximo 1374; el acumulado pasa el tope en 73 de 120 días.
La bodega del rebalanceo es positiva en 120 de 120 días: **el tope de bodega del run 2 es
3.5 veces el del run 1 porque mide sobre todo el regreso de reparadas** (ver Descomposición). Ambos topes usan todos los días medidos, como el run 1 (son capacidades de Ecobici, no
parámetros de política); las columnas de evaluación y selección están arriba.

**Bicis por movimiento** (|delta_rebal|, principal): p50 3, p90 10, p95 14,
p99 24.0, máx 178 (n = 269,474). Con ±1: p50 1, p95 10, p99 20.0.
El máximo es el hub 271-272 2025-10-07 18:49:47 → 2025-10-07 19:33:45 (delta -178, 201 llegadas): un intervalo nocturno de ~40 min en el que Ecobici vacía
el hub varias veces; se ve como un solo movimiento.

## Descomposición taller / daño / rebalanceo

- **Mañana (05:30–12:30, la ventana del run 1):** con la definición del run 1 (taller dentro) Ecobici saca más de lo
  que mete, A − R = -182.2. El taller retira 283.7 dañadas en la mañana,
  156% de ese A − R negativo: **el taller explica todo el A − R negativo del run 1**. Sin taller y sin ±1
  el rebalanceo de la mañana queda en A − R = +96.1.
- **Día completo:** con taller dentro, A − R = -25.6: en el día Ecobici mete casi lo mismo que saca. El
  taller retira 656.7 dañadas por día y regresan ~631 bicis netas que no se distinguen de
  un "poner" de rebalanceo (reparadas que vuelven del taller, o bicis de bodega). Por eso el rebalanceo sin taller
  queda en A − R = +631.0 y el tope de bodega sale en 632. La alternativa sin separar el taller
  (|A − R| con taller dentro, como el run 1) da 74.0 en el día completo.
- Daños (2562.2/día) y reparaciones (1864.6/día) no son movimientos: son eventos exógenos
  (`damage_events.parquet`) que el simulador aplica en todos los brazos.

## Pares ±1 y taller (por qué los ±1 se detectan sobre delta y se descomponen como delta = 0)

| 15 días de evaluación, media por día | undo_sum_delta | undo_sum_delta_rebal | moves | moves_1_bici | A | R | wh | wh_con_undo | taller | p95_ventana |
|---|---|---|---|---|---|---|---|---|---|---|
| alternativa: ±1 sobre delta_rebal | -24.1 | 0.0 | 2,970.1 | 1,301.8 | 6,598.4 | 5,334.5 | 1,263.9 | 1,263.9 | 1,284.5 | 269.6 |
| usada: ±1 sobre delta, par = delta 0 | 0.0 | 0.0 | 2,450.7 | 811.1 | 6,033.3 | 5,336.5 | 696.8 | 696.8 | 717.4 | 231.2 |

Las dos reglas cuadran la bodega del replay (A − R principal ≈ A − R con ±1). La alternativa ("un −1 que
es taller no forma par") convierte cada par "−1 con una dañada menos, luego +1" en *taller retira 1 dañada y
~15 min después Ecobici pone 1 bici*. Son cientos por día de visitas de camión de 1 bici, lo que no es
plausible. La explicación que cuadra con los viajes es otra: una dañada se habilita y se renta, y la salida
cae en el intervalo siguiente al snapshot que ya no la ve. Por eso se usa la primera regla.

## Comparación contra la mañana del run 1

La mañana medida ahora con la regla del run 1 reproduce el `ecobici_por_dia.csv` del run 1 en los
110 días comunes: diferencia máxima 0 movimientos, 0 en A,
0 en R (los snapshots de la mañana son idénticos; ver FUNDAMENTOS).

| 15 días de evaluación, media por día | run 1 (05:30–12:30) | run 2, misma mañana, def. run 1 | run 2 mañana, principal | run 2 día completo, principal |
|---|---|---|---|---|
| movimientos | 2,075.5 | 2,075.5 | 1,073.9 | 2,450.7 |
| A | 3,000.7 | 3,000.7 | 2,654.1 | 6,033.3 |
| R | 3,185.9 | 3,185.9 | 2,543.3 | 5,336.5 |
| A − R | -185.2 | -185.2 | +110.8 | +696.8 |

Topes del run 1 (mañana, con ±1 y taller): tope por hora 512, bodega 180.8.
Run 2 (día completo, principal): 246 y 632.

## Por bloque de 60 min (principal, media por día)

| bloque | movimientos | A | R | A − R |
|---|---|---|---|---|
| 05:30 | 65.3 | 84.2 | 168.0 | -83.8 |
| 06:30 | 85.3 | 171.6 | 211.8 | -40.2 |
| 07:30 | 146.8 | 303.7 | 363.0 | -59.2 |
| 08:30 | 174.4 | 454.4 | 536.0 | -81.7 |
| 09:30 | 201.6 | 596.4 | 500.2 | +96.2 |
| 10:30 | 168.9 | 489.4 | 332.1 | +157.3 |
| 11:30 | 153.4 | 420.1 | 311.8 | +108.3 |
| 12:30 | 110.8 | 256.4 | 178.2 | +78.2 |
| 13:30 | 127.8 | 257.2 | 142.5 | +114.7 |
| 14:30 | 113.7 | 158.6 | 172.4 | -13.8 |
| 15:30 | 114.0 | 214.6 | 151.1 | +63.5 |
| 16:30 | 149.1 | 270.1 | 424.7 | -154.5 |
| 17:30 | 180.9 | 602.3 | 478.2 | +124.1 |
| 18:30 | 98.8 | 330.6 | 330.7 | -0.2 |
| 19:30 | 145.2 | 527.3 | 471.0 | +56.3 |
| 20:30 | 101.9 | 350.5 | 114.3 | +236.2 |
| 21:30 | 45.2 | 90.8 | 23.4 | +67.4 |
| 22:30 | 20.7 | 10.8 | 28.6 | -17.7 |
| 23:30 | 41.2 | 84.6 | 103.8 | -19.2 |

## E y F observados en GBFS (15 días de evaluación, minutos-estación, 05:30–00:30)

E medio = 99,087 (mañana 34,880, tarde 28,117, noche 36,090);
F medio = 17,797 (mañana 6,454, tarde 4,092, noche 7,251).
Escalón entre snapshots con hora corregida; E = 0 disponibles; minutos en blanco aparte (`blank_min`).

## Limitaciones

- **Reparadas que regresan del taller** no se distinguen de un "poner" de rebalanceo: quedan en delta_rebal.
  Por eso A − R del rebalanceo principal queda sesgado hacia arriba y el tope de bodega es grande.
- **Desfase de timestamps.** El feed no trae `last_reported`; se corrige con −30 s (mínimo del barrido:
  2025-09-03: −32 s, 2025-10-15: −32 s, 2025-11-24: −28 s). Aun así 46.5% de los intervalos con delta ≠ 0 son pares ±1 (ruido); se excluyen de lo
  principal y se descomponen como delta = 0: 60,493 de esos renglones son −1 con una dañada
  menos (una dañada que se habilita y se renta, con la salida registrada en el intervalo siguiente). Con la regla
  de taller normal serían taller y el simulador perdería esas bicis; así quedan como reparación.
- **Dos toques en un intervalo son invisibles:** solo se ve el neto por estación e intervalo. De 18:00 a
  21:30 los intervalos son de ~40 min (recolector irregular), así que ahí se juntan más toques y más viajes.
- **Ambigüedad taller/reparación** con delta > 0 (arriba). Esa rama aporta 205.4 de las
  656.7 bicis de taller por día (31%). Si se leyera como "reparación en sitio + poner", la bodega
  principal bajaría de +631.0 a +425.6 por día y el **tope de bodega sería
  426** en vez de 632 (`tope_bodega_rama_delta_pos_reparacion`). Una bici que llega dañada cuenta como daño.
- **Reportes en blanco:** 1,026 intervalos excluidos, 47 con delta ≠ 0.
- **Atípico del 2025-08-14 por la tarde:** desde las 16:49 ~280 estaciones por intervalo tienen delta ≠ 0 (el feed y
  los viajes dejan de cuadrar). Ahí está el máximo del tope por hora (1165, ventana 2025-08-14 17:00:00). No es
  día de evaluación ni de selección y no mueve el p95 (p99 de las ventanas = 312).
- **Viajes** de más de 1 día que llegan en la ventana se pierden (548 en todo ene–nov).
