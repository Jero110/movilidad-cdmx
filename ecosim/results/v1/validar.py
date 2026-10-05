"""V1 reproducible: replay vs E/F oficial de medición, 15 días de selección.

Uso: uv run python -m ecosim.results.v1.validar (bajo scripts/heavy.py).
Las causas son categorías exclusivas por estación-minuto, no contrafactuales
independientes; un residual 'escalón' no es atribución causal.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ecosim import config as C, contracts as K, data, sim

HERE = Path(__file__).resolve().parent
OFFICIAL = pd.read_csv(C.REPO_ROOT / "ecosim/results/medicion/ecobici_observado.csv").set_index("day")


def span(times, start, end):
    lo = max(0, int(np.floor((pd.Timestamp(start) - times[0]) / pd.Timedelta(minutes=1))))
    hi = min(len(times), int(np.ceil((pd.Timestamp(end) - times[0]) / pd.Timedelta(minutes=1))))
    return slice(lo, hi)


def observed(day, r):
    sn = data.snapshots(day)
    sn = sn.assign(t=sn.t - pd.Timedelta(seconds=C.GBFS_COMMIT_LAG_S))
    times = r.extra["minutes"]
    names = list(r.extra["stations"])
    start, end = C.day_bounds(day)
    init = data.initial_state(day).set_index("short_name")
    E = np.zeros((len(times), len(names)), bool)
    F = np.zeros_like(E)
    gap = np.zeros_like(E)
    blank = np.zeros_like(E)
    mins = times.to_numpy(dtype="datetime64[ns]")
    for j, name in enumerate(names):
        g = sn[sn.short_name == name].sort_values("t")
        gt = g.t.to_numpy(dtype="datetime64[ns]")
        k = np.searchsorted(gt, mins, side="right") - 1
        valid = k >= 0
        b = np.full(len(times), int(init.loc[name, "bikes"]), dtype=int)
        d = np.full(len(times), int(init.loc[name, "disabled"]), dtype=int)
        dd = np.full(len(times), int(init.loc[name, "docks_disabled"]), dtype=int)
        cp = np.full(len(times), int(init.loc[name, "cap"]), dtype=int)
        if valid.any():
            b[valid] = g.bikes.to_numpy()[k[valid]]
            d[valid] = g.disabled.to_numpy()[k[valid]]
            dd[valid] = g.docks_disabled.to_numpy()[k[valid]]
            cp[valid] = g.cap.to_numpy()[k[valid]]
            blank[valid, j] = g.blank.to_numpy()[k[valid]]
            gap[valid, j] = (mins[valid] - gt[k[valid]]) > np.timedelta64(30, "m")
        E[:, j] = b == 0
        F[:, j] = b + d + dd >= cp
    return E, F, gap, blank


def neutralizar_neto_diario(orders, net):
    """Quita |net| bicis, proporcionalmente de visitas del mismo signo.

    No asegura neto *aplicado* cero porque los recortes físicos cambian en el
    contrafactual. Se informa ese residuo, sin inventar stock para cuadrarlo.
    """
    sign = 1 if net > 0 else -1
    caps = [max(0, sign * o.delta) for o in orders]
    cuts = sim._spread(min(abs(net), sum(caps)), caps)
    return [K.Order(o.issued_at, o.pickup_at, o.delivery_at, o.short_name, o.delta - sign * cut)
            for o, cut in zip(orders, cuts) if o.delta - sign * cut]


def diagnose(day):
    orders = sim.ecobici_orders(day)
    r = sim.run_day(day, orders=orders, arm="ecobici")
    early = sim.run_day(day, orders=sim.ecobici_orders(day, when="t0"), arm="ecobici_t0")
    neutral = sim.run_day(day, orders=orders, arm="ecobici_neutral_intervalo", neutralize_replay=True)
    daily_neutral = sim.run_day(day, orders=neutralizar_neto_diario(orders, r.extra["replay_neto"]),
                                arm="ecobici_neto_diario_aproximado")
    observed_E, observed_F, gap, blank = observed(day, r)
    times = r.extra["minutes"]
    names = list(r.extra["stations"])
    indices = {s: i for i, s in enumerate(names)}
    sim_E = r.extra["bikes_minute"] == 0
    st = data.initial_state(day).set_index("short_name").reindex(names)
    sim_F = (r.extra["bikes_minute"] + r.extra["disabled_minute"] +
             st.docks_disabled.to_numpy()[None, :] >= st.cap.to_numpy()[None, :])
    detour = np.zeros_like(sim_E)
    pairs = np.zeros_like(sim_E)
    damage = np.zeros_like(sim_E)
    for row in r.extra["detours"].itertuples():
        for s in (row.orig, row.to):
            if s in indices:
                detour[span(times, row.t, row.t + pd.Timedelta(minutes=30)), indices[s]] = True
    for row in r.extra["damage_applied"].itertuples():
        if row.n > row.applied and row.short_name in indices:
            damage[span(times, row.t, row.t + pd.Timedelta(minutes=30)), indices[row.short_name]] = True
    mv = pd.read_parquet(C.DERIVED / "ecobici_moves.parquet")
    mv = mv[(mv.day == day) & mv.par]
    for row in mv.itertuples():
        if row.short_name in indices:
            pairs[span(times, row.t0, row.t1), indices[row.short_name]] = True
    bands = {"manana": (5, 12), "tarde": (12, 18), "noche": (18, 25)}
    hour = np.where(times.hour < 5, times.hour + 24, times.hour)
    rows = []
    examples = []
    timing = []
    for band, (lo, hi) in bands.items():
        sel = (hour >= lo) & (hour < hi)
        # Prioridad exclusiva: hueco/blank > desvío > par > daño > escalón.
        labels = np.full(sim_E.shape, "escalon_foto", dtype="U20")
        labels[damage] = "danada_no_aplicable"
        labels[pairs] = "par_eliminado"
        labels[detour] = "desvio"
        labels[gap | blank] = "hueco_o_blanco"
        for kind, s, o in (("E", sim_E, observed_E), ("F", sim_F, observed_F)):
            other = (early.extra["bikes_minute"] == 0 if kind == "E" else
                     early.extra["bikes_minute"] + early.extra["disabled_minute"] +
                     st.docks_disabled.to_numpy()[None, :] >= st.cap.to_numpy()[None, :])
            timing.append(dict(day=day, franja=band, metrica=kind,
                               t0_menos_t1=int(other[sel].sum() - s[sel].sum())))
            difference = s.astype(np.int8) - o.astype(np.int8)
            for cause in ("hueco_o_blanco", "desvio", "par_eliminado", "danada_no_aplicable", "escalon_foto"):
                mask = sel[:, None] & (labels == cause)
                rows.append(dict(day=day, franja=band, metrica=kind, causa=cause,
                                 diferencia_min=int(difference[mask].sum()),
                                 desacuerdos_min=int(np.count_nonzero(difference[mask]))))
            by_station = difference[sel].sum(axis=0)
            for j in np.argsort(-np.abs(by_station))[:3]:
                examples.append(dict(day=day, franja=band, metrica=kind, estacion=names[j],
                                     diferencia_min=int(by_station[j])))
    feed_E, feed_F = float(OFFICIAL.loc[day, "E"]), float(OFFICIAL.loc[day, "F"])
    # La malla por minuto clasifica contextos; la medición oficial integra
    # cada duración exacta entre commits y no redondea a minutos enteros.
    for kind, local, official in (("E", observed_E, feed_E), ("F", observed_F, feed_F)):
        rows.append(dict(day=day, franja="ajuste_definicion", metrica=kind,
                         causa="escalon_minuto_vs_feed_oficial",
                         diferencia_min=float(local.sum() - official), desacuerdos_min=0))
    daily = dict(day=day, E_sim=r.E, F_sim=r.F, E_feed=feed_E, F_feed=feed_F,
                 E_diferencia=float(r.E - feed_E), F_diferencia=float(r.F - feed_F),
                 E_neutral=neutral.E, F_neutral=neutral.F, EF_neutral=neutral.EF,
                 replay_neto=int(r.extra["replay_neto"]), flota_inicio=int(r.extra["initial_total"]),
                 neutralizacion_bicis=sum(abs(x[2]) for x in neutral.extra["neutralization"]),
                 E_neutral_dia=daily_neutral.E, F_neutral_dia=daily_neutral.F,
                 residuo_neto_dia=daily_neutral.extra["replay_neto"],
                 desvios_salida=r.desvios_salida, desvios_llegada=r.desvios_llegada,
                 danadas_no_aplicables=r.danadas_no_aplicables,
                 huecos_min=int(gap.sum()), blancos_min=int(blank.sum()),
                 pares_min=int(pairs.sum()), replay_externo=r.extra["replay_external"])
    return daily, rows, examples, timing


def main():
    days = [x["day"] for x in json.loads(C.DAYS_JSON.read_text())["seleccion"]]
    daily, causes, examples, timing = [], [], [], []
    for day in days:
        d, c, e, tm = diagnose(day)
        daily.append(d)
        causes.extend(c)
        examples.extend(e)
        timing.extend(tm)
        print(day, d["E_diferencia"], d["F_diferencia"], flush=True)
    pd.DataFrame(daily).to_csv(HERE / "run3_v1_por_dia.csv", index=False)
    pd.DataFrame(daily)[["day", "flota_inicio", "replay_neto", "E_sim", "F_sim",
                          "E_neutral", "F_neutral", "EF_neutral", "neutralizacion_bicis",
                          "E_neutral_dia", "F_neutral_dia", "residuo_neto_dia"]].to_csv(
                              HERE / "run3_replay_neto.csv", index=False)
    pd.DataFrame(causes).to_csv(HERE / "run3_v1_causas.csv", index=False)
    pd.DataFrame(examples).to_csv(HERE / "run3_v1_estaciones.csv", index=False)
    pd.DataFrame(timing).to_csv(HERE / "run3_v1_hora_movimiento.csv", index=False)
    df = pd.DataFrame(daily)
    print(df[["E_sim", "F_sim", "E_feed", "F_feed", "E_diferencia", "F_diferencia"]].sum().to_string())
    print(pd.DataFrame(causes).groupby(["franja", "metrica", "causa"]).diferencia_min.sum().to_string())
    print(pd.DataFrame(timing).groupby(["franja", "metrica"]).t0_menos_t1.sum().to_string())


if __name__ == "__main__":
    main()
