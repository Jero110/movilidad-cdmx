# ecosim / simulador: reporte (run 2)

Código: `ecosim/sim.py`. Tests: `tests/ecosim/test_sim.py` (32 tests). Reglas: plan `docs/planner/plans/2026-09-28-ecosim.md`, sección 1.4, con los cambios del plan `docs/planner/plans/2026-09-28-ecosim2.md` (subtask `simulador2`). El reporte del run 1 está en la historia de git (tag `ecosim-run1`). `baseline_15dias.csv` y `replay_15dias.csv` son del run 1 (05:30–12:30) y no se tocaron. Lo del run 2 está en `run2_15dias.csv`.

## Qué cambió respecto al run 1

Es el mismo motor por eventos y viaje por viaje (`sim.simulate`), con una sola función de métricas para todos los brazos. Los cambios:

1. **Ventana 05:30–00:30** (`config.day_bounds`): son 1140 minutos por estación.
   - Los viajes que cruzan medianoche son viajes normales, porque se usan timestamps completos.
   - Un viaje que sale en la ventana y llega a las 00:30 o después cuenta su salida y no su llegada (`extra["n_arrivals_after_end"]`).
   - En `station_hour`, `hour` va de 5 a 24. La 24 es 00:00–00:30 del día siguiente (`contracts.STATION_HOURS`).
2. **La política se llama cada 15 min desde las 05:30 mientras t + L < 00:30.** Con L = 60 son 72 llamadas (la última a las 23:15); con L = 45, 73 (la última a las 23:30). Las horas quedan en `extra["decision_times"]`.
3. **Dañadas dinámicas** (`damage_events`, contrato `DamageEvents`). Se aplican en su `t`, en todos los brazos, antes que cualquier otro evento de ese instante:
   - `daño`: n disponibles pasan a dañadas. Siguen ocupando su anclaje, así que los anclajes libres no cambian;
   - `reparacion`: n dañadas vuelven a estar disponibles;
   - `taller_retiro`: n dañadas salen del sistema y liberan anclaje.

   Se aplica lo posible y el resto se cuenta: `danos_no_aplicables`, `taller_no_aplicable` y, en `extra`, `reparaciones_no_aplicables`. Un evento en una estación desconocida no es aplicable. El log completo está en `extra["damage_applied"]`, y las dañadas por minuto en `extra["disabled_minute"]`.
   - `run_day(..., damage_events="auto")` es el valor por defecto: lee `data/derived/ecosim/damage_events.parquet` y filtra a la ventana. Si el archivo no existe, avisa (`UserWarning`) y simula con dañadas fijas.
   - `damage_events="fixed"` (o `None`) reproduce las dañadas fijas del run 1. También se le puede pasar un DataFrame.
   - La fuente usada queda en `extra["damage_source"]`.
4. **Órdenes por lote.** Todas las órdenes con el mismo `effective_at` forman un lote. A cada orden **de política** se le aplica, en este orden:
   1. **tope por movimiento**: si |delta| > `params.max_bikes_per_move`, se recorta a ese valor (`recorte_por_movimiento`);
   2. **fuera de servicio**: una orden a una estación con `out_of_service` (de `initial_state`) se recorta completa (`extra["recorte_fuera_de_servicio"]`). Es una red de seguridad: el asignador no debería mandarlas;
   3. **recorte físico**: se quita lo que hay y se pone lo que cabe (`clipped`, igual que en el run 1);
   4. **bodega finita sobre lo aplicado**: al cerrar el lote, A − R debe quedar en [−`params.tope_bodega`, +`params.tope_bodega`]. Si queda arriba, se recortan puestas; si queda abajo, retiros. El recorte se reparte entre las estaciones del lado que sobra, en proporción a lo que se les aplicó (`recorte_bodega`), así que no depende de la posición de la orden en el lote (ver supuesto 2). Como la cuenta se hace después del recorte físico, el caso que rompió el run 1 (pedir quitar 5 donde solo hay 2) ya no rompe el tope.

   El **replay de Ecobici** es un dato: solo lleva el recorte físico (3). En `extra["orders_applied"]` cada orden trae su pedido, lo aplicado y los cuatro recortes, que cumplen |pedido| − |aplicado| = suma de recortes (probado).
