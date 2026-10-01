# ecosim run 2 — apéndice de CONCLUSIONES

Detalle que se sacó de `CONCLUSIONES.md` para que cupiera en 2 páginas. Las tablas completas están en
`tablas.md`.

## Dónde falla (mejor real = `daily`; `donde_falla.csv`, `ef_por_bloque.csv`)

- Los 20 peores estación × hora son solo 1.6% del E+F de `daily`: la falla está repartida.
- **Hubs de la mañana** (07:30–08:30): 273-274, 271-272, 268-269, 266-267 y la 176, los mismos del
  run 1. Ahí el oracle falla casi igual (572 contra 655 en 273-274 a las 07:30) y Ecobici no (112):
  son zonas valet, con varias recargas por hora. El pronóstico se queda corto en la punta (878 salidas
  contra 1,065, con 7 de 15 días censurados).
- **Vaciados de la tarde** (17:30–18:30): 261, 014, 022, 013 y 019. Ecobici también los atiende mejor.
- **Estado de las 05:30:** la 463 amanece vacía y la 176 llena. Además, las estaciones marcadas fuera
  de servicio pueden tener viajes reales (091 y 168 el 09-03): la política no las toca y el replay sí.

## Comparación con el run 1 (misma mañana 05:30–12:30, mismos 15 días)

| brazo | run 1 | run 2 (mañana) | diferencia |
|---|---|---|---|
| no hacer nada (dañadas fijas / dinámicas) | 72,477 | 72,477 / 74,580 | igual / +3% |
| Ecobici (`stock`, dañadas fijas / principal) | 34,325 | 34,325 / 38,611 | igual / +12% |
| oracle | 13,798 (H 3, in-sample) | 12,015 | −13% |
| daily | 19,647 (H 3, in-sample) | 16,376 | −17% |
| ma / model | 18,822 / 18,888 | 18,993 / 18,690 | ≈ igual |

- **Reproducción exacta:** con el método del run 1 salen en la mañana los mismos 72,477 y 34,325.
- **Dañadas dinámicas:** suben la vacía de todos: +3% en la mañana sin rebalanceo y +22% en el oracle
  del día completo (21.7k → 26.6k). En el replay de Ecobici, la versión justa (taller como evento,
  sin ±1) le sube 12% en la mañana. **Contradicción con el run 1:** "E subestimada 18.5%" era real y
  venía de las dañadas fijas; ahora es 6.6% en la mañana.
- **Bodega finita:** en el run 1, `ma` terminaba en +324 con tope teórico de 180. Ahora nunca pasa de
  ±632 sobre lo aplicado. Contra ilimitada, cuesta +15% al oracle y +14% a `daily`.
- **Tope por movimiento (22):** cuesta +5% al oracle y +1% a `daily` contra 42. En el run 1 no había
  tope (máximo 74).
- **±1:** en el run 1, 43% de los movimientos de Ecobici eran pares que se deshacen. Quitarlos baja el
  tope por hora de 474 a 246 y los movimientos de Ecobici de ~5k a ~2.4k por día, pero casi no cambia
  su E+F (−4% con ellos) ni el nuestro (el tope no aprieta).
- **`daily` pasa de último a mejor brazo real:** ahora es un pronóstico de día completo y usa h = 5. En
  el run 1 era un reparto uniforme del total con H ≤ 3.
- Todas las decisiones del run 1 juntas (`diag_como_run1`: dañadas fijas, bodega ilimitada, sin tope
  por movimiento, tope con ±1) dan en la mañana 9,889 (oracle) y 13,719 (`daily`).

## Supuestos de este paso
- Cota de retiro elegida por el E+F medio en toda la rejilla de λ (regla fijada antes de ver resultados).
- h_max = el mayor punto de parada (oracle 6, ma 4).
- Oracle con f = 60, porque su contenido no depende de f.
- Bodega con taller = ⌈73.96⌉ = 74.
- 8 procesos de 1 thread (los 2 cupos del semáforo).
- HiGHS sin corte por reloj (600 s en `run.py`). El corte de 4 s del Asignador dejaba 68 de ~100k
  decisiones dependientes de la carga, y la hibernación de la máquina durante `--all` cambió 7 corridas.
  Por eso `--all` no reproducía. Con 600 s ninguna decisión se corta: la más lenta tarda 333–361 s, todas
  las de más de 60 s son de λ = 5, y los brazos principales tardan como máximo 3.9 s. Si alguna decisión no
  llega al óptimo, `run_one` lanza error.
- Reproducibilidad: la corrida por pasos y `--all` coinciden en las 1,392 filas sin corte; las 48 con
  corte se recalcularon aparte y coinciden con `--all`. No cubre `v1/*`.
- No se corrigió ningún módulo de otro worker.

## Siguientes pasos (detalle)

1. **Pronóstico.** Es el margen grande (~20k). Probar un factor intradía **global** (no por estación),
   elegido en selección, y demanda no censurada en los hubs de la mañana.
2. **λ.** Kneedle elige un punto caro en el día completo (con λ = 5, el oracle mejora 25%). Proponer
   una regla fuera de muestra (λ de menor E+F en selección, o λ por hora de horizonte) y decidirla
   antes del run 3. Hay que resolver el cómputo: con λ = 5 una decisión tarda hasta 66 s en evaluación y
   333 s en selección, y el plan pide ≤ 5 s.
3. **V1 de noche.** Replay con la hora dentro del hueco (punto medio o t1 cuando el hueco > 20 min), o
   un feed más fino de 18:00 a 21:30.
4. **Bodega.** Separar el regreso de reparadas del "poner" de rebalanceo para calibrar mejor 632 contra
   74: es la sensibilidad que más mueve a las políticas.
5. **Hubs y primera hora.** Varias recargas por hora en zonas valet (L corto o reserva fija), y decisión
   nocturna para el estado de las 05:30.
6. **Rutas y camiones.** Con movimientos del orden de los de Ecobici, falta ver si una flota real los
   puede hacer.
