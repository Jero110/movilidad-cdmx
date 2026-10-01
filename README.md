# Rebalanceo de Ecobici con un asignador cada 15 minutos

**Pregunta:** ¿qué tanto bajarían las estaciones vacías o llenas de Ecobici si un asignador decidiera cada 15 min
qué bicis mover, con un pronóstico de viajes? **Resultado** (simulador viaje por viaje, 05:30–00:30, 15 días de
evaluación): con un pronóstico emitido una sola vez a las 05:30 (`daily`), el asignador baja los minutos-estación
vacíos + llenos (E+F) **55% contra lo que hizo Ecobici**. Gana los 15 días con casi los mismos movimientos. El
resultado es *dentro del simulador*.

- **Reporte:** [`report/reporte-final.pdf`](report/reporte-final.pdf) (formato IEEE, 9 páginas).
- **Conclusiones y sesgos:** [`ecosim/results/CONCLUSIONES.md`](ecosim/results/CONCLUSIONES.md).
- **Datos y supuestos:** [`ecosim/FUNDAMENTOS.md`](ecosim/FUNDAMENTOS.md).

## Prender la app

Hace falta [`uv`](https://docs.astral.sh/uv/) (instala Python ≥ 3.12 si falta).

```bash
uv sync
cd scripts/ecobici_mapa
uv run python3 server.py            # abre http://localhost:8000
```

Opciones: `--port 9000`, `--no-browser`, `--no-live` (sin el loop de predicción en vivo). Ctrl-C para detener.

Qué funciona según los datos que haya:

| Pestaña | Qué muestra | Qué necesita |
|---|---|---|
| **Bicis ahora** | mapa 3D con el feed GBFS en vivo | nada, solo internet |
| **Predicción** | cada 15 min: pronóstico `daily` + asignador, qué mover ahora y qué tan bueno habría sido | los CSV de viajes en `data/ecobici/` |
| **Replay** | un día paso a paso (cada 15 min): estado, viajes, decisión del asignador, movimientos de Ecobici, E+F acumulado | los insumos de "Datos" y `uv run python -m ecosim.replay` |
| **Snapshots** | la red a las 05:30 o 12:30 de un día pasado | `uv run python3 scripts/fetch_daily_snapshots.py --since 2026-09-01` |
| **Rebalanceo** | reubicaciones de bicis entre viajes en un día | `uv run python3 analysis/rebalance_routes.py 2025-09-17` |

Sin la carpeta `data/` solo funciona **Bicis ahora**. Detalle de cada pestaña en
[`scripts/ecobici_mapa/README.md`](scripts/ecobici_mapa/README.md) y capturas en
[`scripts/ecobici_mapa/screenshots/`](scripts/ecobici_mapa/screenshots/).

## Datos

`data/` **no está en el repo** (2 GB). Sale de dos fuentes públicas:

1. **Viajes de Ecobici** (datos abiertos, un CSV por mes, viaje por viaje: estación y hora de origen y destino):
   <https://ecobici.cdmx.gob.mx/datos-abiertos/>. Se guardan en `data/ecobici/` con los nombres que espera
   `ecosim/config.py`: `2025-01.csv` … `2025-09.csv`, `2025-10-1.csv`, `2025-11.csv`. La predicción en vivo
   usa además los meses más recientes que haya (p. ej. `public_data_web_2026-08_2.csv`).
2. **Estado de las estaciones (GBFS) histórico**: el bucket público de
   [`MaxHalford/bike-sharing-history`](https://github.com/MaxHalford/bike-sharing-history)
   (`s3://bike-sharing-history/mexico-city/ecobici`, vía `storage.googleapis.com`). `build-snapshots` lo lee
   directo con duckdb; no hay descarga manual.

Construir los insumos, en este orden, desde la raíz del repo:

```bash
uv run python -m ecosim.data build-trips       # CSV ene–nov 2025 → data/derived/ecosim/trips_2025_01_11.parquet
uv run python -m ecosim.data build-snapshots   # bucket GBFS → data/derived/ecosim/snapshots/{día}.parquet
uv run python -m ecosim.days                   # días de evaluación y selección → ecosim/days.json (ya versionado)
uv run python -m ecosim.medicion               # movimientos de Ecobici y dañadas → ecobici_moves.parquet, damage_events.parquet
uv run python -m ecosim.pronostico             # pronósticos daily/ma/model/oracle → data/derived/ecosim/forecasts/
uv run python -m ecosim.replay                 # precalcula el replay de la app (15 días × 4 brazos, ~15 min)
```

**Diciembre 2025 está sellado.** `2025-12.csv` no se lee nunca: `config.SEALED_FROM`, los cargadores de ecosim y
el pronóstico en vivo lo excluyen explícitamente y lo registran en el log.

## Experimentos

```bash
uv run python -m ecosim.run --all                  # todo desde cero, ~85 min (8 procesos de 1 thread)
uv run python -m ecosim.run --stage eval           # un paso: v1 | lambda | h | grid | eval | sens | falla | tablas
uv run python -m ecosim.sim --day 2025-09-03 --arm ecobici   # un día de un brazo del simulador
uv run pytest tests/ecosim -q                      # 318 tests con data/ construida
```

Sin `data/` pasan 292, 22 se saltan y los 4 de `test_replay.py` dan error porque necesitan los viajes del día.

Resultados versionados en `ecosim/results/`: `resultados.csv` (todas las corridas), `frozen.json` (parámetros
congelados en días de selección), `tablas.md` y `CONCLUSIONES.md`.

## Estructura

```
ecosim/                 el código del proyecto
  data.py, days.py        carga de viajes y snapshots, días de selección y evaluación
  medicion.py             qué movió Ecobici de verdad (desde GBFS) y bicis dañadas
  pronostico.py           pronósticos daily / ma / model / oracle
  asignador.py            asignador MILP de horizonte móvil (HiGHS), decide cada 15 min
  sim.py                  simulador viaje por viaje
  run.py                  experimentos: selección, evaluación, sensibilidades, tablas
  replay.py, live.py      replay de decisiones y predicción en vivo para la app
  results/                resultados versionados
  FUNDAMENTOS.md          auditoría de datos y supuestos
scripts/ecobici_mapa/   servidor (FastAPI) y app web (MapLibre)
tests/ecosim/           tests
report/                 reporte final (PDF, fuente .qmd, figuras, tablas y el origen de cada número)
docs/, wiki/            solo los documentos que el código y el reporte citan como fuente (planes de los dos runs,
                        notas del run 1 y dos notas de investigación)
```
