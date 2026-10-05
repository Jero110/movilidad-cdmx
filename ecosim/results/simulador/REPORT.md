# Simulador run 3 — retry de revisión y V1

## Motor y conservación

`sim.simulate` valida después de **cada evento** la identidad:

```
Σ(disponibles + dañadas en estaciones) + viajes en tránsito + camionetas
+ llegadas a estaciones desconocidas − entradas desde estaciones desconocidas
− flujo_externo_replay − flujo_externo_danadas = flota inicial + viajes ya en tránsito al abrir
```

Las órdenes de política equilibran cada decisión: pickup a `t+15`, delivery a `t+60` (sensibilidad 45/75). Si no se recoge todo, las entregas se reducen proporcionalmente al pedido mediante resto mayor; el desempate es `short_name` y posición original. Un sobrante al entregar vuelve al origen o a la estación con anclaje más cercana. No queda stock en camioneta al cierre. Ecobici se aplica en t1 como dato, sin topes ni demora; **`replay_external` es la suma firmada de sus movimientos disponibles efectivamente aplicados**, no carga de camioneta. En el brazo oficial `onsite`, `damage_external=0` y `replay_neto=replay_external`. Las políticas no reciben `replay_external`.

La sensibilidad `damage_variant="feed"` usa los deltas del feed **independientemente del brazo**: una subida de dañadas coincidente con delta de stock positivo mete hasta `min(n, delta)` bicis dañadas desde fuera; una bajada coincidente con delta negativo saca hasta `min(n, abs(delta))` dañadas. El resto cambia de etiqueta en sitio. Aplicación física recorta por anclajes/dañadas existentes y cuenta unidades no aplicables. Esos flujos exógenos se guardan en `damage_external` y entran en la conservación para baseline, políticas y replay. En el replay se resta la parte ya incorporada como dañada del movimiento disponible para no contarla dos veces. Para `simulate` con fixtures hay que pasar `feed_moves` (`EcobiciMoves`); `run_day` los carga automáticamente desde `data/derived/ecosim/ecobici_moves.parquet`.

El neto disponible para integración está en `DayResult.extra["replay_neto"]`; el neto completo de sensibilidad es `extra["neto_externo"]`. Para los 15 días también está en `ecosim/results/v1/run3_replay_neto.csv`.

## Neto del replay: 15 días de selección de agosto 2025

Neto **aplicado** entre 05:00 y 00:30, después de recortes físicos; flota = disponibles + dañadas + viajes iniciales. `run3_replay_neto.csv` contiene además el contrafactual por día.

| Día | Neto Ecobici (bicis) | Flota inicial | % flota |
|---|---:|---:|---:|
| 08-01 | +105 | 7,242 | +1.45% |
| 08-04 | +219 | 7,250 | +3.02% |
| 08-06 | +137 | 7,293 | +1.88% |
| 08-08 | +34 | 7,238 | +0.47% |
| 08-09 | +2 | 7,057 | +0.03% |
| 08-11 | −9 | 7,254 | −0.12% |
| 08-12 | +311 | 7,202 | +4.32% |
| 08-13 | +83 | 7,221 | +1.15% |
| 08-16 | +74 | 6,897 | +1.07% |
| 08-18 | +49 | 7,003 | +0.70% |
| 08-23 | −53 | 7,140 | −0.74% |
| 08-26 | +188 | 7,105 | +2.65% |
| 08-27 | +130 | 7,096 | +1.83% |
| 08-28 | +52 | 7,119 | +0.73% |
| 08-31 | −291 | 7,297 | −3.99% |

Total neto +1,031; media +68.7 bicis/día (**+0.96%** de la flota media del día), mínimo −291, máximo +311, 12/15 días positivos. El replay inferido no tiene garantía de balance en esta ventana: Ecobici puede meter o sacar bicis desde fuera de las estaciones; **los brazos de política no pueden hacerlo**. Donde el neto es positivo, Ecobici tiene más bicis y la comparación de vacías/EF queda sesgada *a favor de Ecobici*; es conservadora para la afirmación de mejora de las políticas. Más flota puede también agravar llenas, así que el signo de la diferencia en F no está garantizado.

### Tamaño de la sensibilidad (solo diagnóstico; NO sustituye el replay oficial)

Se contrastan dos reglas deterministas, reproducibles con `validar.py`:

| 15 días, min-estación | Replay oficial | Neto diario reducido | Neto cero tras cada foto |
|---|---:|---:|---:|
| E | 1,237,431 | 1,257,407 | 1,400,043 |
| F | 289,246 | 277,838 | 389,213 |
| E+F | 1,526,677 | **1,535,245** (+8,568; +0.56%) | **1,789,256** (+262,579; +17.2%) |

