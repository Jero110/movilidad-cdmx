# App Ecobici · rebalanceo (ecosim run 3)

App local con tres pestañas: **Ahora**, **Replay** y **Predicción**.

```bash
cd scripts/ecobici_mapa
uv run python3 server.py --no-browser          # http://localhost:8000
uv run python3 server.py --no-browser --port 8765
uv run python3 server.py --no-browser --no-live # sin bitácora del feed
```

Opciones: `--port`, `--host`, `--no-browser`, `--no-live`, `--results-dir`.

Preparar los datos (desde la raíz del repo):

```bash
uv run python -m ecosim.replay --force   # precálculo del Replay (16 días × 7 brazos, ~20 min con 4 procesos)
uv run python -m ecosim.actualizar       # cada mes: baja el mes nuevo y reentrena los modelos de producción
```

## Qué es medido y qué es estimado

| Dato | Origen | Tipo |
|---|---|---|
| Ahora: bicis, anclajes, `raw` | feed oficial de Ecobici (GBFS), tal cual | medido |
| Replay: viajes (salida, llegada, estación) | CSV de datos abiertos de Ecobici | medido |
| Replay: estado a las 05:00 | foto del feed más cercana, llevada a 05:00 con los viajes | medido + ajuste documentado en `ecosim/data.py` |
| Replay: movimientos de Ecobici (brazo `ecobici`) | cambios entre fotos del feed menos viajes (`ecosim/medicion.py`) | inferido |
| Replay: órdenes de los brazos de política, desvíos, E y F | simulador `ecosim/sim.py` | simulado |
| Predicción: pronósticos | modelos de producción (`ecosim/actualizar.py`) | estimado |
| Predicción: viajes del día (forma directa y `salidas_est`/`llegadas_est`) | cambios del feed entre lecturas (`live.infer_flows`) | estimado (`"viajes_del_dia": "inferidos_feed"`) |
| Predicción: órdenes de asignación | `Asignador` sobre el estado del feed | recomendación; no se ejecuta |

En vivo no se calcula E+F ni ahorro: Ecobici sigue operando y no hay contra
qué comparar.

## Contratos JSON

### `GET /api/snapshot` (Ahora)

`{"generated_at", "last_updated", "stale", "stations": [...], "alerts": [...]}`.
Cada estación: `id, name, short_name, lat, lon, capacity, bikes, docks,
bikes_disabled, docks_disabled, renting, returning, installed, last_reported,
alerted` y **`raw`**: el objeto completo de `station_information` fusionado
con el de `station_status`, con todas las llaves que mande la API
(`station_id` es igual en ambos). Las bicis disponibles para rentar son
`num_bikes_available` (`bikes`); las no rentables (`bikes_disabled`) no se suman.

### `GET /api/zonas` (coropleta)

GeoJSON `FeatureCollection` (EPSG:4326) de las **AGEB urbanas del INEGI** que
tienen al menos una estación. Cada feature: geometría `Polygon` o
`MultiPolygon` y `properties = {"cvegeo", "alcaldia", "estaciones": [short_name, ...]}`.
El servidor reparte las estaciones del snapshot vigente entre esas AGEB (la que
la contiene; si una estación nueva cae fuera de todas, la más cercana, anotada
en `metadata.cercanas_snapshot`), así que cada estación del snapshot aparece en
exactamente una zona. `metadata` trae la fuente.

- **Fuente:** Marco Geoestadístico 2024 del INEGI (corte de actualización
  cartográfica agosto 2024; archivos con fecha 26-nov-2024), entidad 09 Ciudad
  de México, capa `09a` (AGEB urbanas) y `09mun` (nombre de la alcaldía):
  <https://www.inegi.org.mx/contenidos/productos/prod_serv/contenidos/espanol/bvinegi/productos/geografia/marcogeo/794551132173/09_ciudaddemexico.zip>
  (descargado el 2026-10-05; no encontramos una edición 2025 publicada).
- **Cómo se genera:** `uv run python scripts/ecobici_mapa/zonas_ageb.py` (o
  `--shp ruta/09_ciudaddemexico.zip` si la descarga falla) → `zonas_ageb.geojson`
  (se commitea). Reproyecta de Cónica Conforme de Lambert (ITRF2008) a WGS84,
  asigna cada estación de `station_information` a su AGEB (o a la más cercana,
  anotado en `metadata.cercanas`; el 2026-10-05 fueron 0 de 677), se queda con las
  AGEB con estaciones (243) y simplifica 4 m sin cambiar la topología de cada
  polígono (0.11 MB).

