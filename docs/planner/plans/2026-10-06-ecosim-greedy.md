# ecosim-greedy — Planner Plan

**Project name:** ecosim-greedy
**Planned:** 2026-10-06
**Repo root:** /Users/jeronimo.deli/Desktop/other/Vs/vaults/movilidad-cdmx
**Base branch:** `ecosim3-ux-integration` (HEAD d7b9149). Integrar todo en una rama nueva `ecosim-greedy-integracion` creada desde ahí.

## Goal

Reemplazar el MILP (HiGHS) por el greedy como única política de asignación, con la regla nueva de entregas fallidas del simulador; recalibrar n y λ del greedy validando con todo agosto de 2025; volver a correr todos los números con test de septiembre a diciembre de 2025 (sin 2026); mostrar en la app las órdenes de dónde a dónde (emitidas y ejecutadas, en replay y en predicción); y reescribir el reporte para que explique el greedy con fórmulas, sin HiGHS.

## User decisions / hard constraints

1. **Nada del código usa HiGHS ni MILP.** Se borran el `_solve` MILP, `lower_hull`, `hull_value`, la envolvente convexa y la dependencia `highspy` (pyproject + uv.lock). El reporte no menciona MILP, HiGHS ni envolvente, ni siquiera como comparación.
2. **Se llama `greedy`, no `voraz`.** Archivo `ecosim/greedy.py`; `ecosim/voraz.py` (sin commit, en el checkout principal) se borra al terminar la base.
3. **Limpieza del greedy** (auditada 2026-10-06): quitar `kbest` (código muerto); el estado devuelto es `"greedy"`, no `"Optimal"`; devolver además los paquetes (receptor, x, [(donante, k)]). El algoritmo de decisión NO cambia: mismas `pick`/`deliver` que `ecosim/voraz.py` para las mismas entradas. No agregar reparto de sobrante entre receptores (se probó: ≈0.5 %, no vale).
4. **Regla del simulador:** al recoger se lleva solo lo que haya (no completa en otra estación). Al entregar, lo que no cabe en el destino (o todo, si el destino está fuera de servicio) se deja en la estación con anclaje libre más cercana a ESE destino. Nunca vuelve al origen: se elimina la rama de "devolución al origen"; una orden de política a una estación que no existe en el día es un error (`ValueError`), porque el greedy solo planea con estaciones del estado de ese día. Ya está implementada la parte de "más cercana al destino" sin commit en `.worktrees/ecosim3-reporte-caso/ecosim/sim.py` y `tests/ecosim/test_sim.py` (ver `git -C .worktrees/ecosim3-reporte-caso diff ecosim/sim.py tests/ecosim/test_sim.py`).
5. **Partición train / validación / test (rolling):**
   - Train para validar: LightGBM corte `elegir`, viajes 2025-01-01 → 2025-07-31 (sin cambios).
   - **Validación: todos los días de agosto de 2025** que cumplen cobertura ≥ 90 % y apertura válida (los mismos filtros de `ecosim/days.py`), en vez de 15 sorteados. `days.json` → `seleccion` pasa a ser esa lista completa.
   - **Test: septiembre a diciembre de 2025** (121 días), cortes `prueba_1..4`: cada mes LightGBM se reentrena con los 8 meses anteriores (sin cambios; para septiembre incluye agosto, ya como historia, lo cual es correcto).
   - **No se usa 2026** en la evaluación ni en el reporte: se quitan enero 2026 (`prueba_5`) y febrero–agosto 2026 (`prod_2026`, paso 6) de las corridas, tablas y reporte, porque el feed de 2026 es más sucio y se quiere la comparación más justa contra Ecobici. (La app en vivo puede seguir usando sus modelos de producción para pronosticar hoy; eso no es evaluación.)
   - Cómputo esperado: unas 6 h con 5 procesos (sensibilidades y curvas en 121 días).
   - LightGBM no excluye ningún día: los días de validación no están en el train de `elegir`, y que entren al train de los cortes de test es lo correcto en rolling.