**Neto diario reducido:** se distribuye el recorte de `abs(replay_neto)` entre las órdenes originales del mismo signo proporcionalmente al tamaño, con desempate por orden del archivo. No se alteran otros movimientos. Es la lectura más localizada del efecto; no garantiza neto aplicado cero porque cambian los recortes físicos y desvíos (residuo agregado +179 bicis, por día detallado en CSV). **Neto cero tras cada foto:** después de aplicar las órdenes de t1 se revierte su neto aplicado en ese instante, primero en estaciones con movimiento del mismo signo, luego por ID de estación; se mantiene la capacidad y el neto externo vuelve a cero. Requiere **36,199 bicis de correcciones brutas** en 15 días, por lo que su +17.2% de E+F mezcla neutralización y un rebalanceo nuevo: no se puede atribuir al neto diario +1,031. En ningún caso se fuerza el brazo oficial a cuadrar ni se interpreta el contrafactual como estimación causal.

## V1: replay contra observados oficiales de `medicion3`

Ejecutar (semáforo compartido):

```sh
uv run python -m ecosim.results.v1.validar
```

El total observado se **lee** de `ecosim/results/medicion/ecobici_observado.csv` de `medicion3`: integra duraciones exactas entre commits y reporta blancos/sin-observación por separado. `validar.py` reconstruye una malla de un minuto **solo** para etiquetar contextos estación/franja y extraer ejemplos; su diferencia con el total oficial se muestra como fila independiente, sin atribuirla falsamente a una franja. Los CSV de salida son `run3_v1_por_dia.csv`, `run3_v1_causas.csv`, `run3_v1_estaciones.csv`, `run3_v1_hora_movimiento.csv` y `run3_replay_neto.csv`.

| 15 días, min-estación | Replay | Observado medición3 | Replay − observado | Relativo |
|---|---:|---:|---:|---:|
| E | 1,237,431 | 1,261,291.9 | −23,860.9 | −1.89% |
| F | 289,246 | 283,232.2 | +6,013.8 | +2.12% |

Los días 08-28 y 08-31 tienen ΔE de −5,609.3 y −6,194.1 respectivamente; el agregado no oculta estos errores locales. Clasificación **contextual, no causal** de minutos firmados replay − malla por minuto, prioridad: foto vieja (>30 min)/blanca, desvío dentro de 30 min en origen/destino, par eliminado, dañada no aplicable, residual del escalón:

| Franja | Contexto | ΔE | ΔF |
|---|---|---:|---:|
| 05–12 | hueco/blanco | −11 | −11 |
| 05–12 | desvío | +13,002 | +5,979 |
| 05–12 | par | −579 | −115 |
| 05–12 | dañada no aplicable | −1,766 | +1 |
| 05–12 | resto/escalón | −10,000 | −3,624 |
| 12–18 | hueco/blanco | −20 | −20 |
| 12–18 | desvío | +13,880 | +3,112 |
| 12–18 | par | −1,775 | −817 |
| 12–18 | dañada no aplicable | −1,840 | +15 |
| 12–18 | resto/escalón | −28,591 | −7,483 |
| 18–00:30 | hueco/blanco | +11,939 | +5,029 |
| 18–00:30 | desvío | +30,172 | +13,937 |
| 18–00:30 | par | −865 | +165 |
| 18–00:30 | dañada no aplicable | −1,972 | −25 |
| 18–00:30 | resto/escalón | −47,753 | −11,490 |
| Sin franja: definición oficial vs malla minuto | ajuste contable | +2,318.1 | +1,360.8 |

La tabla incluyendo el ajuste contable suma −23,860.9 E y +6,013.8 F. Hay 24,015 desvíos de salida y 10,994 de llegada; 2,896 unidades de dañadas no aplicables y 578,835 celdas estación-minuto con foto de antigüedad >30 min. Ejemplos de E nocturna: estación 659 (08-28, −301 min), estación 674 (08-28, +224) y estación 026 (08-31, −197), en `run3_v1_estaciones.csv`. El contrafactual de aplicar cada movimiento al inicio del intervalo (t0) en vez del final (t1) cambia E en −155,247 y F en −1,725 min-estación: **no** se suma a la tabla, es otra corrida.

**Explicación para el reporte:** el replay da 1.89% menos E y 2.12% más F que el observado oficial. El feed sostiene el último estado observado en escalones de duración variable (con huecos y desfase de ~30 s), mientras el simulador ejecuta viajes a su segundo y deltas al final del intervalo. Redirigir viajes, excluir pares exactos y recortar conversiones de dañadas también altera la trayectoria. Los desajustes se prolongan especialmente de tarde/noche cuando envejece la foto. No se ajustó el motor para hacer coincidir E/F.

## Advertencias

- Si ninguna estación tiene bicicleta o anclaje, no hay solución física para el próximo viaje sin crear bicis: el motor falla explícitamente.
- El promedio de km por desvío omite distancias `NaN` de estaciones sin coordenadas. Capacidad fija desde 05:00 en simulador vs cambios de capacidad del feed pueden producir diferencias adicionales.
- Pruebas: `uv run pytest tests/ecosim/test_sim.py -q`; CLI real: `uv run python -m ecosim.sim --day 2025-08-13 --arm baseline` y `--arm ecobici`.
