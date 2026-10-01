# ecosim / asignador (run 2)

Código: `ecosim/asignador.py` (clase `Asignador`, cumple `contracts.Policy`). Tests: `tests/ecosim/test_asignador.py` (26).
Plan: `docs/planner/plans/2026-09-28-ecosim2.md`, subtask `asignador2`. La formulación base es la del run 1 (regla 1.6), descrita en el docstring del módulo. El reporte del run 1 está en el historial de git (tag `ecosim-run1`).

Comandos (todos con el semáforo `heavy.py`):
- `uv run python -m ecosim.asignador validate` escribe `validation_plans.csv`, `validation_spearman.csv`, `validation_summary.json`, `timing.csv` y `validate.log`.
- `uv run python -m ecosim.asignador day-timing` escribe `day_timing.csv` y `day_timing.log` (lazo cerrado con `flow_sim`).
- `uv run python -m ecosim.asignador retiro-sim` escribe `retiro_sim.csv` y `retiro_sim.log` (lazo cerrado con `ecosim/sim.py`, cota ⌊p⌋ contra ⌊p − σ⌋).

## Qué cambió respecto al run 1

1. **Tope de bicis por movimiento.** Cada orden cumple |x_i| ≤ `params.max_bikes_per_move` (22 por defecto). Entra como cota de la variable y_i y como coeficiente de la restricción que liga y_i con m_i (ver 3). Test (g) con 14, 22 y 42. En la validación, el |delta| máximo de los planes del MILP es 22.
2. **Varias emisiones de pronóstico** (`select_issue`). En la decisión t se usa la emisión más reciente con `issued_at ≤ t`, de:
   - la variante pedida (`params.extra["variant"]`);
   - la serie pedida (`params.refresh_min`: 15/60/180, o 0 para `daily`).

   Hay `ValueError` explícito si:
   - no hay ninguna emisión con `issued_at ≤ t`;
   - no existe esa serie o ese tamaño de bloque;
   - quedan varias variantes o varias series sin elegir;
   - la emisión no cubre [t, t+L+H).

   `Plan.issued_at` guarda la emisión usada. Tests (h), incluida una mutación: si se quita el filtro `issued_at ≤ t`, el test falla.
   - Formato confirmado con `ecosim2-pronostico`: la emisión de s incluye el bloque de 60 min que contiene a s y cubre [s, s+f+60+6 h], recortado a 00:30. oracle/ma/model usan `refresh_min = f` y `horizon_h = 6`; daily usa `refresh_min = 0` y `horizon_h = 19`. **`integracion2` debe pasar `refresh_min=0` para `daily`.**
3. **Cota de retiro, la causa de que el run 1 rompiera el tope de bodega.** Por defecto (`Asignador(retiro="floor")`), cada retiro se limita a las bicis que se proyectan en la estación en t+L: r_i = min(⌊p_i⌋, tope por movimiento).
   - p_i es la proyección fluida a t+L: incluye las órdenes pendientes y usa tasa uniforme por bloque.
   - Por simetría, cada puesta se limita a u_i = min(K_i − ⌈p_i⌉, tope).
   - y_i ∈ [p̂_i − r_i, p̂_i + u_i]. La envolvente convexa se calcula solo sobre ese rango, que es más ajustado que [0, K_i].
   - **Variante `retiro="sigma"`**: r_i = min(⌊p_i − σ_i⌋, tope) y u_i = min(K_i − ⌈p_i + σ_i⌉, tope), con σ_i = √(salidas + llegadas esperadas en [t, t+L)), una desviación Poisson del flujo de esa hora.
     - El margen es fijo (k = 1) y no se tuneó.
     - Es una **decisión de política, como λ**: la congela `integracion2` en días de selección con `sim.py`, igual para todos los brazos (ver "Lazo cerrado").
   - `retiro="round"` (p̂, la cota del run 1) queda solo para comparar.
   - Test (j): con p = 2.6, "round" pide retirar 3, el default ("floor") 2 y "sigma" 1. Además, en 40 estaciones al azar ningún retiro supera ⌊p⌋. Si se muta la cota, el test falla.
