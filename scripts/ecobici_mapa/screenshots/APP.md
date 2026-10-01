# App Ecobici · producto final de ecosim (run 2)

Capturas (1600×1000, Chrome headless por CDP contra `server.py` local):

| archivo | qué muestra |
|---|---|
| `01-mapa-en-vivo.png` | Pestaña **Bicis ahora**: las 677 estaciones con el feed GBFS en vivo. |
| `02-replay-decisiones.png` | Pestaña **Replay**, 2025-09-03, mejor brazo real (`daily`), reproduciendo hacia las 08:45: estado por estación, viajes del intervalo, órdenes del asignador (±n; con borde = decidida ahora, efectiva en t + 60), movimientos de Ecobici (E±n), insumos de la decisión y E+F acumulado contra Ecobici y sin rebalanceo. |
| `03-prediccion-en-vivo.png` | Pestaña **Predicción** con `daily` (default), corrida del loop de las 17:30 del 2026-09-29 contra el feed real: qué mover ahora (232 movimientos, efectivos 18:30), estaciones que se vacían/llenan sin mover nada y de dónde sale cada número (fallback de historia y nota del sesgo de `ma` rotulados). |
| `04-prediccion-que-tan-bueno.png` | La misma pestaña con el panel abajo: viajes esperados por bloque y por estación, y **qué tan bueno sería**: 22 corridas ya evaluadas contra el feed (MAE ≈ 2.2 salidas y 2.0 llegadas por estación-hora a 0 h de antelación, hasta 2.4/2.2 a 3 h). |

## Arquitectura y pipeline en vivo

- **Una sola app**: `scripts/ecobici_mapa/server.py` (FastAPI + uvicorn) sirve `index.html` (MapLibre 3D) y hace de proxy al feed GBFS de Ecobici; `uv run python3 server.py` en `http://localhost:8000`.
- **Replay**: `ecosim/replay.py` corre el mismo motor que los resultados (`sim.simulate` + `Asignador` con los parámetros de `frozen.json`) y graba un frame cada 15 min (05:30–00:30) por día y brazo en `data/derived/ecosim/replay/`. El E+F final de cada día y brazo es idéntico a `resultados.csv` (90 de 90 corridas con renglón en `resultados.csv`, tolerancia 0: 15 días de evaluación × 4 brazos y 15 de selección × 2).
- **En vivo** (`ecosim/live.py`, en un hilo de `server.py`): lee el feed **cada 60 s** (lo guarda como registro propio) y **cada 15 min** arma el estado, pronostica salidas/llegadas por estación con **`daily`** por default (el mejor brazo real del run 2: media de 4 semanas del mismo tipo de día, sin corrección; `ma` con corrección intradía también en el selector, `model` enchufable) y corre el **asignador MILP** congelado (λ = 60, retiro σ, L = 60 min, H = 5 h para `daily`, topes 246/h, bodega ±632, ≤ 22 bicis por movimiento).
- **Datos del `ma` en vivo**: los datos abiertos de viajes llegan a agosto 2026, así que se usa un fallback declarado (las 4 semanas que terminan el 2026-08-30, mismo tipo de día). Diciembre 2025 no se lee.
- **"Qué tan bueno sería"**: cada corrida se guarda; cuando pasa el horizonte, se compara contra lo observado en el feed (salidas y llegadas inferidas de los cambios de stock, stock y minutos vacía/llena) y la pestaña muestra el error acumulado. Endpoints: `/api/replay/*` y `/api/live/*`.
