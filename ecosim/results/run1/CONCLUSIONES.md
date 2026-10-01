# ecosim — conclusiones (15 días, 05:30–12:30)

Plan: `docs/planner/plans/2026-09-28-ecosim.md`. Reproducir con `uv run python -m ecosim.run --all`
(~50 min; una corrida desde cero reproduce idénticas las corridas hechas por pasos). El detalle está
en `tablas.md` y los números por día y brazo en `resultados.csv`. E y F son minutos-estación vacíos o
llenos por día, sin las 5 estaciones-día fuera de servicio.

## Panorama

1. **El simulador reproduce las llenas, pero se queda corto en las vacías (V1: no).** Con las órdenes
   de Ecobici, E sale 18.5% abajo de lo observado en GBFS; F, 4%. La causa: el plan fija las bicis
   dañadas a las 05:30, pero en la realidad cambian durante la mañana. El 80% de los minutos vacíos
   observados son estaciones con 0 disponibles y alguna dañada, que en el simulador sí se pueden usar.
   El sesgo afecta a todos los brazos y hace optimistas todos los E. No se midió si los afecta en la
   misma magnitud: pesa más donde hay más estaciones vacías.
2. **Con pronóstico perfecto, el asignador baja E+F 77% contra no hacer nada y 52% contra Ecobici,
   con 22% de sus movimientos.** No llega a ~0 (ver V2).
3. **Con pronósticos reales (`ma`, `model`, `daily`), el pipeline funciona de punta a punta y gana a
   Ecobici los 15 de 15 días**, con ~1/3 de sus movimientos (18.8k contra 34.3k). Se sostiene con tope
   de bodega 0: la bodega aplicada queda en ≈ +180 en promedio, aunque todavía > 180 en 6 de 15 días
   (máximo 346), y E+F en 19.7k.
4. **`model` no le gana a `ma`.** Entre `ma` y el pronóstico perfecto hay ~5k minutos-estación por
   día: es el margen para el siguiente run.

La ventaja sobre Ecobici se mide *dentro* del simulador y con información asimétrica: el asignador ve
el estado exacto; lo de Ecobici se infiere del feed. Es evidencia de que el enfoque funciona, no una
cifra de ahorro en la calle.

## V1 — simulador contra GBFS: **NO** en E, sí en F

| replay de Ecobici | \|dif\| media E | \|dif\| media F | MAE stock (bicis) | desvíos sal./lleg. por día |
|---|---|---|---|---|
| t0, stock (principal) | **18.5%** (28,087 contra 34,878; 15/15 días abajo) | **4.1%** | 0.68 | 140 / 66 |
| t1 | 13.9% | 5.3% | 0.51 | 458 / 206 |
| solo disponibles | **7.2%** | 15.4% | 0.48 | 102 / 74 |

- El criterio es ≤ 10% medio por día. No es grave (< 25%), así que se siguió con los brazos.
- **Diagnóstico.** "Solo disponibles" arregla E y rompe F: es la firma de fijar las dañadas.
  - En GBFS, las dañadas pasan de 1,370 a las 05:30 a un máximo diario medio de 1,616, y bajan a 1,254
    al cierre (el 09-03, de 426 a 1,142: E −52%).
  - El desvío crece con la mañana y se concentra en estaciones cuyo "vacío" observado tiene dañadas
    (`v1/v1_diag_*.csv`).
  - Muestrear el simulador con el mismo escalón que GBFS da el mismo desvío, así que no es el método
    de medición.
- Lo observado aquí es `ecobici_observado.csv` sin las estaciones fuera de servicio: esa es toda la
  diferencia (hasta 43 min de E y 78 de F por día).
- Desvíos del replay: 140 salidas por día de ~19,260 viajes (0.7%).

## Parámetros

- tope_hora = **512** movimientos/h (p95 de medición) y tope_bodega = **180** (|A − R| medio 180.76);
  μ = 1.
- **λ = 60** por Kneedle sobre el oracle (bloque 60, H = 2, L = 60; ver `lambda_codo.png`).
  - Es casi empate con λ = 30 (0.693 contra 0.681). Si se quita λ = 240, que no mueve nada, Kneedle
    elige λ = 30, que da 10.2k en vez de 16.6k.
  - La curva no es monótona: λ = 0 (10.2k) queda peor que λ = 5 (7.5k).

## Brazos (media por día)

| brazo | mejor (bloque, H)* | E+F L=60 | movimientos | gana a Ecobici | L=45 | L=30 |
|---|---|---|---|---|---|---|
| no hacer nada | — | 72,477 | 0 | 0/15 | | |
| Ecobici (replay t0) | — | 34,325 | 2,042 (1,155 sin pares ±1) | — | | |
| oracle | 60, H3 | 13,798 | 509 | 15/15 | 13,033 | **11,901** |
| ma | 60, H3 | 18,822 | 646 | 15/15 | 16,917 | **14,721** |
| model | 60, H3 | 18,888 | 648 | 15/15 | 16,838 | 14,751 |
| daily | 30 o 60, H3 | 19,647 | 687 | 15/15 | | |

