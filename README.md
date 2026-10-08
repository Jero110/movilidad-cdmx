# Rebalanceo de Ecobici: pronóstico de viajes + asignación cada 15 minutos

**Pregunta:** ¿combinar un pronóstico de viajes con un algoritmo de asignación reduce los minutos en que las estaciones de Ecobici están vacías o llenas, frente a lo que hace hoy Ecobici, con restricciones logísticas realistas?

**Resultado:** simulador viaje por viaje, 05:00–00:30. Los parámetros (horizonte n y precio por visita λ) se eligen en los días válidos de agosto de 2025 y se congelan; la prueba son los 121 días válidos de septiembre a diciembre de 2025. Una asignación greedy cada 15 minutos guiada por una media móvil deja **57,802 minutos-estación vacíos o llenos (E+F) por día, contra 102,135 de Ecobici: 43.4 % menos** (IC95 41.2–45.6 %), y gana los 121 días. El resultado es *dentro del simulador*. Los límites están en el reporte y en [`ecosim/results/CONCLUSIONES.md`](ecosim/results/CONCLUSIONES.md).

- **Reporte:** [`report/reporte-caso.pdf`](report/reporte-caso.pdf) (IEEE). Fuente en [`report/reporte-caso.tex`](report/reporte-caso.tex). Cada cifra tiene su origen en [`report/fuentes-numeros.md`](report/fuentes-numeros.md).
- **Datos y supuestos:** [`ecosim/FUNDAMENTOS.md`](ecosim/FUNDAMENTOS.md).
- **Resultados:** [`ecosim/results/`](ecosim/results/) (`resultados.csv`, `tablas.md`, `frozen.json`).

## Código

| Módulo | Qué hace |
|---|---|
| `ecosim/data.py` | Viajes de datos abiertos y fotos del feed de estaciones |
| `ecosim/medicion.py` | Movimientos de Ecobici inferidos: cambio entre fotos menos viajes |
| `ecosim/sim.py` | Simulador viaje por viaje: recoge en t+15, entrega en t+60, desvíos a la estación más cercana |
| `ecosim/pronostico.py` | Media móvil y LightGBM, en forma diaria y directa (1–4 h) |
| `ecosim/asignador.py` | Proyecta el inventario de cada estación y arma las tablas de costo de quitar o meter bicis |
| `ecosim/greedy.py` | Procedimiento greedy por paquetes (un receptor con sus donantes) que decide cada 15 min qué recoger y qué entregar; parámetro opcional α de costo por km |
| `ecosim/run.py` | Experimentos: validación (agosto 2025) y prueba (sep–dic 2025) de los siete escenarios |
| `ecosim/replay.py` | Precálculo del Replay de la app (16 días × 7 escenarios) |
| `ecosim/live.py` | Pronóstico y asignación en vivo sobre el feed |
| `ecosim/actualizar.py` | Actualización mensual: baja el mes nuevo y reentrena los modelos de producción |

## Prender la app

Hace falta [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
cd scripts/ecobici_mapa
uv run python3 server.py            # http://localhost:8000
```

Opciones: `--port`, `--no-browser`, `--no-live`.

| Pestaña | Qué muestra | Qué necesita |
|---|---|---|
| **Ahora** | Mapa con las bicis disponibles, buscador y ficha de estación con todos los campos del feed | Solo internet |
| **Replay** | Un día paso a paso cada 15 min, con los números de cada foto que cuadran (viajes, órdenes, cambios de etiqueta), comparación de dos escenarios, órdenes de dónde a dónde (emitidas y las que llegaron), selector de costo por km (α) y coropleta por AGEB | Los datos de abajo y `uv run python -m ecosim.replay --force` |
| **Predicción** | Pronóstico (diario o a 1–4 h, tres modelos) y asignación en vivo que emite órdenes cada 15 min (con α seleccionable), disparada por los cambios del feed | Modelos de producción: `uv run python -m ecosim.actualizar` |

Contratos JSON, qué es medido y qué es estimado: [`scripts/ecobici_mapa/README.md`](scripts/ecobici_mapa/README.md). Capturas en [`scripts/ecobici_mapa/screenshots/`](scripts/ecobici_mapa/screenshots/).

## Datos

`data/` no está en el repo, por tamaño. Fuentes, todas públicas:

- **Viajes:** CSV mensuales de [datos abiertos de Ecobici](https://ecobici.cdmx.gob.mx/datos-abiertos/), en `data/ecobici/`. `uv run python -m ecosim.data build-trips` arma el parquet.
- **Fotos del estado de estaciones:** archivo histórico público [`MaxHalford/bike-sharing-history`](https://github.com/MaxHalford/bike-sharing-history). Se baja con `uv run python -m ecosim.data build-snapshots`.
- **Feed en vivo:** <https://gbfs.mex.lyftbikes.com/gbfs/gbfs.json>.
- **Zonas:** AGEB urbanas del Marco Geoestadístico 2024 del INEGI, ya recortadas en `scripts/ecobici_mapa/zonas_ageb.geojson`. `zonas_ageb.py` las regenera.

## Reproducir

```bash
uv run pytest -q                                   # pruebas (requieren data/)
node scripts/ecobici_mapa/test_ui.cjs              # prueba de la app en navegador contra el servidor real
uv run python3 report/figs/numeros_run3.py --check # cada cifra del reporte contra su origen
cd report && pdflatex reporte-caso.tex && bibtex reporte-caso && pdflatex reporte-caso.tex && pdflatex reporte-caso.tex
```

El apéndice del reporte trae el comando que genera cada tabla y cada figura.
