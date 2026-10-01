# Mapa Ecobici en tiempo real · producto final de ecosim

Mapa de las 677 estaciones de Ecobici con disponibilidad de bicis en vivo,
alimentado por el feed GBFS oficial, más las dos pestañas del producto final
de ecosim (run 2): **Replay** de un día con las decisiones del asignador cada
15 min y **Predicción** en vivo (qué mover ahora y qué tan bueno habría sido).

```bash
cd scripts/ecobici_mapa
uv run python3 server.py                  # abre http://localhost:8000
uv run python3 server.py --no-browser     # sin abrir el navegador
```

Opciones: `--port 9000`, `--no-browser`, `--no-live` (sin el loop en vivo),
`--results-dir <carpeta>` (dónde están `frozen.json` y `resultados.csv`; por
defecto `ECOSIM_RESULTS_DIR` o `ecosim/results`). Ctrl-C para detener.

Capturas en [`screenshots/`](screenshots/) y descripción corta de la
arquitectura en [`screenshots/APP.md`](screenshots/APP.md).

## Por qué hay un servidor y no solo un HTML

El feed GBFS **no manda header `Access-Control-Allow-Origin`**, así que un
`fetch()` desde el navegador se bloquea por CORS. `server.py` resuelve eso:
sirve el mapa y actúa de proxy hacia GBFS, cacheando por feed según su
volatilidad (status 15s, información 1h, alertas 5min).

El servidor es FastAPI + uvicorn (dependencias del proyecto; `uv sync` basta).

## Qué muestra

Vista 3D de la ciudad (MapLibre GL) con la cámara inclinada 58°.

- **Ciudad en 3D**: edificios extruidos con altura real sobre un mapa claro.
- **Estaciones como iconos de bici**: una por estación, con color por estado
  (rojo 0 bicis, ámbar 1-3, verde 4+, azul sin espacio, gris inactiva).
- **Click en una estación** → tarjeta con bicis disponibles, ocupación,
  espacios libres, bicis fuera de uso, capacidad, antigüedad del dato y enlace
  a Street View. Se mantiene al día sola mientras esté abierta.
- **Cifra dominante**: total de bicis disponibles en el sistema, más
  estaciones activas / sin bicis / sin espacio.
- **Buscador**: filtra por nombre o número, ordena por escasez, y al elegir
  vuela a la estación y abre su tarjeta.
- **Alertas**: `system_alerts` del feed.

Refresco cada 15s. Si el feed se cae, conserva el último estado bueno y lo
señala en el pie en vez de vaciar el mapa.

### Snapshots diarios (05:30 / 12:30)

La pestaña **Snapshots** muestra el mismo mapa/tarjeta que "Bicis ahora" pero
alimentado por un corte histórico fijo en vez del feed en vivo: para una
fecha y horario (05:30 o 12:30, hora CDMX) elegidos en el selector, pinta el
snapshot real más cercano a esa hora ese día. Útil para ver cómo se veía la
red en un momento pasado sin depender de que el collector propio lleve mucho
tiempo corriendo.

Los datos vienen de `data/derived/daily_snapshots.parquet`, construido por
`scripts/fetch_daily_snapshots.py` a partir del histórico público de
terceros (`MaxHalford/bike-sharing-history`, ver
[[Pruebas-MVP-fuentes-datos]] §4) — no del feed GBFS en vivo. El script se
puede re-correr para agregar días nuevos sin duplicar los ya guardados:

```bash
uv run python3 scripts/fetch_daily_snapshots.py --since 2026-09-01
```

Los endpoints `/api/snapshots/dates` y `/api/snapshots/{date}/{slot}` sirven
la lista de fechas disponibles y el snapshot de un día/horario, en el mismo
formato que `/api/snapshot`.

### Diagnóstico de reubicaciones (día completo, no solo de noche)

La pestaña **Rebalanceo** muestra 2,639 pares estación→estación y 8,257
bicis para el 2025-09-17. Los datos se derivan de viajes consecutivos: una
bici que termina en A y cuyo siguiente viaje inicia en B ≠ A se registra
como una **reubicación entre viajes**. Es consistente con rebalanceo, pero
también puede reflejar mantenimiento, retiro o errores de registro; por eso
la app no lo presenta como una ruta oficial ni como prueba de un camión.