### `GET /api/replay/index`

Días y brazos precalculados, con `final`, `check` (incluye `igual` contra
`resultados.csv` y `cuadre_todas_las_fotos`), `spec`, `cuadre` y `cum` por brazo.

### `GET /api/replay/{día}/dia`

Lo común a todos los brazos: `day, start, step_min, n_frames, times,
stations{short_name,name,lat,lon,cap,docks_disabled,out_of_service},
trips{id,o,d,dep,arr}, ecobici_moves`.

- **79 fotos**: 05:00, 05:15, …, 00:15 y la foto de **cierre 00:30** (estado
  final, después de todos los eventos del día). `times[k]` es la hora de la foto `k`.
- `trips.id`: índice estable del viaje en `ecosim.data.trips(día)` (el mismo
  que usan `desvios`). `dep`/`arr` en minutos desde las 05:00.

### `GET /api/replay/{día}/{brazo}`

`bikes[k][i]`, `dis[k][i]` (no rentables), `cum{E,F,moves,bikes_moved}`,
`applied[k]`, `decision[k]`, `final`, `check`, `spec`, y además:

- `snap[k]`: lo ocurrido en la foto `k`, es decir en el intervalo
  **(t<sub>k−1</sub>, t<sub>k</sub>]** (eventos justo en `t_k` incluidos: ahí se emiten
  órdenes y se aplican recogidas y entregas). En `k = 0` solo cuenta lo que pasa
  a las 05:00 exactas y el “anterior” es `inicial`.
  - Contrato: `min_desde_anterior, salidas, llegadas, desvios_salida,
    desvios_llegada, emitidas, recogidas, entregadas, a_rentable,
    a_no_rentable, E, F, EF, EF_acum, cuadre_ok, descuadre_estaciones,
    bicis_sistema`.
  - Agregados: `bicis_emitidas` (bicis pedidas por las órdenes emitidas),
    `devueltas`, `bicis_no_aplicadas`, `etiquetas_no_aplicadas`,
    `cuadre_sistema_ok`, `disponibles`, `no_rentables`, `en_camioneta`,
    `en_viaje`, `entran_externas`, `salen_externas`, `externo_ecobici`.
  - `salidas`/`llegadas` son las **reales** del simulador (después de desvíos).
    `emitidas` cuenta visitas (órdenes) emitidas en `t_k`; `recogidas` y
    `entregadas` son bicis efectivamente movidas.
  - `E`/`F`: minutos-estación vacía / llena del intervalo
    [t<sub>k−1</sub>, t<sub>k</sub>) con la misma serie minuto a minuto que da `cum` y
    `resultados.csv`; `EF_acum[último] = final.EF`.
- `est[k]`: solo estaciones con algún cambio,
  `[i, entregadas, recogidas, a_rentable, a_no_rentable, salidas, llegadas, devueltas]`
  (columnas en `est_cols`; `i` = índice en `stations`).
- `desvios[k]`: `[trip_id, "salida"|"llegada", i_original, i_real, metros]`.
- `inicial`: `{bikes, dis, en_viaje, bicis_sistema}` a las 05:00, para la cuenta de `k = 0`.
- `final` agrega `recortes`, `danadas_no_aplicables`, `km_desvio_medio`,
  `bicis_sistema_inicio` y `bicis_sistema_cierre`.

**Cuadre** (lo calcula `ecosim/replay.py` y lo vuelve a hacer
`tests/ecosim/test_replay.py` en todas las fotos de todos los archivos):

```
por estación:  bikes[k] = bikes[k−1] − salidas + llegadas + entregadas − recogidas + devueltas + a_rentable − a_no_rentable
               dis[k]   = dis[k−1]   + a_no_rentable − a_rentable
sistema:       bicis_sistema = disponibles + no_rentables + en_camioneta + en_viaje
               bicis_sistema[k] = bicis_sistema[k−1] + entran_externas − salen_externas + externo_ecobici
```

`cuadre_ok` exige ambas; `descuadre_estaciones` cuenta las estaciones que no cuadran.
Eventos del simulador que no estaban en el contrato y se agregan explícitos:

- **`devueltas`** (regla de `sim.py`): si al entregar no hay anclajes o la carga
  no alcanza, el remanente de la camioneta vuelve a la estación con anclaje más
  cercana al origen de la recogida, en la hora de entrega. Son bicis que entran a
  esa estación sin ser una entrega pedida.
