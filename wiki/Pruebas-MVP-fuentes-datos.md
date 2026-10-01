---
tags: [datos, mvp, prueba-real, gobierno, terceros, pricing]
created: 2026-09-13
source: pruebas en vivo (curl/WebFetch/WebSearch) — ver rutas exactas por fuente
---

# Pruebas MVP: fuentes de datos reales (descarga/conexión real, no solo investigación)

Pivote respecto a [[Viabilidad-GNN-y-alternativas]]: en vez de solo documentar
qué *dicen* los portales que tienen, aquí se descargó/conectó de verdad a
cada fuente para confirmar qué tan usable es el dato tal cual llega —
columnas reales, volumen real, y si hace falta pago o solicitud.

> [!question] Pregunta que originó esta página
> El usuario quiere evidencia dura ("hice esta prueba, tengo tal dato, de
> aquí puede salir tal cosa") antes de comprometerse a una fuente, cubriendo
> tanto gobierno (re-verificado) como terceros (Uber, Waze, Google) no
> probados antes.

## 1. Fuentes de gobierno — re-verificadas con descarga real

| Fuente | URL de descarga real usada | Resultado | Filas / tamaño | Columnas reales |
|---|---|---|---|---|
| **Metro (afluencia simple)** | `datos.cdmx.gob.mx/.../0e8ffe58.../download/...csv` | ✅ Descarga directa, sin auth | 1,180,921 filas, 60 MB | `fecha,anio,mes,linea,estacion,afluencia` — confirma lo ya sabido: **solo conteo de entradas, no hay origen-destino** |
| **Metrobús (afluencia simple)** | `datos.cdmx.gob.mx/.../f7943c47.../download/...csv` | ✅ Descarga directa | 53,733 filas, 2 MB | `fecha,anio,mes,linea,afluencia` — mismo problema, sin OD; algunos valores `NaN` en líneas nuevas |
| **C5 incidentes viales (2022-2024)** | `archivo.datos.cdmx.gob.mx/C5/incidentes_viales/inViales_2022_2024.csv` | ✅ Descarga directa | 504,262 filas, 104 MB | `folio,fecha_creacion,hora_creacion,...,longitud,latitud` — punto geolocalizado limpio, listo para map-matching a red vial |
| **AGEB demográfico (Censo 2020)** | dataset `caracteristicas-demograficas-de-la-poblacion-ageb-censo-2020` | ✅ Confirmado accesible | — | Disponible en **XLSX, SHP y GeoJSON** (no solo CSV) — el GeoJSON es directamente usable para un grafo de adyacencia espacial sin conversión manual |
| **Ecobici — GBFS tiempo real** | `gbfs.mex.lyftbikes.com/gbfs/gbfs.json` (discovery) → 5 sub-feeds `es`/`en` | ✅ Funciona en vivo, JSON estándar, sin key | 677 estaciones, ~166 KB, `ttl:10` (refresco cada 10s) | Ver detalle completo en sección 1.bis |
| **Ecobici — histórico viaje-a-viaje** | `ecobici.cdmx.gob.mx/wp-content/uploads/2026/09/public_data_web_2026-08_2.csv` | ✅ Descarga directa, sin auth | **1,294,274 viajes en un solo mes** (agosto 2026), 97 MB | `Genero_Usuario,Edad_Usuario,Bici,Ciclo_Estacion_Retiro,Fecha_Retiro,Hora_Retiro,Ciclo_EstacionArribo,Fecha_Arribo,Hora_Arribo` — **esta es la prueba dura**: cada fila es una arista real estación→estación con timestamps completos |

**Nota práctica:** los links de descarga de Ecobici no son un patrón fijo
(`YYYY-MM.csv`, luego `public_data_web_YYYY-MM.csv`, luego con sufijo `_2`)
— cambian de convención cada tanto. Cualquier pipeline de descarga debe
parsear el HTML de `open-data/` para encontrar el link vigente, no asumir
la URL por fecha.

## 1.bis Ecobici GBFS — detalle completo de los 5 sub-feeds (verificado 2026-09-16)

Endpoint de descubrimiento: `https://gbfs.mex.lyftbikes.com/gbfs/gbfs.json` →
lista 5 feeds en `es`/`en`. Operador declarado en `system_information`:
**BKT** (no Lyft/Motivate pese al dominio `lyftbikes.com` — probablemente
infraestructura tecnológica heredada), sistema `Ecobici`, `start_date:
2022-06-19`, `timezone: GMT-06:00`.

| Sub-feed | Contenido | Snapshot real (2026-09-16, ~esa hora) |
|---|---|---|
| `system_information` | Metadata del sistema | 1 registro — operador, fecha de inicio, timezone |
| `station_information` | Estático: id, nombre, lat/lon, capacidad, kiosko | **677 estaciones** |
| `station_status` | **Vivo**: `num_bikes_available`, `num_docks_available`, `num_bikes_disabled`, `num_docks_disabled`, `is_installed/renting/returning`, `last_reported` (unix ts) | 677 estaciones, refresco `ttl:10`s |
| `free_bike_status` | Bicis sueltas fuera de estación (dockless) | **Vacío** (`bikes: []`) — Ecobici opera 100% con estaciones fijas, sin modalidad dockless activa |
| `system_alerts` | Avisos operativos | 1 alerta activa: cierre de 11 estaciones (Cuauhtémoc: 176, 264-269, 271-275; Benito Juárez: 402, 404, 405) del 13 al 16 de septiembre por fiestas patrias |

**Distribución real de `station_status` en el snapshot de prueba** (677
estaciones, vía `uv run` + pandas):
- **18.6% de estaciones (126/677) sin bicis disponibles** en ese instante —
  evidencia directa y cuantificada del fenómeno de spillover de demanda
  entre estaciones cercanas (usuario no encuentra bici, va a la estación
  vecina) que motivó esta exploración.
- Solo **1.8% (12/677) sin docks libres** (llenas) — asimetría clara: el
  problema dominante en el snapshot es falta de bicis, no falta de espacio.
- **663/677 activas** (`is_renting=1`); las 14 inactivas son consistentes
  con la alerta de cierre por fiestas patrias.

**Qué aporta este feed que el histórico OD (sección 1) no tiene:**
1. Es **snapshot del presente**, no acumula historia por sí mismo — para
   entrenar sigue haciendo falta el histórico OD ya confirmado (1.29M
   viajes/mes). Su valor no es reemplazar ese dato, es complementarlo.
2. Habilita **construir una serie histórica propia y original** desde ahora
   si se hace polling periódico (cada 10s-1min) y se guardan snapshots —
   nadie más tiene ese dataset, sería 100% del proyecto.
3. Habilita que la **app funcional (D5)** sirva predicciones contra el
   estado real del sistema en vivo, en vez de una demo estática sobre datos
   congelados — mapa con disponibilidad real + predicción superpuesta.
4. Habilita **validar el modelo contra la realidad**: comparar la
   predicción hecha hace N minutos contra lo que el feed reporta que pasó
   de verdad, sin esperar la siguiente publicación mensual del CSV
   histórico.

**Implicación para GNN-Ecobici**: cambia la respuesta a "¿esto es
tiempo real?" — de las alternativas evaluadas en
[[Viabilidad-GNN-y-alternativas]], GNN-Ecobici es la única con un camino
realista a un componente real-time genuino en D5, gracias a este feed.
Ver [[GNN-Ecobici-decisiones-de-diseno]] para la implicación de diseño.

## 1.ter Dos trampas del feed GBFS al consumirlo en código (verificado 2026-09-16)

Detectadas al construir el mapa en vivo (`scripts/ecobici_mapa/`), ambas
verificadas contra el feed real. **Ninguna está en la especificación GBFS**:
son desviaciones propias de la implementación de Ecobici, y las dos producen
error silencioso — el código corre y devuelve resultados plausibles pero
incorrectos.

**(1) `system_alerts` no llena `station_ids`.** El campo estándar de GBFS
para decir qué estaciones afecta una alerta viene **ausente**. Los números de
estación van en el texto libre de `description`:

> "Por fiestas patrias, estas estaciones estarán fuera de servicio del 13 al
> 16 de septiembre: Cuauhtémoc: 176, 264 a 269 y 271 a 275. Benito Juárez:
> 402, 404 y 405."

Consumir el campo estándar da cero estaciones afectadas, sin error. Hay que
parsear la prosa, expandir los rangos ("264 a 269") y **descartar las fechas
del mismo texto** ("del 13 al 16 de septiembre"), que de otro modo se leen
como números de estación.

**(2) `station_id` y `short_name` son espacios de identificadores distintos,
y `short_name` puede ser compuesto.** Dos problemas encadenados:

- El número de estación que usa el público (y que aparece en las alertas) es
  `short_name`, no `station_id`. **No coinciden**: la estación con
  `short_name` `"033"` tiene `station_id` `"545"`. Cruzarlos marca estaciones
  equivocadas — en la primera versión del mapa esto produjo 4 falsos
  positivos de 19 marcados.
- `short_name` no siempre es un número: el feed trae `"264-275"`,
  `"268-269"`, `"390-391"`, `"107-108"` — una estación física que agrupa
  varios números consecutivos. Compararlo por igualdad exacta contra un
  número suelto nunca casa.

**Validación de que el matching quedó correcto:** las 15 estaciones que la
alerta menciona resuelven a **10 estaciones físicas** (varias compuestas), y
las 10 reportan `is_renting=0` en `station_status`. El feed en vivo confirma
de forma independiente, por un campo distinto, exactamente las mismas
estaciones — cero falsos positivos, cero faltantes.

**Implicación práctica:** cualquier conteo de "estaciones afectadas por
alertas" o join entre alertas y estaciones en este proyecto debe pasar por
esta normalización. Es un caso más de la clase de defecto documentada en el
log del 2026-09-15 para `tipo_pago` del Metro: invisible en `head()` y en
chequeos de nulos, solo aparece al validar el resultado contra una segunda
fuente.

## 2. Terceros — verificados, ninguno tiene demo abierta hoy

| Fuente | Qué se probó | Resultado | Pricing/condición |
|---|---|---|---|
| **Uber Movement** | `movement.uber.com` en vivo | ❌ **Muerto** — redirige (301) a `uber.com/business/movement-decommissioning` | N/A — el servicio fue dado de baja, no hay API ni descarga |
| **Waze for Cities** | Documentación oficial + ficha de partners | ⚠️ Existe pero **no hay demo sin aprobación** — requiere aplicación formal con correo institucional, revisión por Google/Waze, y estar dentro de una categoría elegible (gobierno, infraestructura pública, **instituciones académicas sí califican**) | **Gratis** una vez aprobado (no es pago), pero el timeline de aprobación es incierto y no controlable para el calendario de la tesis — no se puede tratar como fuente garantizada |
| **Google Maps Platform** (Directions/Distance Matrix, no "Environmental Insights") | Pricing oficial 2026 | ✅ Acceso inmediato con API key, **de pago** | Directions API: **$5.00 USD / 1,000 requests**. Distance Matrix: **$5.00 USD / 1,000 elements** (origen×destino). Planes mensuales: Starter $100/mes (50k llamadas), Essentials $275/mes (100k), Pro $1,200/mes (250k). Da $200 USD de crédito gratis/mes en cuenta nueva → ~40,000 requests gratis de Directions antes de pagar |
| **Google Environmental Insights Explorer (EIE)** | Documentación + búsqueda de API | ⚠️ Existe y es gratis, pero es sobre **emisiones de carbono de edificios/transporte agregadas por ciudad**, no viajes individuales — y **no tiene API pública**, solo descarga manual desde la web app | No aplica a un problema de movilidad de personas/grafo — descartar como fuente primaria |

## 3. Veredicto operativo

- **Nada cambia el ranking de [[Viabilidad-GNN-y-alternativas]]** — al contrario,
  se refuerza con evidencia dura: Ecobici es la única fuente que entrega,
  hoy, sin pago ni solicitud, un archivo con **más de un millón de aristas
  origen-destino reales por mes**, descargable con un `curl` simple.
- **Uber Movement queda descartado por completo** — no es "difícil de
  conseguir", literalmente ya no existe.
- **Waze for Cities es una opción real pero no para el timeline de una
  tesis** — el cuello de botella no es el dato (una vez aprobado, es
  gratis y trae jams/alerts/irregularidades en tiempo real), es el proceso
  de aprobación fuera de tu control. Si el usuario quiere intentarlo de
  todos modos, se puede aplicar en paralelo sin bloquear el proyecto,
  tratándolo como *upside* opcional, no como plan A.
- **Google Maps Platform sí tiene demo inmediata pero es de pago** — viable
  solo para volúmenes pequeños (con el crédito gratis de $200/mes) y como
  fuente *complementaria* (p.ej. tiempos de viaje estimados entre AGEBs
  para el análisis de equidad de acceso), no como fuente principal de un
  grafo a escala de ciudad — el costo escala rápido con cualquier matriz
  OD grande (194×194 de la EOD = 37,636 elementos = ~$188 USD por corrida).
- **AGEB en GeoJSON** es una mejora práctica no documentada antes: permite
  construir el grafo de adyacencia espacial para la alternativa "equidad de
  acceso" sin pasos manuales de conversión desde shapefile.

## 4. Histórico de disponibilidad GBFS (2026-09-19) — ya existe, de tercero, verificado en vivo

> [!warning] Contradice a [[Demo-Ecobici-pronostico-intradia]]
> Esa página marca "histórico propio de snapshots GBFS" como la pieza
> **faltante y bloqueante** de la pestaña 2 (pronóstico intradía): *"El POC
> hoy consulta el feed, pero no guarda historia [...] hay que persistir
> snapshots cada 1–5 minutos"*. Esto ya no es cierto de forma absoluta — un
> tercero lleva **2.4 años** archivando exactamente ese feed para CDMX. No
> reemplaza tener collector propio (ver limitación de cadencia abajo), pero
> significa que el proyecto puede empezar a modelar *hoy* con historia real
> en vez de esperar meses a acumular la propia.

**Fuente:** [`MaxHalford/bike-sharing-history`](https://github.com/MaxHalford/bike-sharing-history)
— repo de "git scraping" que archiva el feed GBFS de 76 sistemas de
bike-sharing en el mundo cada 15 min, incluyendo **#051 Mexico City /
Ecobici**. Los snapshots se versionan como GeoJSON en el historial de git y
se compilan a Parquet en un bucket público de GCS, consultable sin
credenciales vía DuckDB:

```python
import duckdb
con = duckdb.connect(":memory:")
con.execute("SET s3_endpoint='storage.googleapis.com'")
con.execute("SET s3_region='auto'")
con.execute("""
    SELECT * FROM READ_PARQUET(
        's3://bike-sharing-history/mexico-city/ecobici/*/*.parquet'
    )
""").fetch_df()
```

**Verificado en vivo (no solo leído en el README), 2026-09-19:**

| Métrica | Valor medido |
|---|---|
| Filas totales | 35,050,699 |
| Estaciones distintas | 703 |
| Rango de fechas | 2024-04-10 → 2026-08-31 (~2.4 años) |
| Columnas | `station_id, name, short_name, external_id, longitude, latitude, capacity, num_bikes_available, num_bikes_disabled, num_docks_available, num_docks_disabled, is_installed, is_renting, is_returning, committed_at_utc` |

**Confirmación de que es CDMX real** (no basta el nombre de la carpeta —
se cruzó contra el feed vivo que ya usa `scripts/ecobici_mapa/`):
bounding box lat 19.35–19.43 / lon -99.13 a -99.20 (Roma, Condesa, San
Ángel, Pedregal); nombres de calles reales con prefijo `CE-` (p. ej. "CE-438
Adolfo Prieto-José María Olloqui"); y **el `station_id` 10 en este archivo
es exactamente la misma estación** ("CE-438 Adolfo Prieto-José María
Olloqui") que el `station_id` 10 en `ecobici_station_info.json` — mismo
espacio de IDs que el feed en vivo, sin necesidad de re-mapear.

**Limitación real medida — la cadencia de 15 min NO es uniforme en todo el
rango.** Se midió el gap entre snapshots consecutivos, no solo el
conteo por mes:

| Periodo | Gap mediana | Gap p90 | Hueco máximo |
|---|---|---|---|
| 2024-10 | 15.3 min | 19.1 min | 57 min |
| 2025-05 | 15.5 min | 21.0 min | 290 min (~5h) |
| 2026-08 | 42.8 min | 105.3 min | 669 min (~11h) |

El collector corre en GitHub Actions best-effort y se degrada con el
tiempo — el propio README lo advierte para otras ciudades ("2026 ran as
low as ~16/day"). **Ventana densa y confiable para modelar minuto a
minuto: 2024-09 a 2025-06 (~9 meses)**, con mediana real de 15 min y 90%
de huecos por debajo de ~20 min. Fuera de esa ventana la serie tiene huecos
de horas y debe tratarse como muestreo irregular, no como series regulares
a intervalo fijo.

**Defecto de calidad pendiente de limpiar:** el bounding box crudo trae
valores absurdos (`latitude=0.0`, `longitude=99.16`) en algún subconjunto
de filas — probablemente coordenadas mal parseadas en snapshots puntuales
del scraper. Filtrar antes de usar; no invalida el archivo pero hay que
excluir esas filas.

**Qué NO es este archivo:** no es la fuente oficial de Ecobici/SEMOVI, es
un tercero re-publicando el feed público. Depende de que ese repo siga
corriendo — no es razón para no arrancar el collector propio dentro del
POC, sí es razón para no esperar a tenerlo antes de empezar a modelar.

## Ver también
- [[Viabilidad-GNN-y-alternativas]] — ranking de alternativas que esta página confirma con evidencia dura.
- [[Contexto-del-caso]] — panorama de datos que motivó investigar estas fuentes.
- [[Demo-Ecobici-pronostico-intradia]] — página cuyo bloqueante de "falta histórico GBFS" esta sección 4 resuelve parcialmente.
