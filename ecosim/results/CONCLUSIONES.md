# Conclusiones — ecosim run 3 (greedy)

Resultados dentro del simulador; no son una promesa operacional ni rutas verificadas.

## Partición y congelamiento

- **Train de validación:** enero–julio de 2025.
- **Validación:** los 31 días válidos de agosto de 2025. Solo esta partición eligió los parámetros.
- **Test rolling:** septiembre–diciembre de 2025, 121 días (2025-09-01 a 2025-12-31). No se corrió ni reportó 2026.
- Topes duros: p95 de Ecobici en todo 2025, 55 visitas por decisión y 17 bicis por visita.

El horizonte congelado es `n=4` tanto para la forma diaria como para la directa. La calibración de λ igualó las visitas medias de Ecobici en agosto (objetivo: 2,033.87 visitas/día), con la rejilla 10–120, interpolación y confirmación; ningún día de test entró en ella.

| brazo | λ (min/visita) | visitas confirmadas | diferencia vs Ecobici | rondas |
|---|---:|---:|---:|---:|
| oraculo_diario | 44.6349 | 2045.32 | +0.56% | 4 |
| ma_diaria | 58.1824 | 2051.55 | +0.87% | 1 |
| lgbm_diario | 59.1603 | 2052.00 | +0.89% | 1 |
| oraculo_directo | 36.2809 | 2045.65 | +0.58% | 1 |
| lgbm_directo | 68.8817 | 2032.16 | -0.08% | 1 |

Todas las confirmaciones quedaron dentro de 1%.

## Tabla principal: test de 121 días

E y F son minutos-estación/día; las demás columnas son promedios diarios.

| brazo | E | F | E+F | visitas | bicis movidas | reubicación al destino |
|---|---:|---:|---:|---:|---:|---:|
| sin_rebalanceo | 169275.9 | 50785.0 | 220060.9 | 0.0 | 0.0 | 0.0 |
| ecobici | 84956.3 | 17178.3 | 102134.6 | 2263.2 | 10275.8 | 0.0 |
| oraculo_diario | 38078.2 | 2793.6 | 40871.8 | 2053.5 | 9908.1 | 25.8 |
| ma_diaria | 53774.3 | 4027.6 | 57801.8 | 2059.8 | 10083.5 | 21.9 |
| lgbm_diario | 54120.3 | 4066.8 | 58187.1 | 2054.3 | 9746.3 | 18.1 |
| oraculo_directo | 30933.5 | 2750.8 | 33684.4 | 2053.7 | 8851.5 | 3.1 |
| lgbm_directo | 53647.2 | 3863.0 | 57510.2 | 2093.5 | 10877.7 | 19.9 |

Las cinco políticas superan a Ecobici en E+F los **121/121 días**. El mejor brazo real de validación, `ma_diaria`, logra 57,801.8 E+F/día: 43.4% menos que Ecobici, con 9.0% menos visitas en test.

## Curvas fuera de muestra: E+F al esfuerzo de Ecobici

Las curvas de λ se calcularon en los 121 días de test solamente para lectura, sin recalibrar λ. Interpolando a 2,263.21 visitas/día de Ecobici:

| brazo | intervalo λ que encierra el esfuerzo | E+F interpolado |
|---|---|---:|
| oraculo_diario | 30.0–44.6349 | 38693.2 |
| ma_diaria | 45.0–58.1824 | 56059.7 |
| lgbm_diario | 45.0–59.1603 | 56163.2 |
| oraculo_directo | 25.0–30.0 | 31712.7 |
| lgbm_directo | 60.0–68.8817 | 55944.0 |

## Sensibilidades en test

Las sensibilidades usan los 121 días y los λ/n congelados. Las de entrega son las principales.

| variante | oraculo_directo E+F | ma_diaria E+F | nota |
|---|---:|---:|---|
| entrega_45 | 29778.2 | 51243.0 | entrega 15 min antes |
| entrega_75 | 37159.9 | 64723.8 | entrega 15 min después |
| pares_sin_regla | 33684.4 | 57801.8 | solo cambia el esfuerzo medido de Ecobici: 4614.7 visitas/día |
| pares_solo_1 | 33684.4 | 57801.8 | Ecobici: 2469.6 visitas/día |
| danadas_feed | 33773.5 | 59054.7 | flujo externo de dañadas: -65.6 / -63.9 bicis/día |

Respecto a la entrega base de 60 min, entrega_45 reduce E+F en 3,906.2 (oráculo directo) y 6,558.8 (media móvil); entrega_75 lo aumenta en 3,475.5 y 6,922.0, respectivamente.

## V1 y regla de destino

La validación V1 cubre los 121 días: replay de Ecobici frente al feed observado. Promedios diarios: E observado 87,470.1 frente a replay 84,956.3 (−2.7%); F observado 17,062.0 frente a replay 17,178.3 (−0.1%).

`reubicaciones_destino` cuenta las bicis que no caben en el receptor y se dejan en la estación con anclaje libre más cercana a ese destino. En test promedia entre 3.1 y 25.8 bicis/día según brazo; no hay devolución al origen.

Los archivos detallados reproducibles están en `tablas.md`, `resultados.csv`, `curvas_seleccion.csv`, `corridas_run3.csv`, `v1_run3.csv` y `frozen.json`.