\* Elegido **in-sample**. Con H = 1 todo queda en 50–54k: con λ = 60 y una hora de horizonte, una
estación ahorra a lo más 60 min y ningún movimiento paga. Los bloques de 30 min no ayudan. Hubo 0
fallbacks del solver; ~0.3 s por decisión.

## V2 — el oracle no llega a ~0, y se sabe por qué

Oracle (bloque 60, H = 2, L = 60): **16,623** contra 72,477 sin rebalanceo (−77%) y 34,325 de Ecobici
(−52%), con **459 movimientos** (22% de los de Ecobici). Lo que falta:

1. **Primera hora intocable.** Con L = 60 la primera orden llega a las 06:30. El bloque 05:30–06:30
   suma 3,141 (19%); con L = 30 baja a 2,494.
2. **λ.** Con λ = 5 el oracle da 7,479 (−55%), con 1,460 movimientos.
3. **Los topes no pesan con λ = 60.**
   - Sin tope por hora da 16,610; sin ningún tope, 16,603; con los topes 789 o 280, ~16.6k.
   - Con λ = 0 el tope *protege*: sin él, 11,547 movimientos al día y 35,012 de E+F.
   - Hipótesis: el sustituto tiene tramos planos y el solver pone metas en el borde, así que sobre-mueve.
4. **Lead time.** Bajar L de 60 a 30 mejora (13.8k → 11.9k con H = 3). Los bloques de 30 min no.

## Dónde falla (mejor brazo real: `ma`, bloque 60, H = 3, L = 30; `donde_falla.csv`)

- **Hubs compuestos a las 07:30** (273-274, 268-269, 271-272, 266-267, 264-275), con hasta ~70
  salidas por hora.
  - Falla también el oracle (540 contra 621 de `ma` en 273-274), pero **Ecobici no** (71).
  - No es el pronóstico (878 salidas pronosticadas contra 1,065 reales). Una meta cada 15 min con
    λ = 60 no alcanza para surtir sin parar. Hipótesis: recarga continua o zonas valet de Ecobici.
  - GBFS vio vacías estas celdas en 5 a 11 de los 15 días: la demanda real probablemente es mayor.
- **Estado de las 05:30** (176, 024, 463, 459): empiezan vacías o llenas y nadie llega a tiempo.
- **014 llena a las 08:30** (~68 llegadas por hora): Ecobici la vacía (55); nosotros no (398).

## Correcciones a otros módulos y avisos

- **`data.initial_state`** (commit b88562a, con test): el stock se lleva a las 05:30 con la hora del
  feed (commit − 30 s), como `medicion`. Cambia 89 bicis en los 15 días.
  - `simulador/{replay,baseline}_15dias.csv` son de antes de esta corrección; quedan superados por
    `v1/v1_por_dia.csv`.
- **La bodega aplicada rompe el tope de 180 en todos los brazos que rebalancean** (ma +324 por día,
  máximo 508; oracle H3 +205). La causa son los retiros recortados: el asignador cuenta sus pendientes
  al tamaño pedido. Con tope_bodega = 0 (`sens_bodega0`), la bodega aplicada queda en ≈ +180 (ma; todavía
  > 180 en 6 de 15 días, máximo 346) y +96 (oracle):
  - ma: E+F sube a **19,658 (+4.4%)**;
  - oracle: sube a **14,295 (+3.6%)**;
  - ambos siguen ganando a Ecobici 15/15.

  Así que ~4% de la ventaja viene de bicis de bodega por encima del tope.
- **A − R de Ecobici es negativo** (−185 por día en estos días).
  - No lo explican las llegadas a estaciones desconocidas (~1 por día).
  - −164 viene de cambios en dañadas: suben en la mañana y cierran ~115 por debajo de las 05:30.
    Probablemente es retiro al taller.
  - El replay aplica como disponibles las dañadas que Ecobici recoge o deja.
- Supuestos vigentes: dañadas fijas, demanda censurada, desvíos sin tiempo de caminata, medición
  inferida (43% de los movimientos de Ecobici son pares ±1 que se deshacen).

## Siguientes pasos

1. **Dañadas** como evento exógeno en el simulador, igual en todos los brazos (lo que más pesa en V1).
2. **Tope de bodega sobre lo aplicado**, no sobre lo pedido.
3. **λ por hora de horizonte**; validar fuera de muestra H, bloque, L y λ (λ = 30 es casi empate).
4. **Hubs y primera hora:** varias recargas por hora en estaciones de alto flujo; decidir antes de
   las 05:30 o con un lead menor.
5. **Pronóstico** (siguiente run): ~5k minutos-estación por día entre `ma` y el oracle.