6. **Calibración:** se vuelven a elegir n y λ con el greedy, solo en los días de validación (agosto 2025), con el protocolo de la sección "Protocolo de calibración". Ningún día de test se mira para calibrar.
7. **Órdenes de dónde a dónde en la app**, para órdenes emitidas y ya ejecutadas, tanto en Replay como en Predicción (sesión en vivo).
8. **Reporte:** explica el greedy con fórmulas y procedimiento paso a paso, muy bien explicado, sin ejemplo numérico. Debe responder las preguntas que el usuario hizo al aprenderlo (lista en el contrato del reporte).
9. **Topes = p95 de Ecobici en todo 2025** (son límites duros, van primero): 351 días de 2025 con cobertura ≥ 90 %, ventana 05:00, mismo método de `medicion.visit_caps`. Medido por el planner el 2026-10-06: **55 visitas por decisión (p95 55.16) y 17 bicis por visita**; p99 73 visitas (72.65) y 28 bicis. El script de referencia reproduce exactamente el cálculo viejo de ene–ago 2025 (230 días, 47/19, p99 62/33). Se usa solo el p95 anual: **no hay sensibilidad de topes** (ni p99 hacia arriba ni nada hacia abajo). Se quitan las poblaciones viejas como topes (15 días de sep–nov, `referencia_wiki` 67/14; ene–ago, `recalculados` 47/19) y las sensibilidades `p99` y `hacia_adelante`. El reporte dice en Amenazas, en una frase, que el tope se mide sobre todo 2025 (incluye los meses de test) porque describe la operación de Ecobici, no un parámetro ajustado a favor de la política.
10. **Todo con el greedy nuevo:** cada cifra del reporte, de `ecosim/results/` y del Replay de la app (sus 16 días precalculados, 4 por mes de sep–dic 2025, se quedan) sale de las corridas nuevas. Nada del run 3 viejo queda visible.
11. **Sensibilidades y chequeos sobre los 121 días de test** (ya no sobre los 32 días de `curva`): curvas de λ en test (paso 4), sensibilidades (paso 5) y V1 (Ecobici real contra simulado). Sensibilidades que quedan: **entrega a 45 y 75 min** (la principal: ±15 min respecto a la hora), pares (`sin_regla`, `solo_1`) y dañadas del feed. Ninguna de topes.
12. Commits como `Jero110 <jeronimo.deli@gmail.com>`, sin `Co-Authored-By` de Claude (`git -c user.name=Jero110 -c user.email=jeronimo.deli@gmail.com commit ...`).

## Protocolo de calibración (lo ejecuta el worker `runs`)

Datos: solo los días de validación (todo agosto 2025 válido, `days.json` → `seleccion`). Brazos de política: `oraculo_diario`, `ma_diaria`, `lgbm_diario`, `oraculo_directo`, `lgbm_directo`. Topes base fijos (p95 anual 2025: 55 visitas por decisión, 17 bicis por visita).

**Paso A — n (horas que mira el asignador).** Misma regla del run 3 (`run.py` paso 1, `choose_n`), ahora con el greedy:
- Brazos oráculo (`oraculo_diario` para la forma diaria, `oraculo_directo` para la directa), n ∈ {2,…,6}, λ ∈ {15, 30, 60}; n = 1 es "sin rebalanceo" por construcción.
- Por día, E+F promedio sobre las tres λ; luego, para n = 1, 2, …: IC95 t pareado por día de E+F(n+1) − E+F(n). Se elige la primera n cuyo IC95 superior es ≥ 0 (sin mejora demostrable o empeora). Una n por forma (diaria, directa).

**Paso B — λ por brazo (precio por visita, en minutos).**
- Objetivo: el promedio de visitas por día de Ecobici en los mismos días de validación (`ecobici`, pares = `todas`).
- Rejilla: λ ∈ {10, 15, 20, 30, 45, 60, 75, 90, 120} (se amplía la del run 3 para no extrapolar más allá de 60). Cada brazo corre con su n del paso A.
- Para cada brazo: curva visitas/día promedio vs λ (decrece con λ). λ* = interpolación lineal entre los dos puntos de la rejilla que encierran el objetivo.
- Confirmación: correr λ* en los días de validación. Aceptar si |visitas − objetivo| ≤ 1 % del objetivo. Si no, agregar el punto confirmado a la curva, reinterpolar y confirmar; máximo 4 rondas. Si tras 4 rondas no entra en 1 %, quedarse con la ronda más cercana y reportarlo. Si el objetivo cae fuera de la rejilla, extender la rejilla (5 en 5 hacia abajo, ×1.5 hacia arriba) antes de interpolar.
- Se congela en `ecosim/results/frozen.json` (`n`, `lambda_por_brazo` con visitas confirmadas, % de diferencia y rondas). A partir de ahí no se toca.

**Después de congelar:** test (121 días, sep–dic 2025), curvas de λ en test y sensibilidades (los 121 días) se corren con esos n y λ sin ajustarlos. No hay corridas de 2026.

## Planner assumptions / flexible approach

- **Paquetes en el simulador:** cada `Order` lleva un campo nuevo `paquete: int = 0`. El asignador numera los paquetes del greedy dentro de cada decisión; el simulador arma un lote por (decisión, paquete) en vez de uno por decisión, de modo que las bicis de los donantes de un paquete solo van a su receptor. Las órdenes de Ecobici (replay) quedan con `paquete = 0` y su física no cambia. El registro de órdenes y el de reubicaciones guardan el paquete, para reconstruir lo ejecutado de dónde a dónde. Si el worker encuentra una forma más simple que dé pares ejecutados exactos, puede cambiarla y documentarlo.
- `asignador.py` queda como el módulo de proyección y tablas de costo (ecuación de inventario, `fluid_project`, `_option_costs`, `Asignador.plan/decide`), y llama a `greedy.greedy(...)`. Atributos y columnas que solo tenían sentido con un solver (`time_limit`, `threads`, `mip_rel_gap`, `fallbacks`, `timeouts`, `decisiones_limite_10s`) se eliminan donde sea seguro; si una tabla o la app los lee, se ajusta el lector en el mismo cambio.
- `run.py` permite hasta 5 procesos (el límite de 2 era por los threads de HiGHS). Máquina: 14 núcleos.
- Los modelos LightGBM no cambian (no hace falta reentrenar).

