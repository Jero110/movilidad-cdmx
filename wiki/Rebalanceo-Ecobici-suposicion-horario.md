---
tags: [ecobici, rebalanceo, horario, investigacion, correccion]
created: 2026-09-21
summary: Medición directa (sin ventana horaria asumida) de cuándo ocurren las discontinuidades de bicicleta detectadas en el histórico OD. Corrige la suposición implícita de "rebalanceo = fenómeno nocturno" que usaban rebalance_routes.py y Rebalanceo-Ecobici-modelos-optimizacion-RL.md.
---

# Rebalanceo Ecobici — ¿es realmente un fenómeno nocturno?

## Pregunta que originó esta página

El pipeline de detección de reubicaciones (`analysis/rebalance_routes.py`)
filtraba explícitamente a una ventana nocturna fija (última vez vista
18:00–00:35, reaparece 05:00–10:00), asumiendo que el rebalanceo es
overnight. Esa suposición nunca se había medido contra el dato completo —
se eligió por analogía con la práctica común en otras ciudades de
bike-sharing, no por evidencia propia de Ecobici.

## Método

Mismo criterio de detección que ya usaba el pipeline: una bici termina un
viaje en la estación A; su siguiente viaje (misma bici, cualquier usuario)
empieza en la estación B ≠ A; si la distancia A→B supera 300 m, se cuenta
como salto/reubicación. La diferencia es que aquí **no se restringe ningún
horario**: se corrió sobre los 20 CSV mensuales disponibles (2025-01 a
2026-08), sin excluir ninguna hora del día.

Se registró, por cada salto: hora en que se vio por última vez (`arr_dt`,
llegada del viaje anterior), hora en que se volvió a ver (`next_ret_dt`,
salida del siguiente viaje), y si esas dos fechas caen en el mismo día
calendario o cruzan a otro.

## Resultado — literal, sin categorías de horario impuestas

2,781,492 saltos detectados en total sobre el año completo.

| | Casos | % del total |
|---|---:|---:|
| **Mismo día calendario** (dejada y tomada el mismo día) | 1,963,230 | **70.6%** |
| **Cruza a exactamente un día después** | 725,620 | **26.1%** |
| Cruza 2+ días | 92,642 | 3.3% |

Para el subconjunto que cruza a un día calendario después (26.1%), la
distribución de horas sí tiene forma overnight clara:

- Hora en que se dejó: pico en 19h (111,249), 20h (103,226), 21h (84,785) —
  concentrado en la noche.
- Hora en que se tomó de nuevo al día siguiente: pico en 7am (170,516), 8am
  (133,570), 6am (98,288) — concentrado en la madrugada/mañana temprano.

Para el subconjunto del mismo día calendario (70.6%), la distribución no
tiene forma nocturna en absoluto:

- Hora en que se dejó: pico en 8am (334,742) y 9am (241,594), más un pico
  secundario a las 18h (179,010) — horas pico de viaje normal.
- Gap mediano entre dejarla y volver a verla: **2.3 horas**, no toda la
  noche.

## Interpretación — qué se puede afirmar y qué no

**No se puede afirmar** que la mayoría del patrón "mismo día" sea rebalanceo
real por camioneta. Un gap de ~2h en hora pico, repetido más de un millón de
veces al año, es mucho más consistente con relocación peatonal del usuario
(camina la bici unas cuadras), matching entre estaciones compuestas/cercanas,
o error de registro, que con logística vehicular — la única cifra operativa
pública conocida (La Silla Rota, 2026-08-18: camionetas redistribuidoras,
2-3 a 7-8 recorridos **por turno**) es órdenes de magnitud menor que el
volumen medido aquí.

**Tampoco se puede afirmar** que el rebalanceo sea exclusivamente nocturno.
La palabra "turno" en esa misma nota de prensa es compatible con operación
distribuida durante el día, no solo con un evento nocturno único, y no hay
ninguna fuente pública (contrato, anexo técnico, entrevista oficial) que
confirme el horario real de operación.

**Lo que sí se puede afirmar:** el subconjunto que cruza de un día calendario
al siguiente, con gap ~9-10h y la forma "dejada de noche, recogida a primera
hora", es la señal más creíble de traslado operativo genuino disponible en
este dataset — es el mismo subconjunto que `rebalance_routes.py` aislaba
originalmente, solo que ahora se reporta junto al resto en vez de
descartar todo lo demás.

## Contradice/corrige explícitamente

> [!warning] Corrige una suposición no verificada
> [[Rebalanceo-Ecobici-modelos-optimizacion-RL]] recomienda static/overnight
> como punto de partida ("Empezar con static/overnight simulado y luego
> dynamic rolling-horizon intradía"), justificado por practicidad
> metodológica — sigue siendo una recomendación razonable como *punto de
> partida de diseño*, pero esta página muestra que no hay evidencia de que
> corresponda a cómo opera Ecobici realmente. Cualquier afirmación tipo "el
> rebalanceo real ocurre de noche" debe leerse como supuesto de diseño, no
> como hallazgo verificado.
>
> `analysis/rebalance_routes.py` y la pestaña **Rebalanceo** de la app
> (`scripts/ecobici_mapa/`) ya se actualizaron para dejar de filtrar por
> ventana nocturna fija: ahora procesan un día completo y clasifican cada
> salto por `days_crossed` (0 = mismo día, 1 = overnight), mostrando ambos
> en vez de asumir solo uno. Ver `scripts/ecobici_mapa/README.md`.

## Implicación para el simulador de rebalanceo planeado

Para el diseño de "vector de estado al inicio del día + asignación de
rebalanceo una vez + simular el día + RL sobre demanda censurada corregida"
(ver conversación de diseño, no documentada aparte): esta medición no
bloquea ese plan, porque el entrenamiento corre dentro de un simulador
propio, no depende de replicar la operación real. Sí limita qué se puede
afirmar al comparar contra el sistema observado:

- Se puede afirmar: "el sistema observado hoy tiene X estaciones vacías/Y
  llenas" (medible directo de GBFS/`occupancy_15min.parquet`, sin ninguna
  suposición de horario).
- Se puede afirmar: "mi política overnight simulada, evaluada contra un
  baseline de no-intervención **también simulado**, reduce ese número a
  X'/Y' en mi simulador".
- No se puede afirmar: que esa mejora sea "lo que se ganaría reemplazando
  la operación real de Ecobici" — el estado observado real ya trae mezclado
  cualquier rebalanceo real que haya ocurrido (sea overnight, intradía, o
  ambos), así que compararse contra el observado no aísla el valor
  incremental de la política propuesta.

## Fuentes relacionadas en el vault

- [[Rebalanceo-Ecobici-operacion-actual]] — evidencia de prensa sobre
  camionetas redistribuidoras "por turno" (La Silla Rota, 2026-08-18); es
  el único indicio externo, indirecto, de que podría haber operación
  diurna.
- [[Rebalanceo-Ecobici-modelos-optimizacion-RL]] — diseño metodológico que
  recomienda static/overnight como punto de partida; esta página aclara que
  esa elección es de diseño, no evidencia empírica de la operación real.
- `analysis/rebalance_routes.py` — pipeline de detección, actualizado para
  no asumir ventana horaria.