**Ya no se filtra a una ventana nocturna fija.** La versión anterior solo
contaba saltos dentro de 18:00–00:35 → 05:00–10:00, asumiendo rebalanceo
exclusivamente nocturno. Medido sobre el histórico completo (ver
[[Rebalanceo-Ecobici-suposicion-horario]]), **70.6% de los saltos detectados
ocurre el mismo día calendario** en que se dejó la bici (gap mediano 2.3h),
y solo 26.1% cruza a otro día con la forma overnight (visto de noche,
recuperado a primera hora). Por eso la pestaña muestra ambos: líneas
sólidas para mismo día, punteadas para overnight, con un toggle
Todo/Mismo día/Overnight.

Los endpoints `/api/rebalance` y `/api/rebalance/station/{short_name}` sirven
el agregado (con `days_crossed` por ruta) y el detalle por bicicleta. Los
Parquet se construyen con `analysis/rebalance_routes.py <día>` (un solo
argumento de fecha, ej. `2025-09-17`).

## Producto final de ecosim (run 2)

### Pestaña Replay: un día con las decisiones cada 15 min

Eliges un día (los 15 de evaluación de `ecosim/days.json`; también los de
selección si se precalcularon) y un brazo: el **mejor brazo real congelado**
(`best_real` de `frozen.json`, hoy `daily`), el **oracle**, el **replay de
Ecobici** y el **baseline** sin rebalanceo. El reloj va de 05:30 a 00:30 en
pasos de 15 min (play/pausa, paso adelante/atrás, velocidad 1–8×, slider).
En cada paso:

- **Estado** del simulador por estación (vacía, 1–3, 4+, llena, fuera de
  servicio; anillo morado = tiene dañadas).
- **Viajes reales** que salen en el intervalo (arcos origen→destino; al
  reproducir, puntos que viajan), como los usa el simulador, y desvíos.
- **Decisión del asignador** en t: órdenes (±bicis por estación), cuándo se
  hacen efectivas (t + L), proyección y cotas por estación, y E+F esperado
  sin/con las órdenes.
- **Insumos** de esa decisión: emisión de pronóstico usada (variante, f, H,
  L), estado que vio, bodega acumulada, pendientes, topes (por hora, bodega,
  bicis por movimiento), λ, μ y cota de retiro, estado de HiGHS.
- **Órdenes efectivas** en el intervalo con sus recortes (por movimiento,
  fuera de servicio, físico, bodega) y **movimientos de Ecobici** medidos por
  GBFS en el mismo intervalo.
- **Métricas acumuladas**: E+F (min-estación vacías + llenas, sin fuera de
  servicio), movimientos y bicis movidas del brazo contra Ecobici y baseline.

Los datos se **precalculan** con `ecosim/replay.py`, que llama a
`sim.simulate` con exactamente los mismos insumos y parámetros que
`run.run_one` (el asignador va envuelto en una grabadora que solo lee). El
E+F final de cada (día, brazo) se compara contra `resultados.csv` y tiene que
ser igual (tolerancia 0; lo exige `tests/ecosim/test_replay.py`):

```bash
# desde la raíz del repo; los 15 días de evaluación × 4 brazos (~12 min, 1 thread)
uv run python -m ecosim.replay
uv run python -m ecosim.replay --days 2025-09-03            # un día
uv run python -m ecosim.replay --split seleccion            # días de selección
uv run python -m ecosim.replay --results-dir ../ecosim2-integracion/ecosim/results
```

Salida en `data/derived/ecosim/replay/` (fuera de git, ~40 MB los 15 días):
`{día}/dia.json` (estaciones, viajes, movimientos de Ecobici),
`{día}/{brazo}.json` (frames) e `index.json`. Si `frozen.json` cambia, la
pestaña lo avisa y basta con volver a correr el módulo (los archivos que ya
son del `frozen.json` actual se saltan; `--force` recalcula todo).

Endpoints: `/api/replay/index`, `/api/replay/{día}/dia`, `/api/replay/{día}/{brazo}`.
Enlace directo: `/#replay?day=2025-09-03&arm=daily&k=30` (k = paso de 15 min).

### Pestaña Predicción: pipeline en vivo

`server.py` arranca un hilo (`ecosim.live.LiveLoop`, se apaga con `--no-live`):

1. **Cada 60 s** lee el feed (el mismo proxy de `/api/snapshot`) y guarda la
   lectura en `data/derived/ecosim/live/gbfs/{día}.jsonl` (bicis, dañadas,
   anclajes libres, capacidad y en servicio, por estación). Ese registro es
   el estado y la base para medir.