## Scope

In:
- Base de código: greedy limpio, sin MILP/HiGHS, regla de destino sin devolución al origen, paquetes, validación = todo agosto, test = sep–dic 2025 sin 2026, tests.
- Recalibrar n y λ; correr pasos 1–5 de `run.py` (sin paso 6 de 2026) y regenerar tablas.
- App: pares de dónde a dónde en Replay y Predicción.
- Reporte `reporte-caso.tex`: sección del asignador reescrita (greedy), calibración, y todas las cifras nuevas.
- Cierre del executor: `wiki/index.md` y `wiki/log.md` (marcar como superadas las páginas que hablan del MILP/HiGHS y de las cifras viejas; no borrar páginas).

Out:
- Cambiar el algoritmo del greedy (reparto de sobrantes, distancias, rutas de camión).
- Cambiar el horizonte de recogida/entrega o la media móvil.
- `reporte-final.tex` y otros reportes viejos.
- Publicar en GitHub (repos `movilidad-cdmx` público/privado).

## Revisión humana / Human approval checklist

- [ ] El greedy queda idéntico en decisiones a `voraz.py` (solo limpieza + paquetes).
- [ ] HiGHS desaparece por completo (código, dependencia, app, reporte).
- [ ] Topes: p95 de todo 2025 (55 visitas por decisión, 17 bicis por visita); sin sensibilidad de topes.
- [ ] Sensibilidades y V1 en los 121 días de test; la principal es entrega a 45/75 min. Replay de la app (16 días) regenerado con el greedy.
- [ ] Partición: train ene–jul 2025 → validación todo agosto 2025 (n y λ) → test rolling sep–dic 2025 (121 días). Sin 2026. LightGBM sin cambios.
- [ ] Simulador: lo que no cabe se deja en la estación libre más cercana al destino; nunca vuelve al origen.
- [ ] Protocolo de calibración: n por la regla del run 3; λ por igualar visitas de Ecobici ±1 % con rejilla 10–120.
- [ ] Simulador: lotes por paquete (las bicis de un paquete solo van a su receptor). Esto cambia un poco la física de la política respecto a la bolsa común por decisión.
- [ ] Reporte sin ejemplo numérico, con fórmulas.
- [ ] Fuentes del reporte: cada cita citada se abre; las que no abren y no son indispensables se quitan; las indispensables sin enlace vivo quedan marcadas para ti.

## Shared foundation

La subtarea `base` es la base compartida: `runs`, `app` y `reporte` dependen de que esté revisada y mergeada en `ecosim-greedy-integracion`. Orden:

1. `base` (sola).
2. Al mergear `base`: lanzar `runs` y `app` en paralelo, y `reporte` fase 1 (texto del método).
3. Al mergear `runs`: avisar a `reporte` para la fase 2 (cifras).

## Review guidance

- base: deep_ai (toca simulador, contrato `Order` y particiones de días; es tronco)
- runs: validation-only + fast_ai (revisar que el protocolo se siguió y que no se miró prueba)
- app: fast_ai + human (el usuario aprueba la UI)
- reporte: deep_ai + human

---

## Subtask: base

**Mechanism:** pane
**Harness:** claude-code
**Model:** opus
**Review stack:** deep_ai