5. **Replay: `ecobici_orders(day, when="t0"|"t1", variant="rebal"|"stock"|"avail", undo=None|False|True)`.**
   - `rebal` (principal) lee `ecobici_moves.parquet`, columna `delta_rebal`, sin los pares ±1 (`undo=False`). El taller entra como evento `taller_retiro`, no como orden.
   - `stock` lee el mismo archivo con la columna `delta` (el taller va dentro), como el run 1.
   - `avail` lee `ecobici_moves_avail.parquet`, columna `delta`, como el run 1. `medicion2` confirmó que sigue escribiendo ese archivo, porque el principal no trae los intervalos con delta = 0 y delta_avail ≠ 0.
   - `undo=None` (por defecto) quiere decir sin ±1 para `rebal` y con ±1 para `stock`/`avail`, como el run 1. `undo=True` o `undo=False` lo fuerza.
   - **`stock` y `avail` van siempre con dañadas fijas** (`sim.REPLAY_DAMAGE`). Con dañadas dinámicas contarían dos veces lo mismo:
     - `stock` ya trae el taller dentro de su `delta`;
     - `avail` es el delta de *disponibles*, que ya incluye los daños (una disponible que se daña baja las disponibles) y las reparaciones. El 09-03, 2,413 movimientos de `ecobici_moves_avail` caen en la misma estación e intervalo que un evento `daño` (dato de la review).

     La CLI los corre con dañadas fijas y falla con un error explícito si se le pide `--arm ecobici_stock|ecobici_avail --damage auto` (`resolve_damage`). Quien arme esos brazos a mano (`run.py`) debe usar `REPLAY_DAMAGE[variant]` como `damage_events`.
6. **Estado para la política** (`SimState`): `stations` = `STATE_COLUMNS` (`disabled` = dañadas en t, que cambian durante el día; `docks` = cap − disponibles − dañadas − anclajes deshabilitados) **más una columna extra `out_of_service`** (bool). `warehouse` = A − R aplicado hasta t. El contrato no cambió: las columnas extra están permitidas. Se le avisó al asignador.
7. **Conservación** (se verifica al final de cada día):
   - disponibles en estaciones + en tránsito − (A − R) + llegadas a desconocidas − entradas desde desconocidas + (daños − reparaciones) = constante;
   - dañadas: inicial + daños − reparaciones − taller = final.

   El test (a) lo comprueba minuto a minuto, calculado por fuera del motor y con eventos de dañadas.
8. **CLI:** `uv run python -m ecosim.sim --day D --arm baseline|ecobici|ecobici_t1|ecobici_undo|ecobici_stock|ecobici_avail [--damage auto|fixed]`. Sin `--damage`, cada brazo usa el suyo: `auto` para baseline y `rebal`, `fixed` para `stock` y `avail`.

## Supuestos (decididos aquí; revisar)

1. **Orden en un mismo instante:** dañadas → órdenes → decisión de la política → llegadas → salidas → muestra. Entre dañadas del mismo instante: `daño` → `taller_retiro` → `reparacion`, y por estación. Según la descomposición de `medicion`, `daño` no coincide con los otros dos en la misma estación e intervalo; el orden solo decide cuál se queda corto si no alcanzan las dañadas.
2. **La bodega se evalúa al cerrar cada lote**, no orden por orden, porque dentro de un instante no hay viajes. Así, un retiro y una puesta del mismo lote se compensan aunque vengan en cualquier orden. Si hay que recortar lo que sobra (s bicis), se reparte entre las estaciones del lado que se pasa: puestas si se pasa arriba, retiros si se pasa abajo.
   - A cada estación le toca una parte proporcional a lo que se le aplicó en el lote, redondeada por resto mayor (`sim._spread`). Si dos restos empatan, gana la de `short_name` menor; ese desempate mueve a lo más una bici por estación.
   - Así el recorte no depende del orden en que la política emite sus órdenes. Antes caía siempre al final del lote, y el asignador emite por `short_name`, así que lo pagaban las estaciones de `short_name` alto. Hay un test que permuta el lote.
   - Por estación se recorta a lo más lo que se puede deshacer: una puesta recortada no deja bicis negativas y un retiro recortado no se pasa de la capacidad. Con esas guardas siempre alcanza, porque la capacidad de recorte de cada estación es al menos su neto aplicado en el lote. Hay un `assert` que lo comprueba.
   - Si una estación tiene varias órdenes en el lote, su parte se reparte entre ellas con la misma regla. Eso solo cambia la bitácora `orders_applied`, no el estado.