4. **Ventana 05:30–00:30.** El día de una hora de decisión es `config.window_day(t)`: `_day0`, el corte de órdenes y el reinicio de la historia del tope por hora ya no usan la fecha de calendario. No se emiten órdenes con t+L ≥ 00:30. Test (i): un día completo con L ∈ {60, 45, 30} deja la última orden en 00:30 − L − 15 min.
   - Con L ≥ 30 ninguna decisión posterior a medianoche llega a ordenar (t+L ≥ 00:30), así que dos tests usan L = 15 para cruzar medianoche.
   - Uno comprueba que la historia del tope por hora sobrevive de las 23:45 a las 00:00 del día siguiente.
   - El otro prueba `_day0` y las tasas tras medianoche.
   - Si se revierte `_day0` a `t.normalize()` o el reinicio de historia a `t.date()`, fallan (mutación verificada).
5. **Dañadas dinámicas.** K_i = `bikes + docks` del estado en t, como en el run 1: las dañadas de ese instante no cuentan como lugar útil y en cada decisión se lee el valor nuevo. No se supone nada fijo. Hay un test con dañadas que cambian entre dos decisiones.
6. **Fuera de servicio.** Si `state.stations` trae la columna `out_of_service` (el simulador la manda, fija, tomada de `initial_state`; lo confirmó `ecosim2-simulador`), esas estaciones tienen r = u = 0 y nunca reciben órdenes. Ejemplo: la 698 el 2025-10-25. Hay test.
7. **Bodega.** Se conserva la restricción del run 1: |W + P + Σx| ≤ tope_bodega, con W aplicado y P pedido.
8. **Fallback** (k). Si HiGHS no da una solución factible, no se mueve nada, se avisa y se registra en `Asignador.fallbacks`. El test se conserva, y con H = 6 también comprueba el tope por movimiento.
9. **Sin pronóstico:** `decide` con `forecast=None` lanza `ValueError`, en vez de no ordenar nada en silencio. Después del corte de 00:30 no hace falta pronóstico.

Placeholders declarados para `validate` y `day-timing`: `tope_hora = 60`, `tope_bodega = 200` y `max_bikes_per_move = 22`. `retiro-sim` usa los topes reales de `medicion2` (246 / 632 / 22).

## Validación del sustituto (Spearman), ventana nueva

La prueba es la del run 1 con cuatro cambios:
- **días de selección:** 2025-09-01 (lun), 2025-09-21 (dom) y 2025-10-30 (jue);
- **8 horas de decisión en todo el día:** 05:30, 07:30, 09:30, 12:30, 15:30, 18:30, 21:30 y 23:15. La de 23:15 tiene t+L = 00:15, así que su horizonte se corta a 15 min;
- **H ∈ {1, 3, 6}**, con L = 60 y pronóstico oracle;
- **23 planes por decisión, todos dentro de las cotas del MILP**, de modo que no se valúa nada que el MILP no pueda elegir.

El simulador es el de flujo propio (`flow_sim`), con conteos reales por minuto. Cota de retiro por defecto (⌊p⌋).

**Ojo, validación en parte circular:** `flow_sim` es la versión discreta del mismo flujo que usa el sustituto, con los mismos viajes. Solo difiere en el orden de los viajes dentro del bloque; no tiene desvíos, dañadas ni recortes.

| métrica (contra `flow_sim`) | valor |
|---|---|
| **Spearman por decisión, mediana** (envolvente) | **0.977** (H=1: 0.971, H=3: 0.979, H=6: 0.975) |
| Spearman por decisión, mínimo | 0.718 (2025-09-21 23:15, H=6) |
| decisiones con Spearman ≥ 0.8 | 69 / 72; las 3 que no llegan son del 09-21 a las 23:15 (horizonte de 15 min) |
| sin las 23:15: mediana / mínimo | 0.977 / 0.872 |
| mediana por hora | 05:30: 0.907; de 07:30 a 23:15: 0.965–0.987 |
| Spearman con c_i exacto (sin envolvente), mediana | 0.975 |
| Spearman agrupada de la mejora contra no hacer nada | 0.958 |
| Spearman solo entre los planes del MILP (λ-path), mediana / mínimo | 0.938 / 0.342 |
| ahorro de E+F en `flow_sim` / ahorro según el sustituto (λ = 0) | 0.72 |

**Contra `sim.py`.** Lo midió el reviewer; yo no lo reproduje. Montaje: 2025-09-17, oracle_f60, estado sacado de `sim.py` en 09:30, 15:30 y 18:30, H ∈ {1, 3}, 21 planes aplicados como órdenes.
- Spearman por decisión: mediana ≈ 0.94, mínimo 0.71 (18:30, H=3).
- Dentro del λ-path: 0.61–1.0.
- Ahorro en `sim.py` / ahorro del sustituto: **de 0.49 a 1.50**, no ~0.72.