Objective:
En una rama `ecosim-greedy-base` (worktree propio, desde `ecosim3-ux-integration`):
0. **Topes anuales (primero, son límites duros).** En `ecosim/medicion.py`, calcular `visit_caps` sobre todos los días de 2025-01-01 a 2025-12-31 con cobertura ≥ `C.COVERAGE_MIN` y serie densa, ventana 05:00 (hoy `run()` solo junta ene–ago en `caps_iv` y elegibles hasta 2025-11-30: ampliar a todo 2025, incluido diciembre). Guardar en `ecosim/results/medicion/ecobici_stats.json` una clave `anual_2025` con `p95`/`p99` (`visitas_por_decision`, `bicis_por_visita`), `poblacion`, `ventana`, `dias`, `fotos`; quitar `referencia_wiki`, `referencia_reproducida` y `recalculados` como topes (pueden quedar como diagnóstico en el `REPORT.md` de medición, no como entrada de `run.py`). `run.cap()`: solo `base` → p95 anual (quitar `p99` y `adelante`); el p99 puede quedar en el JSON como dato descriptivo. Valores esperados (medidos por el planner con un script que reproduce exactamente el cálculo viejo de ene–ago): 351 días, 23,336 fotos, p95 55 visitas y 17 bicis, p99 73 visitas y 28 bicis. Si no salen, BLOCKED y reportar la diferencia. Script de referencia: `docs/planner/plans/2026-10-06-ecosim-greedy.topes_2025.py` (en el checkout principal; correr con `uv run python3 <script>` desde la raíz del repo).
1. **Regla del simulador:** portar a `ecosim/sim.py` y `tests/ecosim/test_sim.py` el cambio sin commit de `/Users/jeronimo.deli/Desktop/other/Vs/vaults/movilidad-cdmx/.worktrees/ecosim3-reporte-caso` (obtenerlo con `git -C <ese worktree> diff ecosim/sim.py tests/ecosim/test_sim.py`): lo que no cabe en un destino (o todo, si está fuera de servicio) se deja en la estación con anclaje libre más cercana a ese destino (contador `reubicacion_destino`). Además, eliminar la rama que devuelve carga al origen (y el contador `devolucion_origen`): para órdenes de política, un destino que no existe en el día lanza `ValueError`. Al recoger se lleva solo lo que haya. Con lotes por paquete (punto 4), la carga de un lote siempre termina en su receptor o junto a él.
2. **Greedy:** crear `ecosim/greedy.py` con una función pura `greedy(r, u, pc, dc, lam, V)` que devuelve `(pick, deliver, paquetes)`, con `paquetes = [(receptor, x, [(donante, k), ...]), ...]`. Algoritmo exacto de `/Users/jeronimo.deli/Desktop/other/Vs/vaults/movilidad-cdmx/ecosim/voraz.py` (archivo sin commit en el checkout principal; copiarlo al worktree antes de borrarlo): (a) por donante j con r_j>0, `ratio_j = min_k (pc[j,k]+λ)/k`; (b) por receptor i con u_i>0, `gain_i = max_x (−dc[i,x]−λ)`; (c) recorrer receptores por gain descendente; parar si gain ≤ 0 o visitas+2 > V; saltar usados; (d) donantes libres ordenados por ratio, sin el propio i; para cada x = 1..u_i, llenar x tomando `k = min(falta, r_j)` de cada donante en orden, costo = dc[i,x] + λ + Σ(pc[j,k] + λ); descartar si no se completa o si visitas + 1 + #donantes > V; quedarse con la x de mayor −costo si es > 0; (e) asignar y marcar receptor y donantes como usados. Quitar `kbest`. Docstring y comentarios en español, al estilo del repo.
3. **Quitar MILP/HiGHS:** en `ecosim/asignador.py` borrar `lower_hull`, `hull_value`, el `_solve` MILP, el import `highspy`/`scipy.sparse` si queda sin uso, los campos de envolvente del `Plan` y el parámetro `hull` de `Plan.surrogate`; `Asignador.plan` llama a `greedy.greedy` y guarda `paquetes` en el `Plan`; `status = "greedy"`. Quitar `highspy` de `pyproject.toml` y `uv.lock` (`uv remove highspy`). Ajustar `ecosim/run.py`, `ecosim/live.py`, `ecosim/replay.py` y cualquier otro uso (`grep -rn "highspy\|_solve\|hull\|time_limit\|mip_rel_gap\|fallbacks\|timeouts" ecosim scripts tests`). `run.py`: permitir `--procesos` hasta 5.
4. **Paquetes:** `ecosim/contracts.py` `Order` gana `paquete: int = 0` (validado entero ≥ 0). `Asignador.decide` emite una orden por estación con el número de su paquete (1..P dentro de la decisión). `sim.py`: para políticas, un lote por (decisión, paquete): cada lote equilibra recogidas y entregas; el tope de visitas se sigue revisando por decisión; las órdenes con `paquete = 0` (Ecobici) siguen como hoy. El registro de órdenes aplicadas y el de reubicaciones incluyen el paquete, y hay una función en `sim.py` (o `replay.py`) que devuelve los pares ejecutados `(decisión, paquete, origen, destino, bicis)`, incluyendo las reubicaciones destino → estación cercana.
5. **Partición de días:**
   - Validación = todo agosto 2025 válido: en `ecosim/days.py`, `seleccion` deja de sortear 11 + 4 y toma todos los días de agosto 2025 que pasan los filtros actuales (cobertura ≥ `C.COVERAGE_MIN` y apertura válida); quitar `N_SEL_WEEKDAY`, `N_SEL_WEEKEND` y `SELECTION_SEED` de `config.py` si quedan sin uso. Regenerar `ecosim/days.json` (`uv run python -m ecosim.days`) y verificar que solo cambia `seleccion`.
   - Test = sep–dic 2025: en `ecosim/run.py`, `days("prueba")` usa solo `2025-09..2025-12` (quitar `2026-01`); quitar el paso 6 (`prod_2026`) del pipeline (`--all` corre 1–5) y las partes de `tablas()` que leen `prod_2026` o enero 2026 (`run.py` ~líneas 685–760). `replay.py` ya usa sep–dic; revisar que no lea 2026. No tocar los cortes ni el entrenamiento de LightGBM (`pronostico.py`, `C.FOLDS`): los modelos existentes se reutilizan. La app en vivo (`live.py`, modelos `prod_2026_*`) no cambia.