- **`entran_externas` / `salen_externas`**: viajes que salen de una estación que
  no está en el feed de ese día (la bici aparece al llegar) o llegan a una que no
  está (la bici sale del sistema). Por estación ya están dentro de `llegadas`.
- **`externo_ecobici`**: en el brazo `ecobici` las órdenes son los movimientos
  medidos del feed, que no tienen camioneta ni neto cero: su neto cambia las
  bicis del sistema.
- `bicis_no_aplicadas` (bicis pedidas y no movidas: tope por visita, sin bicis o
  sin anclajes, estación fuera de servicio) y `etiquetas_no_aplicadas` (cambios
  rentable ↔ no rentable observados que no se pudieron aplicar por falta de
  bicis) no cambian el inventario; se publican para que nada quede oculto.

Un cambio no rentable ↔ rentable (`a_rentable`, `a_no_rentable`) es la misma bici
en la misma estación que cambia de etiqueta; no es rebalanceo.

### Predicción (`/api/live/…`, solo en vivo)

Sin parámetro de fecha ni modo histórico: todo usa el feed de este momento.

**Watcher del feed.** Desde que arranca (salvo `--no-live`), el servidor consulta
el feed cada `POLL_S` = 15 s y registra una **lectura** solo cuando el feed
cambió (otra `last_updated` de `station_status` o distinto contenido). Cada
lectura va a la bitácora del día, `data/derived/ecosim/live/gbfs/{día}.jsonl`
(foto completa) y `{día}_lecturas.jsonl` (resumen), y se emite como evento
`feed`. Lecturas iguales no generan nada.

`lectura = {"t_feed", "t_detectado", "primera", "estaciones_cambiaron", "salidas_est", "llegadas_est", "saltos_camioneta", "bicis_disponibles", "no_rentables", "anclajes_libres", "estaciones", "estaciones_vacias", "estaciones_llenas"}`

- `t_feed`: hora del feed (`last_updated`). `t_detectado`: cuándo el watcher vio
  el cambio. Ambas en hora local con segundos.
- `salidas_est`/`llegadas_est`: estimadas contra la lectura anterior con la regla
  de `infer_flows` (cambio de disponibles + no rentables, saltos de 5 o más =
  camioneta, contados en `saltos_camioneta`). En la primera lectura van `null`.
- `estaciones_vacias`/`estaciones_llenas`: estaciones en servicio con 0 bicis
  disponibles / 0 anclajes libres.

- `GET /api/live/feed` →
  `{"ultima_lectura": lectura, "ultimo_cambio", "ultima_consulta", "lecturas_hoy", "desde", "poll_s", "error", "lecturas": [últimas 20 lecturas]}`.
- `GET /api/live/eventos`: **Server-Sent Events** (`text/event-stream`). Al conectarse
  manda la última lectura y después cada evento con `id:`, `event:` y `data:` (JSON
  con `id` consecutivo y `hora`). Manda un comentario `: latido` cada 15 s.
  - `event: feed`: una lectura nueva (`data` = `{"id", "evento": "feed", "hora", …lectura}`).
  - `event: paso`: una sesión registró un paso:
    `{"id", "evento": "paso", "hora", "sesion", "n", "t", "t_feed", "disparo", "espera_s", "feed_atrasado", "visitas", "bicis_a_mover", "aplicadas", "estado_feed"}`.
  - `event: sesion`: una sesión arrancó o cambió de estado o de fase:
    `{"id", "evento": "sesion", "hora", "sesion", "estado", "fase", "siguiente_paso", "pasos", "error"}`.
  - En el navegador: `new EventSource("/api/live/eventos")` con
    `addEventListener("feed" | "paso" | "sesion", …)`. `GET /api/live/assign/{id}`
    queda como respaldo.

- `GET /api/live/models` →
  `{"datos": {"ultimo_dia_publicado", "actualizado", "dia"}, "modelos": [{"key", "forma", "label", "disponible", "motivo", "corte": {"train_start", "train_end"}, "n", "lambda", "nota", "rezagos_hoy", "horizontes"}]}`.
  Modelos: `ma_diaria`, `lgbm_diario`, `lgbm_directo` (los oráculos no se
  ofrecen en vivo). Cada uno se prueba una vez al día; si falla, queda
  `disponible: false` con la causa en `motivo`. `horizontes` es la lista de
  valores válidos de `horizonte` para ese modelo: `["dia"]` en la forma diaria,
  `[1, 2, 3, 4]` en la directa.
