# ecosim run 2 — conclusiones (día completo 05:30–00:30, 15 días de evaluación)

Plan: `docs/planner/plans/2026-09-28-ecosim2.md`. Reproducir: `uv run python -m ecosim.run --all` (~85 min, 8
procesos de 1 thread). Cada una de las 1,440 corridas de `resultados.csv` se calculó dos veces con el mismo
resultado: una corrida por pasos, `--all` desde cero y 48 filas recalculadas aparte. Esa verificación no cubre
`v1/*`. Todo lo elegible se eligió en 15 días de **selección**; los números son de los 15 días de
**evaluación** del run 1. E y F son minutos-estación vacíos o llenos por día, sin las estaciones fuera de
servicio. Detalle en `tablas.md` y `CONCLUSIONES-apendice.md`.

## Panorama

1. **Con un pronóstico emitido una sola vez a las 05:30 (`daily`), el asignador baja E+F 55% contra
   Ecobici en el día completo.** Gana los 15 días, con casi los mismos movimientos (2,502 contra 2,416) y
   12% más bicis movidas. Hay sesgos en los dos sentidos:
   - **a favor de Ecobici:** su replay en t0 le baja 9% el E+F (contra t1), y bodega y topes solo se
     aplican a las políticas;
   - **en su contra:** quitar los pares ±1 le sube 4%, y la política ve el estado exacto.
2. **Re-pronosticar durante el día no ayuda.** `ma` y `model` re-emitidos (mejor: cada 15 min, h = 3)
   quedan 14% peor que `daily`, en selección y en evaluación. Coincide con la exactitud de `pronostico2`.
   Parte de la brecha es de bodega: `ma`/`model` la saturan y recortan ~600 bicis por día (`daily`, 203).
   El reviewer lo midió en 4 días con bodega ilimitada: `ma` baja 7.9% y `daily` 5.1%, y `ma` sigue ~7%
   peor. La bodega explica ~1/3 de la brecha.
3. **El pronóstico perfecto (oracle) baja E+F 74% contra Ecobici.** Entre `daily` y el oracle quedan ~20k
   minutos-estación por día: es el margen del siguiente paso.
4. **V1: el simulador sigue a GBFS en la mañana, pero subestima las vacías en la noche.** El sesgo de
   horario del replay favorece a Ecobici. El resto (−5% en E con t1) es del simulador y afecta a todos los
   brazos, sin dirección probada sobre la brecha.

Todo es *dentro del simulador*, con información asimétrica. Es evidencia de que el enfoque funciona, no
una cifra de ahorro en la calle.

## V1 — simulador contra GBFS: **mañana sí; día completo no en E** (no grave, ≤ 25%)

| replay (dif. media absoluta por día) | E día | F día | mañana E / F | tarde E / F | noche E / F |
|---|---|---|---|---|---|
| **principal** (`rebal`, t0, sin ±1, dañadas dinámicas) | **14.1%** (15/15 abajo) | **5.0%** | **6.6 / 2.8%** ✔ | 12.0 / 10.0% | 23.4 / 12.2% |
| t1 | 5.3% | 6.0% | 1.7 / 4.7% | 4.6 / 14.2% | 10.0 / 13.8% |
| con ±1 | 18.1% | 6.8% | 8.5 / 4.2% | 17.2 / 15.2% | 28.4 / 14.6% |
| dañadas fijas (`stock`, método del run 1) | 22.5% | 7.8% | 18.5 / 4.1% | 23.9 / 12.4% | 24.2 / 15.0% |

- **Las dañadas dinámicas arreglan la falla del run 1:** en la mañana, E pasa de 18.5% a 6.6%. El MAE de
  dañadas es 0.04 por estación-snapshot, contra 0.80 con dañadas fijas.
- **Noche:** el 42% del faltante de E cae en los bloques de 17:30 a 21:30. Desde las 18:00 el recolector
  deja huecos de 20–45 min entre snapshots (15 min el resto del día), y el replay en t0 aplica cada
  movimiento al inicio del hueco. Con t1, E queda en −5.3%. Estaciones: 086, 474, 261, 463 y 019
  (`v1/v1_diag_*.csv`).

## Lo que se eligió (solo selección) — `frozen.json`

| elección | valor | evidencia en selección |
|---|---|---|
| topes (de `medicion2`) | 246 por hora, bodega 632, 22 por movimiento; μ = 1, L = 60 | p95 sin ±1; ⌈\|A − R\| medio⌉ |
| cota de retiro | ⌊p − σ⌋ | gana en 84 de 105 pares (λ, día), empata en 15; 69.3k contra 72.2k |
| λ | 60 (Kneedle sobre el oracle, f60, H = 2) | el codo cae en la parte cara: λ = 5 da 13.1k contra 33.9k |
| h_max | 6 | oracle mejora hasta h = 5 (h = 6: +0.5%); `ma` hasta h = 3 (h = 4: +1.8%) |
| mejor (f, h) | oracle h 5; `daily` h 5; `model` f 15 h 3; `ma` f 15 h 3 | 21.5k; 39.5k; 45.3k; 45.4k |
| mejor brazo real | `daily` | |