6. **Tests:** reemplazar `tests/ecosim/test_asignador.py` por tests del greedy: (i) igualdad de `pick`/`deliver` con `voraz.py` en al menos 200 entradas aleatorias reproducibles (r, u ≤ 17, pc/dc aleatorios con c(0)=0, λ ∈ {10, 61, 120}, V ∈ {2, 3, 55}) — este test compara contra una copia literal de voraz.py guardada en `tests/ecosim/_voraz_referencia.py`; (ii) invariantes: Σpick = Σdeliver, pick ≤ r, deliver ≤ u, ninguna estación recoge y entrega, visitas ≤ V, cada paquete suma lo mismo; (iii) un caso a mano con 3 receptores y 3 donantes donde se conoce el resultado. Tests nuevos de `sim.py` para lotes por paquete (las bicis de un paquete no van a otro receptor), para la regla de destino y para el `ValueError` con destino inexistente. Ajustar `tests/ecosim/test_run.py` y otros que asuman 15 días de selección, enero 2026 o paso 6.
7. Borrar `ecosim/voraz.py` del checkout principal SOLO después de que el test (i) pase y el commit esté hecho (es un archivo sin commit; borrarlo con `rm`).

Constraints:
- No cambiar las decisiones del greedy respecto a `voraz.py`, ni `fluid_project`, `_option_costs`, la proyección, topes, recogida a t+15 / entrega a t+60, el pronóstico de media móvil ni el replay de Ecobici (paquete 0).
- No correr los pasos de `run.py` (eso es de `runs`). No tocar `ecosim/results/*` salvo lo que regeneren los tests.
- No tocar el worktree `.worktrees/ecosim3-reporte-caso` (solo leer su diff).
- Commits como Jero110 (ver decisiones).

Validation:
```
cd <worktree> && uv run pytest tests/ecosim -q
grep -rn "highspy\|HiGHS\|lower_hull\|hull_value\|voraz" ecosim scripts tests pyproject.toml   # solo debe salir tests/ecosim/_voraz_referencia.py
uv run python3 -c "from ecosim import run; r=run.one(run.spec('2025-09-01','ma_diaria','humo_base',4,61.0)); print(r['EF'], r['visitas'], r['max_visitas_decision'], r['max_bicis_visita'], r['truck_end'])"   # corre, visitas ≤ 55 por decisión, ≤ 17 bicis, truck_end 0
```
Más: `uv run python3 -c "from ecosim import run; print(run.cap())"` → `{'visitas_por_decision': 55, 'bicis_por_visita': 17}`. Y `uv run python3 -c "from ecosim import run; s=run.days('seleccion'); p=run.days('prueba'); print(len(s), s[0], s[-1], len(p), p[0], p[-1])"` → todos los días válidos de agosto 2025 y 121 días de 2025-09-01 a 2025-12-31.

Stop condition: todo lo anterior pasa, commit(s) hechos en `ecosim-greedy-base`, reporte con lista de archivos tocados, columnas eliminadas o renombradas (para `runs`, `app` y `reporte`) y la forma exacta de los pares ejecutados.

Cross-refs: `runs`, `app` y `reporte` leen de este worker la lista de columnas que cambiaron y la API de pares.

---

## Subtask: runs

**Mechanism:** pane
**Harness:** pi
**Model:** gpt-5.6-terra
**Review stack:** validation-only + fast_ai

Objective:
Con `base` mergeada en `ecosim-greedy-integracion`, en una rama `ecosim-greedy-runs`:
1. Archivar los resultados viejos (MILP + simulador viejo): mover `ecosim/results/{resultados.csv,corridas_run3.csv,curvas_seleccion.csv,n_seleccion.csv,frozen.json,eficiencia_run3.csv,ef_por_bloque.csv,donde_falla.csv,tablas.md,CONCLUSIONES.md}` a `ecosim/results/run3_milp/` y vaciar el caché de corridas `C.DERIVED / "run3_blocks"` moviéndolo a `run3_blocks_milp` (su clave no distingue política ni versión del simulador; si se reutiliza, sirve filas viejas).
2. Implementar en `ecosim/run.py` el "Protocolo de calibración" de este plan (copiado abajo), sin mirar días de prueba: paso 1 (n) tal como está; paso 2 (λ) con rejilla {10, 15, 20, 30, 45, 60, 75, 90, 120}, tolerancia 1 %, máximo 4 rondas, extensión de rejilla si el objetivo cae fuera.
   Además en `run.py`: paso 4 (curvas de λ en test) y paso 5 (sensibilidades) usan los 121 días de `days("prueba")` en vez de `days("curva")`; paso 5 queda con `entrega_45`, `entrega_75`, `pares_sin_regla`, `pares_solo_1`, `danadas_feed` (sin `p99` ni `hacia_adelante`); `validate_v1` usa los 121 días de test en vez de `days("prueba")[:15]`; ajustar en `tablas()` todo lo que filtraba por `days("curva")` (~línea 767).
