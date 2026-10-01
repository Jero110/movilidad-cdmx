# Pronósticos run 2: ¿sirve re-pronosticar?

30 días (15 evaluación + 15 selección), bloques de 60 min, ventana 05:30–00:30.
Corrección intradía: se re-escala solo lo que falta (bins de 15 min ≥ s; lo ya
transcurrido vale lo observado) con factor = clip((obs+K)/(pred+K), 0.5, 2) por
estación y por separado salidas/llegadas, **K = 5 fijado antes de ver
resultados**, sin tunear. El bloque en curso vale `observado_en_el_bloque +
factor · resto_esperado`.

Comparación justa (`accuracy_by_lead.csv`): para cada decisión t de la rejilla de
15 min (`C.decision_times`, t + L < 00:30, L = 60) y cada k = 0..5, el objetivo es
el bloque que contiene t + L + 60·k min; cada serie usa su emisión más reciente
con `issued_at ≤ t`. Todas las series se evalúan sobre los mismos
(día, estación, t, k).

MAE por estación-bloque (promedio k = 0..5):

| split / objetivo | daily | ma f15 / f60 / f180 | model f15 / f60 / f180 |
|---|---|---|---|
| eval, salidas | 2.053 | 2.102 / 2.103 / 2.099 | 2.081 / 2.081 / 2.076 |
| eval, llegadas | 1.983 | 2.024 / 2.027 / 2.026 | 2.005 / 2.005 / 2.003 |
| eval, neto | 2.161 | 2.296 / 2.291 / 2.270 | 2.278 / 2.271 / 2.250 |
| selección, salidas | 1.946 | 2.024 / 2.025 / 2.019 | 1.995 / 1.994 / 1.988 |
| selección, neto | 2.121 | 2.253 / 2.247 / 2.227 | 2.227 / 2.220 / 2.201 |

WAPE de salidas: daily 0.453 / 0.444 (eval / sel); ma 0.462–0.464; model 0.454–0.459.

**Resultado: re-pronosticar NO mejora contra `daily`, en ninguna frecuencia.** La
corrección por estación empeora el MAE ~1–4 % (`ma`) y ~1–3 % (`model`) en ambos
splits, y el neto ~4–6 %. Con la rejilla de 15 min ya se puede comparar f15 con
f60: son prácticamente iguales (diferencia < 0.3 %; f15 es incluso un poco peor
en `ma`) y f180 es el menos malo. Es decir, ninguna frecuencia "empieza a mejorar":
más frecuente = igual o peor. `model` supera a `ma` con o sin corrección, pero
`daily` queda por delante de ambos.

Lectura: los conteos por estación-hora son pequeños (WAPE ≈ 0.45, ruido de
Poisson) y el factor por estación, aun con K = 5, agrega más varianza que señal.
El reviewer diagnosticó que la corrección sí reduce ~15 % el error del total del
sistema; un factor global (fuera de alcance, no probado aquí) sería el candidato,
y debería elegirse en los días de selección.

Notas: (1) `accuracy.csv` (por distancia a la emisión) no es comparable entre
`daily` y las series re-emitidas: cubren bloques distintos (`daily` incluye la
noche, de bajo volumen); comparar con `accuracy_by_lead.csv`. (2) En `accuracy.csv`
el bloque en curso va en la cubeta 0 h. (3) Bloques `censurado` (algún snapshot con
0 bicis): MAE de salidas más alto que en los no censurados (ver `accuracy.csv`).
(4) Filtro `o_known`: en los 30 días la salida desde estación desconocida se
ignora (regla del simulador) y las llegadas de esos viajes sí cuentan; en la
corrida ignoró 0 de 1,662,776 salidas, porque el universo es la unión de las
estaciones de los 30 días. La historia previa (sin lista de estaciones por día
antes de agosto) usa "origen en el universo": no hay diferencia práctica.
