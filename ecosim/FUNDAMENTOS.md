# ecosim / fundamentos: supuestos y hallazgos de auditoría

Plan: `docs/planner/plans/2026-09-28-ecosim.md` (sección 1). Código: `config.py`,
`contracts.py`, `data.py`, `days.py`. Tests: `tests/ecosim/test_fundamentos.py`.

> **Run 2 (plan `docs/planner/plans/2026-09-28-ecosim2.md`):** la ventana pasa a
> 05:30–00:30 y cambian contratos, caché de snapshots y días. Las secciones de
> abajo describen el run 1 (05:30–12:30); lo que cambia está en
> [Run 2](#run-2) al final y manda sobre lo anterior.

> **Run 3 (plan `docs/planner/plans/2026-10-01-ecosim3.md`):** la implementación
> vigente está en [Run 3](#run-3) al final. Las secciones previas quedan como
> registro histórico de los runs 1 y 2.

Cachés (en `data/derived/ecosim/`, fuera de git):
- `trips_2025_01_11.parquet` + `trips_2025_01_11_audit.json`, generados con `uv run python -m ecosim.data build-trips`
- `snapshots/YYYY-MM-DD.parquet`, generados con `uv run python -m ecosim.data build-snapshots`: desde el run 2 hay un archivo por ventana `[d 05:00, d+1 01:00)` local, 121 días (2025-08-01..2025-11-29), 5.73 M renglones. En el run 1 eran 122 días (hasta el 11-30) de 05:00–13:00 y 2.54 M renglones; ese caché está congelado en `data/derived/ecosim_run1/snapshots/`.
- `night_audit.json`, generado con `uv run python -m ecosim.data audit-night` (ver Run 2).

## Código previo: qué se revisó y qué se reutilizó

No se reutilizó código previo. Se revisó:
- `twostep/trips.py` (genera `data/derived/twostep/trips_2025.parquet`). **No sirve tal cual**:
  1. lee `2025-12.csv`, que está sellado (el parquet trae viajes de diciembre);
  2. calcula `o_known/d_known` contra las estaciones de *todo* `daily_snapshots` (2024–2026), no contra las del día;
  3. `day_trips` descarta los viajes de más de 3 h.

  Su limpieza sí resultó correcta en lo que revisa (0 timestamps malos, 0 duraciones negativas, 0 duplicados). Se reescribió en `data.build_trips` leyendo solo ene–nov.
- `scripts/fetch_daily_snapshots.py`: no se usa. Tiene el bug conocido de mezclar la fecha UTC con la hora local. Los snapshots se bajan directo del bucket.

## Viajes (`data.trips`, `data.trips_range`)

Hallazgos sobre los CSV 2025-01..2025-11 (18,689,832 renglones, ver `trips_2025_01_11_audit.json`):
- **Particionado por mes de llegada.** Cada archivo trae los viajes que *llegan* ese mes: el primer renglón de `2025-09.csv` sale el 31/08 a las 23:23 y llega el 01/09. Hay 0 renglones cuyo mes de llegada no coincide con el archivo, así que cada viaje aparece una sola vez y no hay traslape entre archivos.
- Hay 0 timestamps que no parsean, 0 campos vacíos, 0 duraciones negativas, 0 duplicados exactos y 0 duplicados de (bici, t_dep).
- **Zona horaria.** Los CSV vienen en hora local CDMX. Está verificado contra GBFS (ver Snapshots): el stock cuadra con los viajes sin corrimiento.
- **Cruces de medianoche:** 47,960 viajes. Se manejan solos porque se usan timestamps completos (fecha + hora). Hay 7,158 viajes de más de 3 h y 548 de más de 1 día; se marcan con `long` pero **no se filtran**.
- **Estaciones desconocidas:** solo `1000` (547) y `Temporal - 2da Sección Bosque Chapultepec` (772), en todo ene–nov. El ID de la Temporal viene en el CSV con comillas literales; `build_trips` se las quita. En los 15 días de evaluación hay 0 salidas desde una desconocida y de 0 a 8 llegadas por día a una desconocida.
- `o = d` (misma estación): 537,534. Se conservan porque son un desanclaje y un anclaje reales.
- **Comparación contra el CSV crudo.** Se parseó el CSV de forma independiente con pandas para 2025-09-03 (`2025-09.csv`) y 2025-10-22 (`2025-10-1.csv`). Coincide renglón por renglón con `trips(day)` (test `test_trips_match_raw_csv`).

Supuestos:
- `trips(day)` incluye los viajes que salen en [05:30, 12:30) y los que salieron antes y llegan en [05:30, 12:30). No hay límite de duración: una bici que llega dentro de la ventana sí llega. En los 15 días entran de 29 a 77 viajes en curso por día y de 9 a 18 viajes `long`.
- **Estaciones desconocidas** (`o_known`/`d_known` se calculan contra las estaciones GBFS *de ese día*):
  - una salida desde una desconocida se ignora;
  - una llegada a una desconocida saca la bici del sistema y se cuenta.

  Lo implementa el simulador. `trips()` solo marca las estaciones.
- Un viaje que sale el 30-nov dentro de la ventana y llega en diciembre estaría en `2025-12.csv` y se pierde. Es irrelevante porque requiere un viaje de más de 11 h.
- `trips_range(start, end)` filtra por fecha de salida, con fechas inclusivas. No calcula `*_known`. Con fechas a partir de diciembre 2025 lanza error.

## Snapshots GBFS (`data.snapshots`, `data.coverage`)

Hallazgos del bucket `s3://bike-sharing-history/mexico-city/ecobici/{año}/{Mes}.parquet`:
- **No trae `last_reported`.** El único tiempo disponible es `committed_at_utc`, que es la hora del commit del recolector y es igual para las 677 estaciones de cada commit. Por eso `t` = `committed_at_utc` convertido a hora local.
- **Conversión UTC → local.** Se hace con `timezone('America/Mexico_City', ts)` y la sesión de duckdb en UTC. Todas las filas cumplen `t = UTC − 6 h` (test). CDMX no tiene horario de verano desde 2022; verificado día por día, ago–nov es UTC−6.
- **Los archivos están particionados por mes UTC.** 05:00–13:00 local equivale a 11:00–19:00 UTC del mismo día, así que ago–nov local cae entero en `Aug..Nov.parquet`. `Dec.parquet` nunca se lee.
- **El reloj del feed y el de los viajes coinciden.** En cada intervalo entre snapshots de cada estación se compara el cambio de stock (disponibles + dañadas) con llegadas − salidas, corriendo los viajes s minutos. La fracción de intervalos que cuadra exacto:

  | día | −6 h | −15 min | 0 | **+0.5 min** | +15 min | +6 h |
  |---|---|---|---|---|---|---|
  | 2025-09-10 | 0.23 | 0.28 | 0.78 | **0.87** | 0.31 | 0.38 |
  | 2025-10-18 | 0.28 | 0.39 | 0.87 | **0.94** | 0.42 | 0.50 |
  | 2025-11-12 | — | — | 0.76 | **0.85** | — | — |

  El máximo cae en +30–36 s: **el estado del feed es ~30 s anterior a `committed_at`**. Está en `config.GBFS_COMMIT_LAG_S = 30` como dato informativo y **no se aplica**, así que `t` es el commit crudo. Un error de zona horaria de ±1 h o ±6 h se notaría de inmediato (test `test_snapshot_clock_aligns_with_trip_clock`).
- **Cadencia de los commits:** ~15 min en mediana (entre 10 y 24 min), unos 31 commits en 05:00–13:00. Hay 0 duplicados (estación, t).
- **Cobertura** (bloques de 15 min de [05:30, 12:30) con ≥1 commit): va de 0.857 a 0.964 en ago–nov. Nunca llega a 1.0: con una cadencia de ~15 min con jitter siempre queda algún bloque vacío. En sep–nov solo **2025-10-09** (0.857) queda bajo 0.90.
- **Estaciones de prueba en el bucket:** `tsi-*`, `sim-claude-*`, `Station Test`, etc., con coordenadas de Montreal (~3,700 km) o (0,0) (~10,900 km). Aparecen una vez cada una y ninguna cae en las ventanas cacheadas. Por defensa, `snapshots()` y `stations()` solo aceptan `short_name` de la forma `NNN` o `NNN-NNN`. Estas estaciones probablemente son el origen de la "estación a ~9,000 km" que menciona el plan.
- **Reportes en blanco:** son renglones con bikes = disabled = docks = docks_disabled = 0; en 4 estación-días además `cap = 0`. Hay 593 renglones en 24 días. En los casos revisados (091 y 168 el 2025-09-03, 681 el 09-26, 420 el 10-07, 698 el 10-25) **no hay ningún viaje** desde la estación mientras está en blanco, y cuando vuelve a reportar suele estar surtida (p. ej. 23/23 bicis). Se interpreta como estación fuera de línea. `snapshots()` los marca con `blank`, sin filtrarlos.
- **Aviso para `medicion`:** con stock = disponibles + dañadas = 0 durante el blanco, **la reaparición de una estación en blanco se verá como un movimiento de Ecobici** (p. ej. +23 bicis de golpe), y el paso a blanco como un retiro. Hay que decidir explícitamente cómo tratar los intervalos que tocan un `blank`.
- **Columna `status` del bucket:** viene **nula en los 2,539,427 renglones** de ago–nov. Se guarda en el caché (`snapshots/*.parquet`), pero `snapshots()` no la expone porque no aporta nada. El único marcador de fuera de servicio disponible es el derivado `blank`, junto con `is_installed/is_renting/is_returning`.
- Fuera de los reportes en blanco se cumple siempre `bikes + disabled + docks + docks_disabled = cap`.
- `is_renting = False` en algunas estaciones (p. ej. 463, 244, 459 y 402 por largos periodos). Se reporta y no se filtra.

## Estaciones y vecinos (`data.stations`, `data.neighbors`)

- Las estaciones del día son **677 todos los días** de ago–nov. Sus coordenadas y `cap` salen del snapshot más cercano a las 05:30.
- **Regla única de capacidad** (`data._nearest_0530`, compartida por `stations()` e `initial_state()`): se usa la `cap` del snapshot más cercano a las 05:30; si ese snapshot está en blanco, se usa la capacidad máxima reportada ese día. Así, la 091 el 09-03 tiene cap 18 en ambas funciones (antes `stations()` daba 0). Un test exige que coincidan.
- `out_of_service` (en `stations()` e `initial_state()`) = en blanco a las 05:30 o capacidad 0.
- **No hay coordenadas inválidas entre las 677.** La más lejana está a 8.1 km del centro. `coord_invalid` (NaN o a más de 50 km del centro) queda como defensa y está probado con un caso sintético, con (0,0) y Montreal.
- **Dentro de un día no hay pares a distancia 0.** El mínimo es 13.6 m (013–261). `coord_dup` marca pares exactos y se prueba con un caso sintético. 14 estaciones movieron sus coordenadas menos de 60 m durante el periodo; se usa la del día.
- La capacidad cambia en el tiempo en algunas estaciones (p. ej. 146: 23 → 11), así que se usa la del día.
- `neighbors(day)`: todas contra todas entre las válidas, con haversine (R = 6,371 km), ordenadas por (`short_name`, `dist_m`) y con `rank` (1 = la más cercana). En los 15 días de evaluación ninguna estación queda sin vecina a menos de 500 m.

## Estado inicial (`data.initial_state`)

- Toma, por estación, el snapshot más cercano a las 05:30, sea antes o después. `offset_min` es su desfase y `stale` marca los desfases mayores a 10 min. En los 15 días el desfase máximo es de ~5 min y hay 0 `stale`.
- **Se lleva a las 05:30 exactas** (corrección tras la review). El snapshot más cercano suele ser de ~05:34, así que su stock ya incluye los viajes de 05:30–05:34, y `trips(day)` los vuelve a entregar al simulador (doble conteo). Por eso:
  - si el snapshot es posterior a 05:30: `bikes_0530 = bikes_snap + salidas − llegadas`, con los viajes cuya hora cae en [05:30, t_snap];
  - si es anterior: `bikes_0530 = bikes_snap − salidas + llegadas`, con los viajes en (t_snap, 05:30).

  Se usa `t_snap` = commit crudo, sin la corrección de ~30 s. Las dañadas y `docks_disabled` no se tocan, y `docks = cap − bikes − disabled − docks_disabled`. Si el resultado cae fuera de [0, cap − disabled − docks_disabled] se recorta y se marca `roll_clipped`; `df.attrs["n_roll_clipped"]` da el conteo. Los valores crudos quedan en `bikes_snap` y `docks_snap`, y el ajuste pedido en `roll_adj`. En los 15 días:

  | | mín | máx |
  |---|---|---|
  | estaciones ajustadas por día | 17 | 66 |
  | Σ\|ajuste\| en bicis | 17 | 76 |
  | estaciones recortadas | 0 | 3 |

  Todos los recortes son de ±1 bici: estaciones que el snapshot da llenas pero con una salida en [05:30, t_snap], o vacías con una llegada. Salen del desfase de reloj entre los viajes y el feed.
- **Supuesto sobre los reportes en blanco:** si el snapshot más cercano está en blanco, *no* se salta a uno posterior, porque eso metería a las 05:30 bicis que Ecobici puso después. Se imputa como estación vacía: bikes = disabled = 0 y docks = cap = capacidad máxima reportada ese día. Queda marcada `blank = True` y `out_of_service = True`, y no se le aplica el ajuste a 05:30. En los 15 días son **5 estaciones-día**:

  | día | estación | nota |
  |---|---|---|
  | 2025-09-03 | 091 | |
  | 2025-09-03 | 168 | |
  | 2025-09-26 | 681 | |
  | 2025-10-07 | 420 | |
  | 2025-10-25 | 698 | reporta `cap = 0` todo el día, queda con 0 bicis / 0 anclajes |
- Las dañadas (`disabled`) quedan fijas en el valor de este snapshot. Lo aplica el simulador (regla 1.4).

## Contratos (`contracts.py`)

- Columnas y tipos de Trips, Snapshots, InitialState, Forecast, Orders, EcobiciMoves, State y DayResult, con funciones `validate_*` que admiten columnas extra.
- `Order` es un dataclass inmutable con `delta` entero distinto de 0. Acepta `np.int64` y lo convierte a `int`. `validate_orders` también rechaza `delta = 0`.
- `validate_forecast` exige bloques anclados a las 05:30: (block_start − 05:30) debe ser múltiplo de `block_min` y el bloque debe caer dentro de [05:30, 12:30).
- `Policy.decide(t, state: SimState, pending_orders, forecast, params: PolicyParams) -> list[Order]`.
- `PolicyParams` trae los placeholders del plan: `tope_hora = 60` y `tope_bodega = 200`.
- `DayResult` trae `metrics`, con exactamente las llaves de `DAY_METRICS`, incluida `arrivals_unknown` (llegadas a estaciones desconocidas, es decir, bicis que salen del sistema), y `station_hour` (short_name, hour 5..12, E, F). Su validación exige que la tabla estación × hora sume E y F, que rebalanced = min(A, R) y que warehouse = A − R.

## Días (`days.py` → `ecosim/days.json`)

- Candidatos: 2025-09-01..2025-11-30 con cobertura ≥ 0.90. Solo se excluye 2025-10-09.
- Tipo de día: los festivos salen de `holidays.MX` y en ese periodo son solo 16-sep y 17-nov. El 2-nov no es festivo oficial en ese paquete y cae en domingo.
- Muestreo: `np.random.default_rng(20260928)`, 11 de entre semana y 4 de fin de semana/festivo, sin reemplazo. El resultado se ordena por fecha.
- Elegidos: 09-03, 09-15, 09-26, 09-28 (dom), 10-07, 10-11 (sáb), 10-13, 10-15, 10-22, 10-25 (sáb), 11-07, 11-12, 11-15 (sáb), 11-19, 11-24. Ninguno es festivo. La cobertura va de 0.93 a 0.96.

## Run 2

Plan: `docs/planner/plans/2026-09-28-ecosim2.md` (subtask `fundamentos2`).

### Cambios

**Ventana (`config.py`).** El día `d` es la ventana `[d 05:30, d+1 00:30)`, que coincide con el horario de servicio de Ecobici:
- `DAY_END = 00:30` (del día siguiente) y `WINDOW_MIN = 1140` (19 h). `day_bounds(d)` devuelve `(d 05:30, d+1 00:30)`.
- `decision_times(d)` va de 05:30 a 00:15 (76 pasos). Cada caller filtra los que no le sirven (p. ej. t + L ≥ 00:30).
- `SNAPSHOT_WINDOW = (05:00, 01:00)`, `SNAPSHOT_WINDOW_MIN = 1200` y `snapshot_bounds(d) = (d 05:00, d+1 01:00)`.
- **Nuevo `window_day(t)`** = fecha de `t − 5 h`: es la ventana a la que pertenece una hora. **Ojo:** `day_bounds(t)` y `as_date(t)` con t = 00:15 toman la fecha de calendario y caen en la ventana del día siguiente. Para horas de decisión o de evento hay que usar `window_day(t)`.
- **Sitios que usan la fecha de calendario y se rompen después de medianoche** (no se arreglan aquí; los arregla el dueño de cada módulo):
  - `asignador.py:63` (`_day0`): `t.normalize() + 05:30`. Con t = d+1 00:15 da d+1 05:30, el ancla de otro día.
  - `asignador.py:215`: la historia del tope por hora se reinicia cuando cambia `t.date()`, así que se borra a medianoche, a mitad de la ventana.
  - `asignador.py:261`: `C.day_bounds(t)` con t después de medianoche da el fin de la ventana del día siguiente (d+2 00:30), así que no corta las órdenes con t + L ≥ 00:30.
  - `pronostico.py:59`: `day = t.dt.normalize()` asigna al día de calendario los viajes de 00:00–00:30, que pertenecen a la ventana del día anterior.
- Constantes nuevas:
  - `MAX_BIKES_PER_MOVE = 22` y `MAX_BIKES_PER_MOVE_SENS = (14, 22, 42)`;
  - `FORECAST_REFRESH_MIN = (15, 60, 180)` y `FORECAST_BLOCK_MIN = 60`;
  - `SELECTION_SEED = 20260929`, con `N_SEL_WEEKDAY/N_SEL_WEEKEND = 11/4`;
  - `COVERAGE_BLOCK_MIN = 60`;
  - `RUN1_EVAL_DAYS`: los 15 días del run 1, copiados de `days.json` en el tag `ecosim-run1`.
- `EVAL_END` y `SNAPSHOT_END` pasan a **2025-11-29**, porque la ventana del 30-nov termina el 1-dic, que está sellado.
- Se conservan del run 1 `H_GRID_HOURS = [1, 2, 3]`, `BLOCK_GRID_MIN` y `LEAD_GRID_MIN`. El h del run 2 lo sube `integracion2`.

**Datos (`data.py`).**
- **Sello de diciembre por ventana.** `_check_day(d)` revisa la fecha y también el *fin* de sus ventanas (d+1 00:30 y d+1 01:00). Lo usan `trips`, `snapshots`, `_raw_snapshots`, `stations`, `initial_state` y `coverage`. Con eso la ventana del **2025-11-30** lanza "sellado" en todas (test `test_window_of_nov30_is_sealed`). `_check_not_sealed` ahora acepta `date` o `datetime`. `trips_range` sigue filtrando por fecha de salida, como antes.
- **Snapshots de 24 h** (`build_snapshots`). Hay un parquet por ventana `[d 05:00, d+1 01:00)` para d = 2025-08-01..2025-11-29: **121 archivos y 5,730,807 renglones** (~30 MB), que reemplazan el caché de 05:00–13:00.
  - En UTC la ventana es `[d 11:00, d+1 07:00)`. Para cada mes se leen su archivo del bucket y el del mes siguiente (por la ventana del último día), siempre dentro de `Aug..Nov.parquet`.
  - Además se filtra `committed_at_utc < 2025-11-30 07:00 UTC`. `Dec.parquet` nunca se abre.
  - El archivo del 30-nov que dejó el run 1 se borra. Los archivos se escriben vía `.tmp` + rename.
  - El caché del run 1 sigue congelado en `data/derived/ecosim_run1/snapshots/`; no se tocó.
- **Cadencia:** hay 70 commits por ventana en mediana (entre 55 y 73). El primero cae entre 05:05 y 05:07 y el último entre 00:53 y 00:54.
- **Mismo contenido en la mañana.** Se verificó que, para los 15 días de evaluación más 08-15, 10-09 y 11-29, los renglones de 05:00–13:00 del caché nuevo son idénticos a los del caché del run 1.
- **`trips(day)`** devuelve los viajes que salen en `[d 05:30, d+1 00:30)` y los que salieron antes y llegan dentro. Los que cruzan medianoche entran solos, porque se usan timestamps completos. Los que salen dentro y llegan después de 00:30 también se devuelven: el simulador cuenta su salida y no su llegada. En los 15 días de evaluación:

  | | mín | máx |
  |---|---|---|
  | viajes por ventana | 37,046 | 70,151 |
  | salen antes de medianoche y llegan después | 89 | 547 |
  | salen en 00:00–00:30 | 128 | 538 |
  | salen dentro y llegan después de 00:30 | 65 | 368 |
  | `long` (> 3 h) | 18 | 69 |

  El máximo de cada columna es el 2025-10-25 (sábado). Un viaje puede llegar en otro mes y estar en otro CSV: el del 09-03 18:48 llega el 19-nov a la estación `1000`. El test contra el CSV crudo lo toma en cuenta.
- **`initial_state` no cambió de código.** Contra el caché del run 1, `initial_state` y `stations` son idénticos en 14 de los 15 días. La excepción es la **698 el 2025-10-25**:
  - está en blanco con cap 0 a las 05:30 y reaparece a las 16:35 con cap 15;
  - la regla "cap máxima reportada en la ventana" ahora ve la tarde, así que da cap 15 (antes 0) y docks 15;
  - sigue con `blank = out_of_service = True` y 0 bicis.
- **`coverage(day, block_min=60)`** = fracción de los 19 bloques de 60 min en `[05:30, 00:30)` con al menos un commit crudo. Cumplir ≥ 0.90 exige 18 de 19.

**Contratos (`contracts.py`).** Los nombres de abajo son los que usan los workers siguientes.
- `DamageEvents`:
  - columnas `short_name, t, kind, n` y `DAMAGE_KINDS = ("daño", "reparacion", "taller_retiro")`;
  - `validate_damage_events` exige `kind` válido, `n` entero > 0 y que no se repita (short_name, t, kind).
- `EcobiciMoves` agrega:
  - `delta_rebal` (int), `taller_retiro` (int ≥ 0) y `undo` (bool);
  - el validador exige `delta_rebal = delta + taller_retiro` (la definición del plan: delta − (−taller_retiro));
  - **no** exige `taller_retiro ≤ −delta`, porque el caso (d) del plan de `medicion2` (retira 2 dañadas y pone 5 ⇒ taller 2, delta_rebal +5, delta +3) no lo cumple. Ver la nota para medición abajo.
  - `delta_avail` (int) es **opcional** (`ECOBICI_MOVES_OPTIONAL`): si la columna está, se valida que sea entera y sin nulos. La produce `medicion2` y la usa el replay `avail` del simulador.
- `Forecast` agrega `refresh_min` (int ≥ 0; 0 = una sola emisión, `daily`) y `horizon_h` (int ≥ 1).
  - Admite varias emisiones por día. La llave pasa a `FORECAST_KEY = (issued_at, variant, refresh_min, short_name, block_start, block_min)`, así que la misma hora puede aparecer en series de distinta frecuencia.
  - Los bloques van anclados a las 05:30 y deben caer dentro de `[05:30, 00:30)`: el último de 60 min es el de las 23:30; con 30 min se admite el de 00:00.
  - `issued_at` debe caer en `[05:30, 00:30)` de la misma ventana que sus bloques.
  - El día de ventana se calcula como `window_day`.
- `PolicyParams` agrega:
  - `max_bikes_per_move = 22` y `refresh_min = 60`;
  - `__post_init__` exige `H_horas` entero ≥ 1 sin tope, `max_bikes_per_move` entero ≥ 1 y `refresh_min` entero ≥ 0, y convierte `np.int64` a `int`.
- `DayResult`:
  - `DAY_METRICS` agrega `recorte_bodega`, `recorte_por_movimiento`, `danos_aplicados`, `danos_no_aplicables`, `taller_aplicado` y `taller_no_aplicable` (lista `RUN2_METRICS`, enteros ≥ 0);
  - `station_hour.hour` va de **5 a 24** (`STATION_HOURS`): la 5 es 05:30–06:00 y la 24 es 00:00–00:30 del día siguiente, para que ordene cronológicamente.
- `SimState` es el mismo contrato. El docstring aclara que `disabled` son las dañadas en t y que cambian durante el día.

### Auditoría de la noche (`uv run python -m ecosim.data audit-night` → `data/derived/ecosim/night_audit.json`)

Se revisaron los 121 días (ago-01..nov-29), por franja de 30 min. Las fracciones son de renglones estación × commit; "disponibles" y "dañadas" son sumas del sistema por commit. "Cuadre" es la fracción de intervalos estación con Δ(disponibles + dañadas) = llegadas − salidas, con el desfase de 30 s.

| franja | commits/día | días con commit | hueco mediano (min) | huecos > 30 min | no renta | disponibles | dañadas | salidas/día | cuadre |
|---|---|---|---|---|---|---|---|---|---|
| 05:00 | 2.0 | 100% | 16.5 | 0 | 1.3% | 5,575 | 1,386 | 111 | 0.99 |
| 08:00 | 2.0 | 100% | 17.2 | 0 | 0.7% | 4,513 | 1,441 | 1,873 | 0.88 |
| 17:30 | 2.0 | 100% | 12.8 | 1 | 0.2% | 4,232 | 1,411 | 1,969 | 0.87 |
| 18:00 | 0.7 | 74% | 38.8 | 90 | 0.2% | 4,102 | 1,277 | 2,325 | 0.80 |
| 19:00 | 0.1 | 11% | 38.8 | 12 | 0.1% | 5,042 | 1,241 | 1,881 | 0.82 |
| 19:30 | 1.0 | 100% | 42.6 | 121 | 0.2% | 4,533 | 1,283 | 1,515 | 0.82 |
| 20:30 | 0.7 | 64% | 36.6 | 56 | 0.2% | 4,961 | 1,233 | 1,094 | 0.86 |
| 22:00 | 1.9 | 100% | 17.3 | 7 | 0.2% | 5,293 | 1,368 | 738 | 0.94 |
| 23:30 | 2.0 | 100% | 12.4 | 0 | 0.2% | 5,411 | 1,410 | 310 | 0.97 |
| **00:00** | **2.0** | **100%** | **17.5** | **0** | **0.2%** | **5,442** | **1,404** | **223** | **0.96** |
| **00:30** | 2.0 | 100% | 13.7 | 0 | **98.6%** | **90** | **6,835** | **0.7** | 0.98 |

Conclusiones:
- **00:00–00:30 es confiable. La ventana se queda en 05:30–00:30** y no hay que acortarla:
  - hay 2 commits por franja todos los días, ningún hueco > 30 min y el hueco máximo es de 26 min;
  - no renta el 0.18%, como de día;
  - hay 223 salidas por día;
  - el stock cuadra con los viajes en el 95.7% de los intervalos, más que de día (0.86–0.90), porque hay menos movimiento;
  - a las 00:30 el último commit tiene 3.8 min de antigüedad en mediana (4.5 máx) y a las 00:00, 10.8 min (12.4 máx).
- **A las 00:30 el sistema cierra.**
  - El 98.6% de los renglones pasa a `is_renting = False`.
  - Las disponibles caen de ~5,440 a ~90 y las dañadas suben de ~1,400 a ~6,835: el feed reporta las bicis ancladas como dañadas.
  - `is_returning` no cambia (0.17%): se puede devolver.
  - Quedan 0.7 salidas por día.
  - Ejemplo: la 698 el 10-25 tiene 10 disponibles y 3 dañadas a las 23:49, y a las 00:41 tiene 0 disponibles, 13 dañadas y `is_renting = False`.
- **Aviso para `medicion2`:** el intervalo que cruza las 00:30 (último commit antes → primero después) ve un "daño" masivo que es el cierre del sistema, no un daño real. Hay que cortar la medición en el último commit < 00:30, o tratar ese intervalo aparte. Lo mismo, en sentido contrario, en la reapertura de las 05:00: a las 05:00 todavía no renta el 1.3%.
- **El recolector es irregular de 18:00 a 21:30.**
  - Commits cada ~40 min en mediana (máx 57 min), con muchas franjas de 30 min sin commit: la de 19:00 solo tiene commit el 11% de los días.
  - Es el origen de los huecos de ~44 min que menciona el plan. Por eso la cobertura se mide con bloques de 60 min.
  - En esas horas el cuadre baja a 0.80–0.82, porque los intervalos de ~40 min juntan más viajes y más rebalanceo. Es una limitación para la medición de Ecobici en la tarde y no quiere decir que el feed esté mal.
- Reportes en blanco a lo largo del día: 0.01–0.03% de los renglones, sin patrón nocturno.

### Días (`days.py` → `ecosim/days.json`)

- **Candidatos:** 2025-09-01..2025-11-29 (90 días). Cobertura de 60 min: 88 días con 1.0, el 2025-11-18 con 0.947 (sí cumple) y el **2025-09-10 con 0.789**, que es el único excluido: no hay commits de 13:05 a 17:34 (4 h 29 min). El 2025-11-30 se excluye por el sello.
- **Evaluación:** los 15 días del run 1 cumplen (todos con cobertura 1.0). **No hubo reemplazos.** Si alguno no hubiera cumplido, `evaluation_days` lo habría reemplazado, en orden de fecha, por uno del mismo estrato al azar con `default_rng(20260928)` entre los que cumplen y no son del run 1 (probado con datos sintéticos).
  - Entre semana: 09-03, 09-15, 09-26, 10-07, 10-13, 10-15, 10-22, 11-07, 11-12, 11-19, 11-24.
  - Fin de semana: 09-28 (dom), 10-11, 10-25 y 11-15 (sáb).
- **Selección:** `default_rng(20260929)` sobre los que cumplen y no son de evaluación (62 entre semana y 27 de fin de semana o festivo en total, 51 + 23 sin los de evaluación). Se eligen 11 + 4, sin reemplazo.
  - Entre semana: 09-01, 09-08, 09-17, 09-25, 10-09, 10-10, 10-30, 10-31, 11-06, 11-26, 11-28.
  - Fin de semana: 09-13, 09-21, 10-18 (sáb/dom) y 11-01 (sáb).
  - Ninguno es festivo; el 16-sep y el 17-nov quedaron en el pool y no salieron.
  - El **2025-10-09** (excluido en el run 1 por la cobertura de 15 min de la mañana, 0.857) cumple la regla nueva y quedó en selección.
- **Formato de `days.json`:**
  - `evaluacion` y `seleccion`: `[{day, type, coverage}]`, ordenados por fecha;
  - `reemplazos_evaluacion`, `pool`, `all_candidates`, semillas y definición de cobertura;
  - `days` es un **alias de `evaluacion`** para que el código del run 1 que lee `["days"]` siga funcionando.
  - `load_days(kind)` acepta `"evaluacion"` (por defecto), `"seleccion"` o `"todos"`.

### Tests de otros módulos que rompe este cambio (los arregla el dueño de cada módulo)

`uv run pytest tests/ecosim --ignore=tests/ecosim/test_fundamentos.py`: 42 fallos y 18 que pasan. Las causas:
- **`DayResult.metrics` sin las 6 métricas nuevas:** 14 de `test_sim.py` y 2 de `test_run.py`.
- **`Forecast` sin `refresh_min`/`horizon_h`:** 13 de `test_asignador.py` y 2 de `test_pronostico.py`, más `test_oracle_reproduce_fixture`, cuyo fixture de conteos supone la ventana de 7 h.
- **`EcobiciMoves` sin `delta_rebal`/`taller_retiro`/`undo`:** 3 de `test_sim.py` (replay) y 5 de `test_medicion.py` (KeyError al seleccionar columnas).
- **Ventana de 19 h:** `test_medicion.py::test_window_counts` y `test_window_selection_uses_initial_snapshot`.

## Run 3

Plan: `docs/planner/plans/2026-10-01-ecosim3.md`. El día de simulación es
`[d 05:00, d+1 00:30)`, 1,170 minutos y 78 decisiones separadas 15 minutos.
`window_day(t)` asigna las decisiones después de medianoche al día anterior.
La ventana de fotos sigue siendo `[d 05:00, d+1 01:00)`. La cobertura usa
**20 bloques**: 19 de 60 minutos y el último de 00:00–00:30. Un día supera
90% con 18 o más bloques cubiertos.

### Parámetros y contratos

- Una orden emitida en `t` recoge a `t+15` o entrega a `t+60` (sensibilidad de
  entrega: 45, 60, 75 minutos). Las entregas no ocurren en la recogida.
- Valores de referencia para medición: 67 visitas por decisión, 14 bicis por
  visita; sensibilidad p99: 83 visitas y 24 bicis. `medicion3` los recalcula.
  `N_GRID = (1,2,3,4,5,6)`, `LAMBDA_GRID_N = (15,30,60)` y
  `LAMBDA_GRID = (10,15,20,30,45,60)`.
- `FOLDS` contiene `name`, `train_start`, `train_end`, `test_month`: elegir
  (2025-01..07 → agosto), cinco pruebas (ventanas móviles de ocho meses →
  septiembre de 2025 a enero de 2026) y siete cortes de pronóstico (febrero
  a agosto de 2026). Ningún entrenamiento ve el mes de prueba.
- `Order` guarda `issued_at`, `pickup_at`, `delivery_at`, `short_name` y
  `delta`: negativo se aplica al recoger; positivo, al entregar.
  `DamageEvents.kind` solo admite `sube` y `baja`, ambas etiquetas cambian
  bicis **en sitio**, con `n > 0`. `EcobiciMoves.delta` es
  Δ(disponibles + dañadas) − (llegadas − salidas); `par` marca un movimiento
  que se deshace y no cuenta como visita.
- `ForecastTable` es la interfaz `table(t, n_hours)` → arreglo
  `[estación, 4·n_hours, 2]` de salidas y llegadas. Tiene `forma` (`diaria` o
  `directa`) y `modelo`. `PolicyParams` lleva `n_hours`, `lam`,
  `visits_per_decision`, `max_bikes_per_visit`, `pickup_min`, `delivery_min`
  y `retiro`. `DayResult` expone E, F, EF, visitas totales/de recogida/de
  entrega, bicis movidas, máximo en tránsito, recortes por causa, desvíos de
  salida y llegada, km medio de desvío, dañadas no aplicables y tiempos de
  decisión.
- Se quitaron `SEALED_FROM`, `_check_not_sealed`, `_check_day` y todos sus
  usos: diciembre ya no está sellado. También `LEAD_MIN`, `LEAD_GRID_MIN`,
  `BLOCK_MIN`, `BLOCK_GRID_MIN`, `H_GRID_HOURS`, `MAX_BIKES_PER_MOVE`,
  `MAX_BIKES_PER_MOVE_SENS`, `FORECAST_REFRESH_MIN` y
  `FORECAST_BLOCK_MIN`, además de `EVAL_START`, `EVAL_END`, `EVAL_SEED`,
  `N_EVAL_WEEKDAY` y `N_EVAL_WEEKEND` del muestreo anterior. Salieron de `PolicyParams` `tope_hora`,
  `tope_bodega`, `refresh_min`, `mu` y los campos del run 2 que fueron
  reemplazados por la nueva interfaz. La tabla de pronóstico del run 2 y su
  validador dejan paso a `ForecastTable`.

### Viajes y 2024

`uv run python -m ecosim.data build-trips` construye
`data/derived/ecosim/trips_2024_01_2026_08.parquet` desde los CSV mensuales
del [portal de Ecobici](https://ecobici.cdmx.gob.mx/datos-abiertos/). Los 12
CSV de 2024 están en `data/ecobici/`. Se auditó su compatibilidad:

| periodo de llegada | viajes válidos | IDs de estación numéricos |
|---|---:|---:|
| 2024 | 22,242,869 | 677 |
| 2025 | 20,134,900 | 677 |
| 2026, hasta agosto | 11,910,166 | 677 |

Los 677 IDs numéricos de 2024 coinciden con los de 2025. Los ID totales son
680 en 2024 y 679 en 2025 por estaciones temporales; las diferencias son
`1002` y `tag 2` (solo 2024) y `Temporal - 2da Sección Bosque Chapultepec`
(solo 2025). Seis CSV de 2024 llaman `Fecha Arribo` a `Fecha_Arribo`;
`build_trips` normaliza ambos encabezados. La auditoría de los 54,287,936
renglones crudos encontró 0 meses de archivo distintos del mes de llegada,
0 duraciones negativas y 0 duplicados exactos. Un renglón está truncado en
`2026-03.csv` y se descarta: quedan **54,287,935 viajes válidos**.

El archivo de marzo de 2026 termina el **23 de marzo a las 13:07:15**.
Los días desde el 23 no tienen un registro de viajes completo para la
ventana de simulación, aunque los archivos de meses posteriores contienen
unos pocos viajes largos con salida a fines de marzo. `prod_2026` solo
admite marzo 1–22.

### Fotos de las 05:00 y estado inicial

`uv run python -m ecosim.data build-snapshots` guarda las ventanas completas
de 2025 y enero de 2026 en `data/derived/ecosim/snapshots/YYYY-MM-DD.parquet`.
Para febrero–agosto de 2026 guarda la foto inicial y los commits en que
cambia `disabled`, suficientes para el estado inicial y los eventos de
no rentables. El archivo histórico no contiene fotos de **2025-07-17..27**;
no se imputan. Hay 596 archivos de caché hasta el 30 de agosto de 2026.

Al cerrar a las 00:30, el feed cambia masivamente disponibles por dañadas.
La regla del estado inicial es la **primera foto entre 05:00 y antes de 06:00**
en que al menos **95% de las estaciones presentes** tiene `is_renting=True`.
También se exige que la foto tenga al menos 95% del número máximo de
estaciones reportado esa hora, para no interpretar un subconjunto escaso
como una apertura. La foto se lleva a las 05:00 sumando las salidas y
restando las llegadas entre 05:00 y `t_snap − 30 s`. Las dañadas se conservan
en el valor de esa foto. Los recortes físicos se marcan en `roll_clipped`.

Entre enero de 2025 y enero de 2026 hay 396 días calendario: 11 sin foto,
385 con caché, **383 con foto abierta utilizable**. En 376 de esos 383 el
primer commit ya era utilizable; en 7 se eligió uno posterior. Dos días
con foto no permiten reconstruir la apertura:

- **2025-05-24:** entre 05:05 y 06:09 solo 61.6–61.7% de 677 estaciones
  reportan renta; a las 06:26 es 92.8% y a las 06:46, 98.8%. Las
  disponibles saltan de 3,365 a 5,205 mientras desaparecen etiquetas de
  cierre. Retroceder solo los viajes desde las 06:46 inventaría un estado.
- **2025-05-26:** la primera foto es de las 08:38 (3,983 disponibles y
  1,434 dañadas); no hay evidencia del estado de las 05:00.

`initial_state` da un error explícito en ambos días. No están en ningún
conjunto de simulación; mayo de 2025 se usa solo para entrenar con viajes.
Por ejemplo, el 2025-12-10 produce 5,989 disponibles y 927 dañadas a las
05:00, desde la foto de las 05:07.

### Cobertura y días

`uv run python -m ecosim.data build-coverage` calcula la cobertura de los
commits **crudos** (no del caché reducido de 2026) y deja
`data/derived/ecosim/snapshot_coverage_run3.json`. En 2025 y enero de 2026
usa las fotos locales; de febrero a agosto de 2026 consulta los archivos
mensuales del bucket. Los días sin archivo cuentan 0.

| mes | cobertura media | días ≥90% | días con ventana completa |
|---|---:|---:|---:|
| 2025-01 | 0.9968 | 31 | 31 |
| 2025-02 | 1.0000 | 28 | 28 |
| 2025-03 | 1.0000 | 31 | 31 |
| 2025-04 | 1.0000 | 30 | 30 |
| 2025-05 | 0.9952 | 30 | 31 |
| 2025-06 | 1.0000 | 30 | 30 |
| 2025-07 | 0.5790 | 19 | 20 |
| 2025-08 | 1.0000 | 31 | 31 |
| 2025-09 | 0.9950 | 29 | 30 |
| 2025-10 | 1.0000 | 31 | 31 |
| 2025-11 | 1.0000 | 30 | 30 |
| 2025-12 | 1.0000 | 31 | 31 |
| 2026-01 | 0.9968 | 31 | 31 |
| 2026-02 | 0.9536 | 27 | 28 |
| 2026-03 | 0.9565 | 31 | 31 |
| 2026-04 | 0.9433 | 29 | 30 |
| 2026-05 | 0.8790 | 19 | 31 |
| 2026-06 | 0.8100 | 11 | 30 |
| 2026-07 | 0.5839 | 0 | 31 |
| 2026-08 | 0.6774 | 12 | 30 |

`2026/Aug.parquet` existe (494,224 renglones, commits UTC del 1 al 31 de
agosto; último commit **2026-08-31 23:51:29 UTC**, 17:51 local). La ventana
del 31 de agosto necesita `2026/Sep.parquet`, que devuelve **HTTP 404**.
Por eso solo se cachean y consideran completas ventanas hasta el 30 de
agosto. Julio de 2026 tiene 0 días con cobertura ≥90%; agosto sí conserva
12 ventanas candidatas, de las que tres tienen foto inicial ≤10 minutos y
viajes completos.

`uv run python -m ecosim.days` reconstruye `ecosim/days.json`:

- **Selección:** 15 días de agosto de 2025, semilla `20261001`, 11 entre
  semana + 4 de fin de semana/festivo, todos con cobertura ≥90%:
  01, 04, 06, 08, 09, 11, 12, 13, 16, 18, 23, 26, 27, 28, 31.
- **Prueba:** todos los días elegibles de septiembre 2025 (29), octubre
  (31), noviembre (30), diciembre (31) y enero 2026 (31). El 10 de
  septiembre queda fuera por cobertura 0.80.
- **Curva:** 32 días, ocho por mes (6 entre semana + 2 otros), con un solo
  generador `default_rng(20261002)`:

  | mes de 2025 | días del mes |
  |---|---|
  | septiembre | 02, 03, 05, 09, 20, 21, 23, 26 |
  | octubre | 01, 08, 10, 16, 25, 26, 27, 29 |
  | noviembre | 01, 10, 12, 18, 19, 20, 27, 29 |
  | diciembre | 03, 04, 06, 10, 11, 12, 22, 28 |

- **Producción 2026:** hasta ocho días por mes, con cobertura ≥90%, foto
  abierta a ≤10 minutos de las 05:00, ventana de fotos completa y viajes
  del día completos:

  | mes | días del mes |
  |---|---|
  | febrero | 01, 07, 08, 09, 14, 15, 21, 22 |
  | marzo | 01, 07, 16, 17, 21, 22 |
  | abril | 01, 02, 06, 08, 09, 10, 13, 15 |
  | mayo | 02, 03, 09, 12, 16, 31 |
  | junio | 14, 20 |
  | julio | ninguno |
  | agosto | 19, 21, 25 |

Los días de producción de 2026 se usan para contrastar modelo con oráculo,
no para comparar esfuerzo contra Ecobici.