3. Correr `uv run python3 -m ecosim.run --all --procesos 5 --threads 1` (pasos 1–5; ya no hay paso 6) y dejar regeneradas las tablas (`tablas()`), `frozen.json`, `resultados.csv`, `curvas_seleccion.csv`, `n_seleccion.csv` y el resto de salidas del run 3.
4. Regenerar el replay precalculado de la app (`uv run python3 -m ecosim.replay`, 16 días de sep–dic 2025) para que coincida con el `resultados.csv` nuevo (el propio script lo verifica).
5. Escribir `ecosim/results/CONCLUSIONES.md` nuevo con: partición (train ene–jul 2025, validación = todo agosto 2025 con su número de días, test sep–dic 2025, 121 días), n y λ elegidos por brazo (visitas confirmadas y % contra Ecobici), tabla principal en test (E, F, E+F, visitas, bicis movidas por brazo, incluidos `ecobici` y `sin_rebalanceo`), días en que la política gana a Ecobici, E+F a igual número de visitas que Ecobici en test (de las curvas del paso 4), sensibilidades (entrega 45/75 primero), V1 en 121 días y el contador nuevo `reubicacion_destino`.

Protocolo de calibración (literal):
- Datos: solo los días de `days.json` → `seleccion` (todo agosto 2025 válido). Brazos: `oraculo_diario`, `ma_diaria`, `lgbm_diario`, `oraculo_directo`, `lgbm_directo`. Topes base p95 anual 2025 (55 visitas por decisión, 17 bicis por visita).
- n: oráculos, n ∈ {2..6}, λ ∈ {15, 30, 60}; por día promedio de E+F sobre las tres λ; primera n con IC95 superior de E+F(n+1) − E+F(n) ≥ 0; una n por forma.
- λ: objetivo = visitas/día promedio de `ecobici` en esos mismos días; curva visitas vs λ por brazo con su n; interpolación lineal entre los puntos que encierran el objetivo; confirmar; aceptar si |visitas − objetivo| ≤ 1 %; si no, agregar el punto y repetir (máx. 4 rondas); congelar en `frozen.json`.

Constraints:
- No cambiar el greedy, el simulador ni el pronóstico (si algo parece mal, BLOCKED y reportar).
- Ningún día de test (sep–dic 2025) entra a la calibración. No ajustar n ni λ después de congelar. No correr nada de 2026.
- Commits como Jero110. Los CSV de resultados sí se versionan como hoy.

Validation:
```
uv run pytest tests/ecosim -q
uv run python3 -c "import json;f=json.load(open('ecosim/results/frozen.json'));print(f['n'], {k:(round(v['lambda'],2), round(v['diferencia_pct'],2)) for k,v in f['lambda_por_brazo'].items()})"   # |diferencia_pct| ≤ 1 para todos o explicado
uv run python3 -c "import pandas as pd;r=pd.read_csv('ecosim/results/resultados.csv');p=r[r.tag=='prueba'];print(p.groupby('arm').day.nunique(), p.groupby('arm')[['EF','visitas']].mean().round(0), p.day.min(), p.day.max())"   # 121 días por brazo, 7 brazos, 2025-09-01..2025-12-31; sin filas de 2026
```

Stop condition: pasos 1–5 terminados y replay regenerado, validaciones pasan, `CONCLUSIONES.md` escrito, commit hecho; reporte con la tabla principal y n/λ congelados.

Cross-refs: preguntar a `ecosim-greedy-base` (o leer su reporte) qué columnas cambiaron de nombre o desaparecieron.

---

## Subtask: app

**Mechanism:** pane
**Harness:** claude-code
**Model:** sonnet
**Review stack:** fast_ai + human

Objective:
Con `base` mergeada, en una rama `ecosim-greedy-app`, la app (`scripts/ecobici_mapa/server.py`, `app.js`, `index.html`, `app.css`; backend en `ecosim/live.py` y `ecosim/replay.py`) muestra las órdenes de dónde a dónde:
1. **Emitidas:** para cada decisión, lista de pares planeados "Recoge k en ORIGEN → entrega en DESTINO", agrupados por paquete (un receptor con sus donantes), con los horarios t+15 y t+60. Viene de `Plan.paquetes`.
2. **Ejecutadas:** lo que el simulador realmente movió por paquete: bicis recogidas en cada origen, entregadas en el destino y, si no cupieron, "dejadas en ESTACIÓN cercana (d m)". Viene de los pares ejecutados que expone `base`.
3. En **Replay** (comparación de escenarios) y en **Predicción / sesión en vivo** (`live.Sesion`): la lista de órdenes existente (`_orden`, pestañas "Emitidas"/"Aplicadas" en `index.html` ~línea 293, app.js ~línea 1853) muestra los pares.
4. **Toggles en el mapa, como los de viajes** (`index.html` ~líneas 104–113: "Viajes de la foto" `#showTrips`, "Viajes desviados" `#showDetours`): agregar dos toggles nuevos con el mismo componente `label.toggle`, en Replay y en Predicción:
   - **"Órdenes emitidas"**: dibuja una línea origen → destino por cada par planeado en el paso actual (estilo análogo a los viajes de la foto, con un color propio y flecha o gradiente que indique el sentido).
   - **"Órdenes que llegaron"**: dibuja lo ejecutado en el paso actual: origen → destino con las bicis realmente movidas, y en otro trazo (punteado) destino → estación cercana cuando no cupieron.
   Ambos apagados por defecto, independientes entre sí, con su leyenda; al pasar o seleccionar una línea, ficha con origen, destino, bicis y horas. Las órdenes de Ecobici (sin pares) no dibujan líneas y se siguen mostrando como hoy.
