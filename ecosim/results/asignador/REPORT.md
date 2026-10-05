# Validación del asignador — run 3

Fixture reproducible: 700 estaciones, flujo Poisson independiente del pronóstico; el simulador de flujo recorta a [0, K] y aplica las dos piernas en t+15 y t+60. Se comparan planes MILP, sin movimiento, fracciones y alternativas aleatorias (estas últimas sólo para validar el costo, no son decisiones factibles).

Spearman entre costo incremental pronosticado y E+F real: mediana 0.964 en 12 grupos definidos.

| n | mediana Spearman |
|---:|---:|
| 2 | 0.973 |
| 4 | 0.964 |
| 6 | 0.955 |

| n | λ | decisiones | visitas mediana | mediana s | p95 s | máximo s | límite 10 s | fallback |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 10 | 4 | 67.0 | 0.772 | 8.660 | 10.002 | 1 | 0 |
| 2 | 30 | 4 | 67.0 | 2.109 | 8.986 | 10.002 | 1 | 0 |
| 2 | 60 | 4 | 67.0 | 1.597 | 8.813 | 10.001 | 1 | 0 |
| 4 | 10 | 4 | 67.0 | 0.375 | 0.483 | 0.494 | 0 | 0 |
| 4 | 30 | 4 | 67.0 | 0.431 | 1.324 | 1.464 | 0 | 0 |
| 4 | 60 | 4 | 67.0 | 0.415 | 0.662 | 0.701 | 0 | 0 |
| 6 | 10 | 4 | 67.0 | 0.302 | 0.512 | 0.523 | 0 | 0 |
| 6 | 30 | 4 | 67.0 | 0.292 | 0.455 | 0.459 | 0 | 0 |
| 6 | 60 | 4 | 67.0 | 0.289 | 0.441 | 0.444 | 0 | 0 |

λ=10: 1/12 decisiones (8.3%) llegaron al límite. No se aumentó el límite de 10 s.

Supuesto: las dañadas presentes en t ocupan anclajes durante la proyección; eventos futuros no son visibles. Las órdenes pendientes se aplican en su hora y se recortan en la proyección; no se presupone capacidad de bodega.
