"""Greedy de una decisión: qué estaciones recogen, cuáles entregan y cuánto.

Función pura, sin solver. Recibe, por estación i = 0..n−1:

- r[i]: bicis que se pueden recoger en i a t+15 (0 si no es donante);
- u[i]: bicis que se pueden entregar en i a t+60 (0 si no es receptora);
- pc[i, k]: costo incremental (minutos-estación vacíos o llenos en la
  ventana) de recoger k bicis en i, con pc[i, 0] = 0;
- dc[i, x]: costo incremental de entregar x bicis en i, con dc[i, 0] = 0;
- lam (λ): precio de una visita, en las mismas unidades;
- V: tope de visitas de la decisión.

Procedimiento:

1. Donantes: para cada j con r[j] > 0, su mejor costo por bici
   ratio[j] = min_k (pc[j, k] + λ) / k, k = 1..r[j].
2. Receptores: para cada i con u[i] > 0, su mejor ahorro si las bicis
   fueran gratis, gain[i] = max_x (−dc[i, x] − λ), x = 1..u[i].
3. Se recorren receptores de mayor a menor gain. Se para en cuanto
   gain ≤ 0 o ya no caben dos visitas más (visitas + 2 > V); se saltan los
   receptores ya usados (como donantes de un paquete anterior).
4. Para el receptor i, los donantes libres (sin i) se ordenan por ratio.
   Para cada x = 1..u[i] se llena x tomando k = min(falta, r[j]) de cada
   donante en ese orden; el costo del paquete es
   dc[i, x] + λ + Σ_j (pc[j, k_j] + λ). Se descarta x si no se completa o
   si visitas + 1 + #donantes > V. Se queda la x con mayor −costo,
   solo si −costo > 0.
5. Se asigna el paquete (receptor, x, [(donante, k), ...]) y se marcan el
   receptor y sus donantes como usados: ninguna estación recoge y entrega
   en la misma decisión.

Costo por distancia (α, opcional). Con alfa > 0 y la matriz D[i, j] de
kilómetros en línea recta entre estaciones, cada tramo donante j → receptor i
cuesta además α·D[i, j] (minutos-estación por km):

- el orden de donantes de cada receptor i usa
  ρ_ij = min_k (pc[j, k] + λ + α·D[i, j]) / k, k = 1..r[j];
- el costo del paquete suma α·D[i, j] por cada donante que toma.

gain[i] no cambia: es el ahorro del receptor si las bicis fueran gratis.
Con alfa = 0 (o D = None) se usa el procedimiento de arriba, sin cambios.

Devuelve (pick, deliver, paquetes): bicis a recoger y a entregar por
estación y la lista de paquetes en el orden en que se asignaron. Cada
paquete equilibra recogidas y entregas.
"""
from __future__ import annotations

import numpy as np


def greedy(r, u, pc, dc, lam, V, alfa=0.0, D=None):
    """Una decisión del greedy; con `alfa > 0` y `D` suma el costo por km de cada tramo."""
    if alfa > 0 and D is not None:
        return _greedy_alfa(r, u, pc, dc, lam, V, float(alfa), np.asarray(D, float))
    n = len(r)
    pick, deliver = np.zeros(n, int), np.zeros(n, int)
    # Donante: costo total por k bicis = pc[k] + λ; se ordena por el mejor costo/bici.
    ratio = np.full(n, np.inf)
    for j in np.flatnonzero(r > 0):
        k = np.arange(1, r[j] + 1)
        ratio[j] = ((pc[j, k] + lam) / k).min()
    # Receptor: mejor ahorro neto de visita si las bicis fueran gratis.
    gain = np.full(n, -np.inf)
    for i in np.flatnonzero(u > 0):
        gain[i] = (-dc[i, 1: u[i] + 1] - lam).max()
    used, visits, paquetes = np.zeros(n, bool), 0, []
    for i in np.argsort(-gain):
        if gain[i] <= 0 or visits + 2 > V:
            break
        if used[i]:
            continue
        donors = [j for j in np.argsort(ratio) if not used[j] and j != i and np.isfinite(ratio[j])]
        best = (0.0, 0, [])  # (ahorro neto, x, [(donante, k)])
        for x in range(1, u[i] + 1):
            need, cost, take = x, dc[i, x] + lam, []
            for j in donors:
                if need == 0:
                    break
                k = min(need, r[j])
                cost += pc[j, k] + lam
                take.append((j, k))
                need -= k
            if need or visits + 1 + len(take) > V:
                continue
            if -cost > best[0]:
                best = (-cost, x, take)
        if best[1]:
            deliver[i] = best[1]
            used[i] = True
            visits += 1
            for j, k in best[2]:
                pick[j] = k
                used[j] = True
                visits += 1
            paquetes.append((int(i), int(best[1]), [(int(j), int(k)) for j, k in best[2]]))
    return pick, deliver, paquetes


def _greedy_alfa(r, u, pc, dc, lam, V, alfa, D):
    """El mismo greedy con costo α·D[i, j] por tramo donante j → receptor i (ver el docstring del módulo)."""
    n = len(r)
    pick, deliver = np.zeros(n, int), np.zeros(n, int)
    gain = np.full(n, -np.inf)
    for i in np.flatnonzero(u > 0):
        gain[i] = (-dc[i, 1: u[i] + 1] - lam).max()
    donors_all = np.flatnonzero(r > 0)
    ks = np.arange(1, pc.shape[1])
    used, visits, paquetes = np.zeros(n, bool), 0, []
    for i in np.argsort(-gain):
        if gain[i] <= 0 or visits + 2 > V:
            break
        if used[i]:
            continue
        J = donors_all[(~used[donors_all]) & (donors_all != i)]
        if len(J) == 0:
            continue
        # Estación sin coordenadas (D = NaN): tramo imposible, el donante queda al final y nunca se toma.
        leg = np.nan_to_num(lam + alfa * D[i, J], nan=np.inf)
        tot = pc[J][:, 1:] + leg[:, None]
        tot = np.where(ks[None, :] <= r[J][:, None], tot / ks[None, :], np.inf)
        donors = J[np.argsort(tot.min(axis=1), kind="stable")]
        best = (0.0, 0, [])  # (ahorro neto, x, [(donante, k)])
        for x in range(1, u[i] + 1):
            need, cost, take = x, dc[i, x] + lam, []
            for j in donors:
                if need == 0:
                    break
                k = min(need, r[j])
                cost += pc[j, k] + lam + alfa * D[i, j]
                take.append((j, k))
                need -= k
            if need or visits + 1 + len(take) > V:
                continue
            if -cost > best[0]:
                best = (-cost, x, take)
        if best[1]:
            deliver[i] = best[1]
            used[i] = True
            visits += 1
            for j, k in best[2]:
                pick[j] = k
                used[j] = True
                visits += 1
            paquetes.append((int(i), int(best[1]), [(int(j), int(k)) for j, k in best[2]]))
    return pick, deliver, paquetes