- `POST /api/live/forecast?model=&horizonte=` → (forma diaria — `ma_diaria`,
  `lgbm_diario` — solo `horizonte=dia`; forma directa — `lgbm_directo` — solo
  `horizonte=1|2|3|4`; cualquier otra combinación, y cualquier otro parámetro
  como `at`, responde **400** con el motivo)
  `{"id", "issued_at", "t", "t_feed", "model", "forma", "horizonte", "referencia": {"rezagos": "reales"|"dia_referencia", "dia_referencia", "faltan", "feed_hoy": {"desde", "fotos", "completo", "hueco_max_min", "nota"?}|null, "viajes_del_dia": "inferidos_feed"|null, "inferencia", "estaciones_sin_pronostico"}, "minutes": [15, 30, …], "stations": {"short_name", "name", "lat", "lon", "cap"}, "bikes_now": [...], "proyeccion": [[...] por paso], "salidas": [[...] por paso], "llegadas": [[...] por paso], "riesgos": [{"short_name", "tipo": "vacia"|"llena", "minutos"}], "notas", "estimado": true}`.
  - `t` = cuarto de hora en curso del feed; `proyeccion[j][i]` es la estación `i`
    a `t + minutes[j]`; `salidas`/`llegadas` tienen la misma forma (esperadas en ese cuarto).
  - `dia` llega hasta las 00:30 (la forma diaria es el pronóstico del día
    completo); la directa, de 1 a 4 h. Si el horizonte pasa de las 00:30, se corta
    ahí y lo dice en `notas`.
  - Fuera de 05:00–00:30 responde 409.
  - Fechas y horas: hora local sin zona; `issued_at` y `t_feed` con segundos.
- `POST /api/live/assign/start?forecast_id=&horas=` → `{"session_id"}`. La
  decisión sigue siendo cada 15 min (recoge a t+15, entrega a t+60, topes por
  decisión), pero cada paso lo **dispara el feed**:
  - el primer paso corre de inmediato con el feed actual (`t` = hora actual
    redondeada a 15 min; `disparo: "inicio"`);
  - en cada marca `t` siguiente, el paso espera la **primera lectura del feed con
    `t_feed ≥ t`** (`disparo: "cambio_feed"`, `espera_s` = segundos de `t` a que se
    detectó);
  - si en `ESPERA_MAX_S` = 300 s no llega, corre con la última lectura
    (`disparo: "sin_cambio_feed"`, `feed_atrasado: true`). Si el feed quedó más de
    15 min atrás de `t`, ese paso no emite órdenes y lo dice en `nota`.
- `GET /api/live/assign/{id}` →
  `{"estado": "corriendo"|"terminada"|"detenida"|"error", "fase": "emitiendo"|"cerrando", "model", "inicio", "horas", "fin_emision", "params", "inicio_estado": estado, "pasos": [{"t", "t_feed", "disparo": "inicio"|"cambio_feed"|"sin_cambio_feed", "espera_s", "feed_atrasado", "estado_feed": estado, "lecturas_desde_paso": [lectura], "emitidas": [orden], "aplicadas": [orden], "bicis_a_mover", "visitas", "salidas_est", "llegadas_est", "ventana_est", "saltos_camioneta", "nota"}], "totales": {"bicis_a_mover", "visitas", "recogidas", "entregadas"}, "pendientes": [orden], "siguiente_paso", "error", "nota"}`,
  con `orden = {"short_name", "accion": "recoger"|"entregar", "n", "emitida", "recoge", "entrega"}`
  y `estado = {"t_feed", "bicis_disponibles", "no_rentables", "anclajes_libres", "estaciones_vacias", "estaciones_llenas"}`.
  - `inicio_estado`: el feed con que arrancó la sesión; `estado_feed`: el del paso;
    `lecturas_desde_paso`: las lecturas del feed entre el paso anterior y este.
  - Durante `horas` emite órdenes (fase `emitiendo`); después sigue cada 15 min
    sin emitir hasta que se aplica la última entrega pendiente (fase `cerrando`)
    y termina con recogidas = entregadas.
  - `aplicadas` = órdenes cuya recogida (`recoge`) o entrega (`entrega`) toca en ese paso.
  - `salidas_est`/`llegadas_est`: viajes estimados del feed en `ventana_est` (los 15 min previos).
- `POST /api/live/assign/{id}/stop` detiene la sesión antes del siguiente paso.
- `GET /api/live/assign` lista las sesiones guardadas (para retomar la activa al recargar).