5. Quitar cualquier texto de la app que mencione MILP, HiGHS u "óptimo"; el texto del asignador dice que decide con un procedimiento greedy cada 15 min. Quitar de la app cualquier selector o texto de enero 2026 / 2026 en Replay (el test es sep–dic 2025).

Constraints:
- No cambiar el greedy, el simulador ni los números. No cambiar el diseño de las vistas más allá de lo necesario para mostrar los pares.
- Mantener los tests de la app pasando (`scripts/ecobici_mapa/test_server.py`, `tests/ecosim/test_live.py`, `tests/ecosim/test_replay.py`, `scripts/ecobici_mapa/test_ui.cjs`).
- Commits como Jero110.

Validation:
```
uv run pytest tests/ecosim/test_live.py tests/ecosim/test_replay.py scripts/ecobici_mapa/test_server.py -q
node scripts/ecobici_mapa/test_ui.cjs --no-live   # capturas en scripts/ecobici_mapa/screenshots/
```
Más: capturas de Replay y Predicción con cada toggle prendido ("Órdenes emitidas" y "Órdenes que llegaron"), mostrando al menos un paquete con 2 donantes y uno con reubicación al destino cercano, guardadas en `scripts/ecobici_mapa/screenshots/` y listadas en el reporte.

Stop condition: pares emitidos y ejecutados visibles en ambas vistas (lista y toggles del mapa), tests pasan, capturas tomadas, commit hecho.

Cross-refs: pedir a `ecosim-greedy-base` la forma exacta de `Plan.paquetes` y de los pares ejecutados.

---

## Subtask: reporte

**Mechanism:** pane
**Harness:** claude-code
**Model:** opus
**Review stack:** deep_ai + human

Objective:
El reporte vigente es `report/reporte-caso.tex` en el worktree existente `/Users/jeronimo.deli/Desktop/other/Vs/vaults/movilidad-cdmx/.worktrees/ecosim3-reporte-caso` (rama `ecosim3-reporte-caso`). `report/*.tex` está en `.gitignore`: el .tex, `numeros-caso.tex`, `figs/figs_caso.py` y `report/tablas/*-caso.tex` NO están en git; trabajar ahí mismo, no en un worktree nuevo. Antes de empezar: descartar en ese worktree los cambios sin commit de `ecosim/sim.py` y `tests/ecosim/test_sim.py` (ya entraron por `base`; `git checkout -- ecosim/sim.py tests/ecosim/test_sim.py`), conservar `report/referencias.bib`, y mergear `ecosim-greedy-integracion` en `ecosim3-reporte-caso`.