2. **Cada 15 min** (05:30, 05:45, …; y con el botón **Correr ahora**) corre
   `ecosim.live.run_at(t)`:
   - **estado** = última lectura con hora ≤ t;
   - **pronóstico** de salidas y llegadas por estación y bloque de 60 min
     con el modelo elegido. **Por default `daily`** (el mejor brazo real del
     run 2: media de 4 semanas del mismo tipo de día, una emisión, sin
     corrección; el loop corre con él). En el selector también está `ma` (la
     misma media + corrección intradía con viajes inferidos del feed, solo
     sobre los bins de 15 min que el registro cubre; **sesgo**: compara
     viajes inferidos, que son netos y menos que los reales, contra una base
     de viajes reales, así que el factor tiende a bajar el pronóstico). `model` (LightGBM) queda enchufable en `live.FORECASTERS`
     pero no disponible: necesita los viajes reales del día anterior;
   - **asignador** con lo congelado en `frozen.json` (λ, μ, cota de retiro,
     L, topes, bicis por movimiento; f y H del mejor (f, H) del modelo),
     bodega inicial 0 y sin órdenes pendientes;
   - **riesgo**: flujo sin mover nada en [t, t + L + H): primer minuto en que
     cada estación se vacía o se llena.
   Cada corrida se guarda en `data/derived/ecosim/live/runs/{fecha-hora}_{modelo}.json`.
3. **Qué tan bueno sería** (`live.evaluate`, `/api/live/eval`): cuando un
   bloque pronosticado ya terminó, compara salidas y llegadas pronosticadas
   contra las **inferidas** de los cambios de stock del feed (neto por
   lectura de ~1 min, así que subestiman; cambios ≥ 5 bicis se toman como
   camión), el stock proyectado sin mover contra el observado y E/F en el
   horizonte completo. Muestra el error acumulado por antelación.

**Datos para el `ma` en vivo.** Los datos abiertos de Ecobici en
`data/ecobici/` llegan hasta **agosto 2026** (último día completo
2026-08-30), así que para hoy no hay las 4 semanas previas. Se usa un
**fallback declarado**: las 4 semanas que terminan en el último día
publicado, del mismo tipo de día (la pestaña lo rotula en naranja). Nunca se
lee `2025-12.csv` (sellado) ni un día ≥ el de la corrida. Cuando se publiquen
meses nuevos basta con dejarlos en `data/ecobici/`.

Una corrida sin servidor: `uv run python -m ecosim.live` (`--model ma` para la otra variante); la
evaluación acumulada: `uv run python -m ecosim.live --eval`.

Endpoints: `/api/live/status`, `/api/live/latest?model=daily`,
`POST /api/live/run?model=daily`, `/api/live/eval`. Enlace directo: `/#vivo`.

## Dos rarezas del feed que el código maneja

Ambas verificadas contra datos reales el 2026-09-16, y ninguna está en la
especificación GBFS:

1. **Las alertas no llenan `station_ids`.** Ecobici manda los números de
   estación en el texto libre de `description` ("Cuauhtémoc: 176, 264 a 269 y
   271 a 275"). `parse_station_numbers()` los extrae, expande los rangos y
   descarta las fechas del mismo texto ("del 13 al 16 de septiembre"), que de
   otro modo se leerían como números de estación.

2. **`short_name` puede ser compuesto.** Además de `"033"`, el feed trae
   `"264-275"`, `"268-269"`, `"390-391"`: una estación física que agrupa varios
   números. `short_name_keys()` los expande.

   Cuidado adicional: `station_id` y `short_name` son espacios de
   identificadores **distintos** — la estación con `short_name` `"033"` tiene
   `station_id` `"545"`. Cruzarlos marca estaciones equivocadas.

   Validación de que el matching quedó bien: las 15 estaciones que la alerta
   menciona resuelven a 10 estaciones físicas, y las 10 reportan
   `is_renting=0` en el feed en vivo — confirmación independiente, sin falsos
   positivos.

## Fuente

`https://gbfs.mex.lyftbikes.com/gbfs/gbfs.json` → sub-feeds `es`.
Detalle de los 5 sub-feeds en [[Pruebas-MVP-fuentes-datos]] §1.bis.

## Siguiente paso posible

El servidor no guarda historia: cada poll reemplaza al anterior. Persistir los
snapshots a disco construiría la serie temporal propia que el wiki señala como
100% original del proyecto (nadie más la tiene) — ver
[[Pruebas-MVP-fuentes-datos]] §1.bis, punto 2.

La siguiente etapa es convertir el pronóstico actual de la tarjeta en un
backtest reproducible y, después, comparar reglas de inventario y min-cost
flow/MPC en un simulador. La pestaña **Rebalanceo** actual es un diagnóstico de
reubicaciones históricas, no un recomendador de camiones. El diseño y los
criterios para avanzar están en [[Demo-Ecobici-pronostico-intradia]] y
[[Rebalanceo-Ecobici-modelos-optimizacion-RL]].
