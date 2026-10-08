"""Asignador voraz: mismas tablas de costo que el MILP, sin solver ni envolvente.

Hereda de Asignador todo menos _solve. Cada receptor (entrega) elige la cantidad
x que maximiza su ahorro menos el costo de conseguir x bicis de los donantes más
baratos por bici; se compromete y sigue con el siguiente receptor.
"""
from __future__ import annotations

import time

import numpy as np

from ecosim.asignador import Asignador


class AsignadorVoraz(Asignador):
    def _solve(self, r, u, pc, dc, ph, dh, params, remaining):
        begin = time.perf_counter()
        n, lam, V = len(r), params.lam, params.visits_per_decision
        pick, deliver = np.zeros(n, int), np.zeros(n, int)
        # Donante: costo total por k bicis = pc[k] + lam; se ordena por el mejor costo/bici.
        ratio = np.full(n, np.inf)
        kbest = np.zeros(n, int)
        for i in np.flatnonzero(r > 0):
            k = np.arange(1, r[i] + 1)
            per = (pc[i, k] + lam) / k
            kbest[i] = k[per.argmin()]
            ratio[i] = per.min()
        # Receptor: mejor ahorro neto de visita si las bicis fueran gratis.
        gain = np.full(n, -np.inf)
        for i in np.flatnonzero(u > 0):
            gain[i] = (-dc[i, 1: u[i] + 1] - lam).max()
        used, visits = np.zeros(n, bool), 0
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
        return pick, deliver, "Optimal", 0.0, time.perf_counter() - begin