3. **`clipped` sigue siendo solo el recorte físico**, como en el run 1. `recorte_bodega`, `recorte_por_movimiento` y el de fuera de servicio van aparte.
4. **Bodega ilimitada** (sensibilidad del run 1): se pasa un `params.tope_bodega` muy grande. El simulador siempre usa `params.tope_bodega` para las políticas.
5. **Estaciones fuera de servicio** (`out_of_service` de `initial_state`: en blanco a las 05:30 o cap 0):
   - Se simulan como estaciones normales y **`metrics["E"]` y `metrics["F"]` las incluyen** (cifra inclusiva). `extra["E_out_of_service"]` y `extra["F_out_of_service"]` son la parte de E y F que aportan esas estaciones. **El titular es E − `E_out_of_service`** (y lo mismo con F), como en el run 1; lo calcula `run.py:182`.
   - No aceptan órdenes de política, pero sí reciben dañadas y replay.
   - La 698 el 2025-10-25 queda con cap 15 y 0 bicis todo el día: está vacía y aporta 1140 minutos a E.
   - **Pueden tener viajes reales.** El flag sale del snapshot de las 05:30, y una estación en blanco a esa hora puede volver después. El 09-03, la 091 y la 168 están marcadas y tienen de 39 a 60 viajes reales ese día (dato de la review). La política no las puede atender en todo el día y el replay de Ecobici sí (1 orden, 13 bicis). No sesgan el titular, porque salen de él, pero pesan a través de los desvíos hacia las vecinas. `integracion2` debería mencionarlo en CONCLUSIONES.
6. **La hora de los eventos de dañadas** es la que trae el archivo (`t` = t1 corregido, commit − 30 s, según `medicion2`). `damage_events_raw.parquet` (hora cruda) se puede pasar como DataFrame para la sensibilidad.
7. Sin cambios del run 1: desvíos (500 m / la más cercana), estaciones desconocidas, guardas de estación sin coordenadas y de viajes de duración 0, `record_at` (ahora también guarda `disabled_record`).

## Tiempo por día

Todas las corridas de los 15 días de evaluación, cargando datos, tardan de **0.77 a 1.01 s** (media ~0.9 s) en cualquier brazo, con las dañadas dinámicas incluidas. El comando de validación:

```
$ uv run python -m ecosim.sim --day 2025-09-03 --arm baseline   # "seconds": 0.95 (el reloj total depende de la espera en el semáforo)
```

## Pasada de humo en los 15 días de evaluación (`run2_15dias.csv`)

Usa los archivos que `medicion2` escribió el 28-sep a las 21:41. **Son provisionales**: `medicion2` los reescribió a las 21:47, mientras cerraba este reporte (el baseline del 09-03 pasó de 941 a 518 retiros de taller), todavía no tienen review, y la V1 contra GBFS es de `integracion2`. Las cifras de abajo sirven para ver que el motor corre de punta a punta, no como resultado. Medias por día:

| brazo | E | F | E+F | órdenes | A | R | A − R | recorte físico | daños aplic. / no aplic. | taller aplic. / no aplic. |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline (dañadas dinámicas) | 222,948 | 43,923 | 266,871 | 0 | 0 | 0 | 0 | 0 | 2,001 / 718 | 1,141 / 144 |
| baseline, dañadas fijas | 197,935 | 53,903 | 251,837 | 0 | 0 | 0 | 0 | 0 | — | — |
| ecobici (rebal, t0, sin ±1) | 106,308 | 13,690 | 119,998 | 2,426 | 5,950 | 5,068 | +882 | 326 | 2,546 / 173 | 1,260 / 24 |
| ecobici con ±1 | 85,026 | 17,250 | 102,277 | 4,309 | 7,168 | 5,753 | +1,416 | 351 | 2,686 / 34 | 1,282 / 3 |
| ecobici t1 | 113,805 | 14,061 | 127,865 | 2,426 | 5,971 | 5,149 | +822 | 224 | 2,430 / 289 | 1,238 / 46 |
| ecobici stock, dañadas fijas (≈ run 1) | 79,850 | 17,724 | 97,574 | 5,095 | 6,935 | 6,717 | +217 | 387 | — | — |

En las 90 corridas: 0 salidas fallidas, 0 llegadas fallidas y 56,300 viajes servidos por día en todos los brazos. Hay 134 llegadas por día después de las 00:30.

Observaciones (no se arreglan aquí; son para `medicion2` e `integracion2`):
- **Las dañadas dinámicas cambian mucho las cuentas.** En el replay principal hay 2,546 daños, 1,342 reparaciones y 1,260 retiros de taller por día. En el baseline, el 26% de los daños no se puede aplicar (718/día), porque sin rebalanceo las estaciones se vacían antes que en la realidad. En el replay baja a 6.4%.
- ~~**Quitar los pares ±1 no es neutral para la bodega.**~~ **Superada.** Esta observación salía de los archivos de las 21:41: el 09-03, los renglones `undo` sumaban −6 en `delta` y +489 en `delta_rebal`. Con los archivos actuales de `medicion2` (21:47–21:50) suman **−6 en los dos** (verificado). La review mide para el replay del 09-03 A − R = +973 sin ±1 contra +1,017 con ±1. Las filas "con ±1" de la tabla de arriba son de los archivos viejos.
- **El replay `rebal` con dañadas dinámicas da E+F 23% más alto que el `stock` con dañadas fijas del run 1** (120 k contra 98 k). Parte viene del día completo, pero la comparación justa es la V1 de `integracion2`.