Lectura:
- El sustituto ordena bien los planes en todo el día y con H hasta 6. Contra `sim.py` el orden global se mantiene (≈ 0.94), pero **el sustituto no calibra la magnitud del ahorro**.
- Las más bajas son de las 23:15, cuando el horizonte se corta a 15 min, y de las 05:30, cuando casi nada está vacío o lleno. En ambos casos las diferencias entre planes son chicas.
- Entre planes cercanos al óptimo (λ-path) el orden es débil.

## Tiempos

**Por decisión** (677 estaciones, oracle, 4 threads). Son 504 resoluciones, todas `Optimal`:

| H | mediana | p95 | máx |
|---|---|---|---|
| 1 | 0.18 s | 0.33 s | 0.40 s |
| 3 | 0.25 s | 0.42 s | 0.51 s |
| 6 | **0.33 s** | **0.59 s** | **0.63 s** |

**Por día completo:** 72 decisiones, 05:30 → 23:15, en lazo cerrado con `flow_sim` (`run_day_flow`), λ = 60, oracle y los placeholders. Con H = 6 el día tarda 14–20 s; el peor de las 27 corridas tarda 27 s. La decisión más lenta en lazo cerrado tardó 1.43 s. No hubo fallbacks. Con `sim.py` (simulador incluido, topes reales y H = 3), un día tarda 18–39 s. Los requisitos eran ≤ 5 s por decisión con H = 6 y ≤ 3 min por día.

## Lazo cerrado: ¿se siguen recortando retiros?

### Con `flow_sim` y oracle (solo orientativo)

`day_timing.csv` tiene 3 días de selección × H ∈ {1, 3, 6} × las tres cotas. La bodega aplicada = A − R realmente aplicado, con recortes. Promedio de los 3 días:

| H | cota | retiros pedidos | **retiros recortados** | puestas recortadas | **máx \|bodega aplicada\|** | E+F (flow_sim) |
|---|---|---|---|---|---|---|
| 1 | run 1 (p̂) | 2,670 | 59.7 | 8.7 | 53 | 68,865 |
| 1 | ⌊p⌋ | 2,799 | 64.3 | 12.0 | 54 | 67,335 |
| 1 | **⌊p − σ⌋** | 2,445 | **6.0** | 1.0 | **6** | 68,003 |
| 3 | run 1 (p̂) | 3,086 | 89.0 | 11.0 | 269 | 33,508 |
| 3 | ⌊p⌋ | 3,144 | 88.0 | 17.3 | 265 | 33,864 |
| 3 | **⌊p − σ⌋** | 2,729 | **9.3** | 0.0 | **207** | **32,942** |
| 6 | run 1 (p̂) | 3,049 | 71.7 | 11.0 | 256 | 28,676 |
| 6 | ⌊p⌋ | 3,106 | 72.3 | 15.0 | 257 | 28,538 |
| 6 | **⌊p − σ⌋** | 2,841 | **7.7** | 2.7 | **206** | **27,509** |

Estos números son solo de `flow_sim` con oracle: sin desvíos, dañadas ni pronóstico con error. Por ejemplo, en `flow_sim` σ deja 1–15 retiros recortados por día, pero en `sim.py` deja 12–740 (tabla de abajo).

### Con `sim.py` (floor contra σ), días de selección

`retiro_sim.csv`. Montaje:
- los 15 días de selección, con dañadas dinámicas;
- topes reales de `medicion2`: tope_hora 246, tope_bodega 632, 22 por movimiento;
- λ = 60, L = 60, H = 3, f = 60;
- oracle, `ma` y `model`, con los archivos de `pronostico2`.

Medias por día. E+F = métricas E + F de `sim.py`, que incluyen los minutos de las estaciones fuera de servicio. Esos minutos son iguales en las dos cotas: el asignador nunca les ordena.

| brazo | cota | E+F | retiros pedidos | **retiros recortados** (físico) | puestas recortadas | **recorte_bodega** | movimientos aplicados |
|---|---|---|---|---|---|---|---|
| oracle | ⌊p⌋ | 28,808 | 5,041 | 317 (32–603) | 44 | 180 (0–418) | 1,574 |
| oracle | ⌊p − σ⌋ | **26,044** | 4,785 | **69** (12–143) | 10 | **50** (0–140) | 1,750 |
| ma | ⌊p⌋ | 48,223 | 7,520 | 932 (333–1,521) | 121 | 754 (11–1,401) | 2,390 |
| ma | ⌊p − σ⌋ | **46,096** | 7,058 | **389** (126–740) | 44 | **331** (0–695) | 2,522 |
| model | ⌊p⌋ | 48,877 | 7,370 | 846 (309–1,327) | 102 | 673 (0–1,238) | 2,406 |
| model | ⌊p − σ⌋ | **45,984** | 6,953 | **355** (145–545) | 37 | **297** (0–514) | 2,541 |