**Fase 1 (al mergear `base`) — método:**
0. **Auditoría de fuentes.** Para cada entrada de `report/referencias.bib` citada en `reporte-caso.tex` (hoy 27 de 41), abrir la fuente: `https://doi.org/<doi>` si tiene DOI; si no, la URL o fuente de `note`/`howpublished`. Usar Exa (`web_fetch_exa`) y, si se acaban los créditos (429), WebFetch. "Abre" = la página del artículo o dato carga (una página de editorial con resumen y muro de pago cuenta como que abre); "no abre" = 404, DOI que no resuelve, dominio caído o página que no corresponde a la referencia. Decisión: si abre, se queda. Si no abre: (a) si la cita no es indispensable (no sostiene una cifra, método o afirmación central del reporte), quitar la cita y ajustar o quitar la frase; (b) si es indispensable, buscar una versión que sí abra (otra URL oficial, arXiv, repositorio de la institución) y actualizar la entrada; si no hay, dejarla y marcarla para revisión humana. Quitar también del .bib las entradas que ya no se citan. Entregar una tabla en el reporte del worker: clave, enlace probado, abre (sí/no), para qué se cita, acción tomada.
1. Reescribir la sección del asignador: quitar MILP, programa entero, envolvente convexa, HiGHS y sus ecuaciones (`eq:obj`, `eq:c1`, `eq:c2`, …) y cualquier mención en el resto del reporte (introducción, conclusiones, amenazas, apéndices, figuras, bibliografía de HiGHS).
2. Explicar el greedy con fórmulas y procedimiento numerado, sin ejemplo numérico, en la notación que ya usa el reporte (s_i(m), o_im, f_im, [·]_K, p_i, q_i, r_i, u_i, B, c^R_i(x), c^E_i(x), λ, n, H). Debe quedar claro, en este orden:
   - qué se calcula: c^R_i(k) y c^E_i(x) son minutos vacía/llena totales para k o x bicis (no por bici), con la ecuación de inventario que ya está;
   - λ es un costo fijo por visita (por parada), no por bici; mover 1 o 2 bicis en una parada paga un solo λ;
   - orden de donantes: ρ_j = min_k (c^R_j(k)+λ)/k, que reparte el λ entre las bicis de una parada; orden de receptores: g_i = max_x (−c^E_i(x) − λ), el ahorro si las bicis fueran gratis;
   - el procedimiento por paquetes: se recorren receptores por g_i; para cada x se juntan las bicis de los donantes libres más baratos tomando k_j = min(falta, r_j); costo neto del paquete = c^E_i(x) + λ + Σ_j (c^R_j(k_j) + λ); se elige la x de mayor ahorro neto y se acepta si es positivo; los usados no vuelven a entrar;
   - por qué cuadra: cada paquete recoge lo mismo que entrega, así que la decisión entera también;
   - topes: B bicis por visita entra antes (en r_i y u_i) y V visitas por decisión durante (valores en `numeros-caso.tex`; hoy 17 y 55); por qué no se recorta al final; de dónde salen (p95 de lo que hizo Ecobici en todo 2025);
   - cuándo para: g_i ≤ 0, no caben 2 visitas, no hay donantes que completen;
   - qué sale: órdenes por estación con su paquete (de dónde a dónde), recogida en t+15 y entrega en t+60, y que cada 15 minutos se recalcula con las órdenes pendientes como o_im;
   - límites honestos: un donante sirve a un solo receptor por decisión; no se toma en cuenta la distancia.
3. Reescribir la descripción del simulador para la regla nueva (recoger lo que haya; lo que no cabe se deja en la estación libre más cercana al destino; nunca vuelve al origen).
4. Sección de datos y calibración como train / validación / test rolling: LightGBM entrena con ene–jul 2025; validación = todos los días válidos de agosto 2025, donde se eligen n (regla del IC95 pareado con oráculos) y λ (igualar visitas de Ecobici ±1 %, rejilla 10–120, interpolación y confirmación) y se congelan; test = sep–dic 2025, con LightGBM reentrenado cada mes con los 8 meses previos. Explicar en una frase por qué no se usa 2026 (feed más sucio; comparación más justa contra Ecobici). Quitar del reporte toda cifra, tabla o figura de enero 2026 y de 2026.

**Fase 2 (cuando el executor avise que `runs` está mergeado) — cifras:**
5. Regenerar cifras con `uv run python3 report/figs/figs_caso.py` (y `--check`) y `numeros_run3.py`, tablas y figuras; actualizar todo texto con números (porcentajes, días ganados, n, λ, visitas, sensibilidades). Ninguna cifra vieja del MILP ni de 2026 queda; el test son 121 días.
6. Compilar: `cd report && pdflatex reporte-caso && bibtex reporte-caso && pdflatex reporte-caso && pdflatex reporte-caso`. Cuerpo ≤ 8 páginas (las referencias empiezan en la página 9); si no cabe, mover material de apoyo a apéndices, no recortar lo pedido arriba.

Constraints:
- Sin ejemplo numérico del greedy. Sin piloto en conclusiones. "Ecobici real contra simulado" y sensibilidades siguen en Amenazas a la validez.
- No presentar etiquetas de una regla como dato medido; cada cifra sale de `numeros-caso.tex` o de `resultados.csv`.
- No tocar código de `ecosim/` (salvo el merge). Commits (de lo versionado, p. ej. `referencias.bib`) como Jero110.

Validation:
```
cd /Users/jeronimo.deli/Desktop/other/Vs/vaults/movilidad-cdmx/.worktrees/ecosim3-reporte-caso/report
grep -n -i "milp\|highs\|envolvente\|programa entero\|entero mixto\|voraz" reporte-caso.tex numeros-caso.tex tablas/*-caso.tex   # vacío
uv run python3 figs/figs_caso.py --check
pdflatex -interaction=nonstopmode reporte-caso && bibtex reporte-caso && pdflatex -interaction=nonstopmode reporte-caso && pdflatex -interaction=nonstopmode reporte-caso
grep -c "Warning.*undefined" reporte-caso.log   # 0
```
Más: página donde empiezan las referencias (debe ser 9 o antes).

Stop condition: fase 2 terminada, validaciones pasan, PDF compilado; reporte con la lista de secciones cambiadas, la página de inicio de referencias y la tabla de auditoría de fuentes.

Cross-refs: leer el reporte de `ecosim-greedy-base` (forma de paquetes, regla del simulador, columnas) y `ecosim/results/CONCLUSIONES.md` de `ecosim-greedy-runs`.