Las sesiones se guardan en `data/derived/ecosim/live/asignaciones/{id}.json`
y los pronósticos en `data/derived/ecosim/live/pronosticos/{id}.json`; recargar la
página no las pierde. Si el servidor se reinicia, una sesión que estaba
corriendo queda en `estado: "error"` con la causa (su hilo no sobrevive).

**Cómo pronostica en vivo.** `ecosim/live.py` llama a `ecosim.pronostico.forecast`
(misma construcción de variables que el experimento) sobre los conteos y modelos
de producción. Rezagos (`lag7`, `lag14`, `mean28`): si existen en los viajes
publicados se usan tal cual. `mean28` cuenta como existente solo con al menos
`MIN_RECENT_PEERS` = 4 días comparables (mismo tipo: entre semana / fin de semana)
con viajes publicados en los 28 días previos; con menos, se trata como faltante
(`faltan` lo dice, p. ej. `"mean28: 3 días comparables publicados (< 4)"`). Con todos
presentes, `"rezagos": "reales"`; si falta alguno, el pronóstico se hace para el día publicado
más reciente del mismo tipo que sí los tiene (`"dia_referencia"`) y se alinea al
reloj de hoy. Nunca se rellenan con ceros. La forma directa calcula `last15`,
`last60` y `elapsed_delta` con la misma función, alimentada con salidas y
llegadas inferidas del feed de hoy (cambios de disponibles + no rentables entre
lecturas; saltos de 5 o más bicis se toman como camioneta). Si hoy no hay
lecturas desde las 05:00 sin huecos de más de 15 min, `feed_hoy.completo` es `false`.
El asignador usa n, λ y topes congelados en `ecosim/results/frozen.json`.

## Datos y modelos de producción (`ecosim/actualizar.py`)

`uv run python -m ecosim.actualizar` (una vez al mes):

1. Lee <https://ecobici.cdmx.gob.mx/datos-abiertos/> y baja el mes más nuevo a
   `data/ecobici/` si no está. Si la página o la descarga fallan, lo avisa y usa
   los CSV que haya ahí (el usuario puede dejarlos a mano). Con `--sin-red` no lo intenta.
2. Audita el encabezado de cada CSV contra las columnas que lee `ecosim.data.build_trips`.
3. Arma `data/derived/ecosim/produccion/viajes.parquet` con todos los meses
   (desde 2024-01) usando `build_trips` (misma limpieza y auditoría,
   `viajes_auditoria.json`) y los conteos por estación y cuarto de hora (`conteos.npz`).
4. Entrena `lgbm_diario` y `lgbm_directo` con `ecosim.pronostico.train`
   (mismas variables y parámetros del run 3) con todos los días disponibles y
   los guarda en `produccion/modelos/`.
5. Escribe `produccion/manifiesto.json` (último día publicado, rango de
   entrenamiento, fecha de actualización, filas, archivos y resultado de la descarga).

Sin meses nuevos no reentrena (`--forzar` para hacerlo). No toca
`trips_2024_01_2026_08.parquet` ni `modelos/` del run 3 (los usan el replay y el reporte).

## Endpoints

Activos: `/api/snapshot`, `/api/zonas`, `/api/replay/index`, `/api/replay/{día}/dia`,
`/api/replay/{día}/{brazo}`, `/api/live/models`, `POST /api/live/forecast`,
`POST /api/live/assign/start`, `GET /api/live/assign`, `GET /api/live/assign/{id}`,
`POST /api/live/assign/{id}/stop`, `GET /api/live/feed`, `GET /api/live/eventos` (SSE).

`/api/replay/{día}/{brazo}` solo sirve días `AAAA-MM-DD` y los siete brazos; en
`data/derived/ecosim/replay/` quedan archivos viejos (`baseline.json`,
`daily.json`, `oracle.json` del run 2, sin `snap`) y cuatro carpetas mal nombradas
(`"2025-09-01 2025-09-11 2025-09-20 2025-09-30"`, etc., de una corrida con
`--days` entre comillas) que ni se sirven ni se listan. `ecosim.replay` ahora
rechaza días mal escritos.

Eliminados en ecosim3-ux (eran del prototipo de Predicción con corridas
guardadas y calificación de avisos): `/api/live/status`, `POST /api/live/run`,
`/api/live/latest`, `/api/live/eval`, `/api/live/orders.csv`. Eliminados en run 3:
`/api/snapshots/*`, `/api/rebalance*`, `/forecast/{short_name}`.