## Brazos en evaluación (media por día, L = 60, todo congelado)

| brazo | E+F | mañana | tarde | noche | movimientos | bicis movidas | gana a Ecobici | recorte bodega |
|---|---|---|---|---|---|---|---|---|
| no hacer nada | 250,827 | 74,580 | 74,558 | 101,689 | 0 | 0 | 0/15 | — |
| **Ecobici** (replay principal) | **102,214** | 38,611 | 28,456 | 35,148 | 2,416 | 11,067 | — | — |
| oracle (h 5) | **26,561** (−74%) | 12,015 | 7,769 | 6,777 | 1,970 | 10,624 | 15/15 | 83 |
| **daily (h 5)** | **46,268** (−55%) | 16,376 | 15,119 | 14,773 | 2,502 | 12,375 | 15/15 | 203 |
| ma (f 15, h 3) | 52,952 (−48%) | 18,993 | 17,467 | 16,492 | 2,947 | 16,740 | 15/15 | 639 |
| model (f 15, h 3) | 53,126 (−48%) | 18,690 | 17,016 | 17,420 | 2,948 | 16,707 | 15/15 | 596 |

- Por día, `daily` gana entre 47% y 62%. Con L = 30: oracle 22,056, `model` 39,253, `ma` 39,509.
- En las 1,380 corridas de política: 0 fallbacks, 0 decisiones no óptimas, recorte por movimiento 0, y la
  bodega nunca sale de ±632.
- Tiempo por decisión en los brazos principales: máximo 3.9 s (el plan pide ≤ 5 s). Todas las decisiones de
  más de 60 s son de λ = 5; la peor tarda 333 s.

## V2 — el oracle no llega a ~0

−89% contra no hacer nada y −74% contra Ecobici, con 82% de sus movimientos y 96% de sus bicis. Los
26.6k que quedan se explican así:
- **primera hora intocable** (L = 60): 3,157, el 12%;
- **λ** (con λ = 5): 19.8k, −25%;
- **lead time** (L = 30): 22.1k, −17%;
- **bodega ilimitada**: 23.0k, −13%;
- **dañadas fijas**: 21.7k (irreal);
- **tope por movimiento** (sin tope): 25.0k, −6%.

El resto son hubs donde una o dos visitas por hora no alcanzan.

## Sensibilidades (evaluación; E+F oracle / `daily`; todas ganan a Ecobici 15/15)

| cambio | oracle | daily |
|---|---|---|
| principal | 26,561 | 46,268 |
| tope por movimiento 14 / 42 / sin tope | 29,939 / 25,291 / 24,996 | 48,018 / 45,919 / 45,546 |
| tope por hora con ±1 (474) | 26,464 | 45,631 |
| dañadas fijas | 21,701 | 41,473 |
| bodega ilimitada / con taller (74) | 23,035 / 33,050 | 40,680 / 55,286 |
| μ = 0 / cota ⌊p⌋ | 26,190 / 29,073 | 46,936 / 48,752 |
| λ = 5 (menor E+F en selección) | 19,816 | 45,465 |

- El tope por hora no aprieta (< 1.5%). Lo que más mueve a las políticas es la **bodega**: con el taller
  dentro, `daily` sube 19%.
- **λ = 5 cuesta cómputo:** hasta 66 s por decisión en evaluación y 333 s en selección, contra ≤ 5 s del
  plan.

## Dónde falla, run 1 y siguientes pasos (detalle en `CONCLUSIONES-apendice.md`)

- **Dónde falla:** los 20 peores estación × hora son solo 1.6% del E+F de `daily`. Destacan:
  - los hubs de 07:30–08:30 del run 1 (273-274, 271-272, 268-269), donde el oracle falla casi igual y
    Ecobici no (zonas valet);
  - los vaciados de 17:30–18:30 (261, 014, 022).
- **Contra el run 1 (misma mañana):** con su método se reproducen exactos 72,477 y 34,325. En el run 2:
  - Ecobici justo: 38,611 (+12%);
  - oracle: 12,015 (−13%);
  - `daily`: 16,376 (−17%), pasa de último a mejor brazo real;
  - `ma`/`model`: ≈ igual.

  La E subestimada 18.5% del run 1 era por las dañadas fijas.
- **Siguientes pasos:**
  1. factor intradía global de pronóstico;
  2. regla fuera de muestra para λ que respete ≤ 5 s por decisión;
  3. replay con la hora dentro del hueco de 18:00–21:30;
  4. separar el regreso de reparadas para calibrar la bodega;
  5. recargas múltiples en zonas valet;
  6. rutas y camiones.