- **Con los topes reales, σ baja E+F en 44 de 45 corridas.**
  - Mediana por día: oracle −11.6%, `ma` −5.3%, `model` −6.5%.
  - Rango: oracle de −20.4% a −2.4%; `ma` de −12.1% a +3.1%; `model` de −11.4% a −0.3%.
  - Recorta menos de la mitad de retiros y de bodega.
  - La bodega aplicada llega al tope (≈ 632) en las dos variantes.
- **Con los placeholders (60 / 200) el resultado es mixto.** Medición del reviewer, `sim.py`, días de selección:

  | día | brazo | σ | ⌊p⌋ | Δ de σ |
  |---|---|---|---|---|
  | 09-17 | oracle H6 | 117,220 (15) | 111,948 (47) | +4.7% |
  | 09-21 | oracle H6 | 13,590 (16) | 14,767 (78) | −8.0% |
  | 10-09 | oracle H6 | 68,332 (0) | 65,979 (28) | +3.6% |
  | 09-17 | ma H3 | 172,162 (67) | 177,470 (217) | −3.0% |
  | 10-09 | ma H3 | 129,043 (65) | 123,787 (167) | +4.2% |
  | 09-17 | model H3 | 176,862 (79) | 174,731 (212) | +1.2% |
  | 10-09 | model H3 | 126,990 (53) | 123,791 (164) | +2.6% |

  E+F, con recorte_bodega entre paréntesis.
- **El margen no es neutral entre brazos.** Con los topes reales **agranda** la brecha oracle–`ma` en ~640 E+F por día en promedio (19,415 → 20,053), porque σ ayuda más al oracle. El signo varía por día. En la medición del reviewer (placeholders), el 09-17 la achica.
- **Por qué quedan tantos recortes con ⌊p⌋:**
  - la proyección fluida usa la tasa media del bloque, y el stock real a t+L varía por el orden de los viajes, los desvíos y las dañadas;
  - con pronóstico real se suma su error;
  - cada retiro recortado sube la bodega aplicada y el simulador recorta después las puestas (`recorte_bodega`).

## Supuestos y avisos para `integracion2`

1. **Una instancia de `Asignador` por brazo y día.** La historia propia del tope por hora se reinicia cuando cambia `window_day(t)` o cuando t no avanza.
2. **`params.refresh_min` elige la serie de pronóstico:**
   - `daily` → 0;
   - `ma`/`model`/`oracle` → f.

   Si el pronóstico junta variantes, pasa `params.extra["variant"]`.
3. **H ≤ `horizon_h` de la emisión.** Hoy es 6 para las series re-emitidas. Si H pide más de lo que cubre la emisión, lanza `ValueError` en vez de usar tasas 0 en silencio.
4. **La cota de retiro (`Asignador(retiro=...)`) es una decisión de política, como λ.**
   - Default: "floor" (⌊p⌋), lo que pide el brief.
   - `integracion2` debe congelarla en días de selección con `sim.py`, antes de ver evaluación, e igual para todos los brazos. Con "sigma" como candidata, ⌊p⌋ se reporta como sensibilidad.
   - Con los topes reales, σ gana en selección (arriba); con los placeholders, el resultado es mixto.
5. **Los pendientes cuentan para la bodega a su tamaño pedido**, como en el run 1. Con ⌊p⌋, lo aplicado se aleja bastante de lo pedido (hasta ~1,500 retiros recortados por día con `ma`); el simulador lo corrige con `recorte_bodega`.
6. **Tope por hora "goloso"**, como en el run 1: con λ = 0 y tope 60, una decisión usa los 60 movimientos (mediana 60 en la validación).
7. Umbrales de vacía/llena del fluido: 0.5, igual que en el run 1.
8. No se usa del estado nada aparte de `bikes`, `docks`, `warehouse` y la columna opcional `out_of_service`.
9. Los días de validación son de **selección**; no se tocó ningún día de evaluación. La elección de λ y h queda para `integracion2`.
10. **Con `forecast=None`, `decide` lanza `ValueError`** (antes no ordenaba nada en silencio).
