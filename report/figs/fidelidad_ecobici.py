"""Ecobici real (feed) contra Ecobici medido (simulado): desvíos y sus causas.

    uv run python3 report/figs/fidelidad_ecobici.py          # escribe fidelidad_ecobici.json
    uv run python3 report/figs/fidelidad_ecobici.py --check  # recalcula y exige el mismo JSON

Solo análisis sobre lo que ya existe; no corre el simulador. Entradas (solo
lectura):

- `data/derived/ecosim/replay/<día>/{dia,<brazo>}.json`: los 16 días que
  tienen los siete brazos simulados (4 por mes, septiembre a diciembre de 2025).
  `snap[k]` resume el cuarto de hora que termina en la foto k; `est[k]` son
  filas `[i, entregadas, recogidas, a_rentable, a_no_rentable, salidas,
  llegadas, devueltas]`; `desvios[k]` son `[viaje, "salida"|"llegada",
  i_orig, i_real, metros]`; `bikes[k]` son las bicis disponibles simuladas en
  `start + 15 k` min.
- `data/derived/ecosim/ecobici_moves.parquet` (movimientos de Ecobici inferidos,
  solo para el ejemplo de la estación 273-274).
- `ecosim.data.snapshots(día)` (fotos reales del feed, sin las fotos en blanco)
  y `ecosim.data.trips(día)`.
- `ecosim/results/resultados.csv` (tag=prueba, días de prueba) y
  `ecosim/results/medicion/ecobici_observado.csv` (E y F observados en las
  fotos, mismos días).

Salida: `report/figs/fidelidad_ecobici.json`, que lee `numeros_run3.py`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ecosim import config as C, data  # noqa: E402

DERIVED = ROOT / 'data/derived/ecosim'
REPLAY = DERIVED / 'replay'
R = ROOT / 'ecosim/results'
OUT = Path(__file__).with_suffix('.json')
ARMS = ['sin_rebalanceo', 'ecobici', 'ma_diaria', 'lgbm_diario', 'lgbm_directo',
        'oraculo_diario', 'oraculo_directo']
TOP = .10        # estaciones de mucha demanda: el decil con más viajes en los 16 días
N_PEAK = 6       # horas pico: las seis horas de reloj con más salidas en los 16 días
LAG = pd.Timedelta(seconds=C.GBFS_COMMIT_LAG_S)   # hora del estado = commit − 30 s


def r(x, d=4):
    return round(float(x), d)


def replay_days():
    return sorted(p.name for p in REPLAY.iterdir()
                  if p.is_dir() and all((p / f'{a}.json').exists() for a in ARMS))


def load(day, name):
    return json.loads((REPLAY / day / f'{name}.json').read_text())


def real_snaps(day):
    """Fotos reales sin blancos, con la hora del estado (commit − 30 s)."""
    s = data.snapshots(day)
    s = s[~s['blank']].copy()
    s['ts'] = s['t'] - LAG
    return s.sort_values('ts')


def por_brazo(days):
    """Desvíos por viaje simulado en los 16 días: Σ desvíos / Σ salidas de snap[k]."""
    out = {}
    for a in ARMS:
        ds = dl = sal = 0
        for d in days:
            for s in load(d, a)['snap']:
                ds += s['desvios_salida']
                dl += s['desvios_llegada']
                sal += s['salidas']
        out[a] = {'desvios_salida': ds, 'desvios_llegada': dl, 'salidas': sal,
                  'pct': r((ds + dl) / sal * 100, 2)}
    return out


def resultados_152():
    """Medias por día en los días de prueba: E, F y desvíos por brazo; E y F del feed.

    Las salidas por día se cuentan con `data.trips` (salida dentro de 05:00–00:30
    y en una estación del día), igual que el simulador.
    """
    res = pd.read_csv(R / 'resultados.csv', dtype={'day': str})
    res = res[res['tag'] == 'prueba']
    days = sorted(res['day'].unique())
    sal = {}
    for d in days:
        t = data.trips(d)
        lo, hi = (pd.Timestamp(x) for x in C.day_bounds(d))
        sal[d] = int(((t['t_dep'] >= lo) & (t['t_dep'] < hi) & t['o_known']).sum())
    salidas = float(np.mean([sal[d] for d in days]))
    arms = {}
    for a in ARMS:
        x = res[res['arm'] == a]
        assert sorted(x['day']) == days
        desv = (x['desvios_salida'] + x['desvios_llegada']).mean()
        arms[a] = {'E': r(x['E'].mean(), 1), 'F': r(x['F'].mean(), 1), 'EF': r(x['EF'].mean(), 1),
                   'desvios': r(desv, 1), 'pct': r(desv / salidas * 100, 2)}
    obs = pd.read_csv(R / 'medicion/ecobici_observado.csv', dtype={'day': str}).set_index('day')
    obs = obs.loc[days]
    feed = {'E': r(obs['E'].mean(), 1), 'F': r(obs['F'].mean(), 1),
            'EF': r((obs['E'] + obs['F']).mean(), 1)}
    return {'dias': len(days), 'salidas_dia': r(salidas, 1), 'brazos': arms, 'feed': feed}, sal


def causa_resolucion(days):
    """Causa 1: desvíos en una estación con movimiento de Ecobici en el mismo cuarto de hora.

    Una salida desviada cuenta si su estación de origen recibió una entrega de
    Ecobici en ese cuarto; una llegada desviada, si tuvo una recogida. Como
    referencia, la misma proporción para todas las salidas y llegadas.
    """
    c = dict.fromkeys(['sal', 'sal_ent', 'lle', 'lle_rec', 'tsal', 'tsal_ent', 'tlle', 'tlle_rec'], 0)
    for d in days:
        j = load(d, 'ecobici')
        for est, dv in zip(j['est'], j['desvios']):
            ent = {row[0] for row in est if row[1] > 0}
            rec = {row[0] for row in est if row[2] > 0}
            for i, e, rc, _, _, s, l, _ in est:
                c['tsal'] += s
                c['tlle'] += l
                c['tsal_ent'] += s if e > 0 else 0
                c['tlle_rec'] += l if rc > 0 else 0
            for _, kind, io, _, _ in dv:
                if kind == 'salida':
                    c['sal'] += 1
                    c['sal_ent'] += io in ent
                else:
                    c['lle'] += 1
                    c['lle_rec'] += io in rec
    return {**c, 'pct_sal': r(c['sal_ent'] / c['sal'] * 100, 1),
            'pct_lle': r(c['lle_rec'] / c['lle'] * 100, 1),
            'pct_tsal': r(c['tsal_ent'] / c['tsal'] * 100, 1),
            'pct_tlle': r(c['tlle_rec'] / c['tlle'] * 100, 1)}


def causa_cero(days, snaps):
    """Causa 2: salidas reales desde una estación que la última foto previa marcaba en 0.

    Cruce `merge_asof` hacia atrás por estación entre la hora de salida y la
    hora del estado de cada foto. Se pierden las salidas sin foto previa ese día.
    """
    rows, ages = [], []
    for d in days:
        t = data.trips(d)
        lo, hi = (pd.Timestamp(x) for x in C.day_bounds(d))
        t = t[(t['t_dep'] >= lo) & (t['t_dep'] < hi)].sort_values('t_dep')
        s = snaps[d][['short_name', 'ts', 'bikes', 'disabled']].rename(columns={'short_name': 'o'})
        x = pd.merge_asof(t, s, left_on='t_dep', right_on='ts', by='o', direction='backward')
        x = x.dropna(subset=['ts'])
        z = x[x['bikes'] == 0]
        age = (z['t_dep'] - z['ts']).dt.total_seconds() / 60
        ages.append(age)
        rows.append({'dia': d, 'salidas': len(x), 'en_cero': len(z),
                     'pct': r(len(z) / len(x) * 100, 2),
                     'pct_no_rentables': r((z['disabled'] > 0).mean() * 100, 1),
                     'edad_mediana_min': r(age.median(), 1)})
    tot = sum(x['salidas'] for x in rows)
    cero = sum(x['en_cero'] for x in rows)
    nr = sum(x['en_cero'] * x['pct_no_rentables'] / 100 for x in rows)
    return {'por_dia': rows, 'salidas': tot, 'en_cero': cero, 'pct': r(cero / tot * 100, 2),
            'pct_min': min(x['pct'] for x in rows), 'pct_max': max(x['pct'] for x in rows),
            'pct_no_rentables': r(nr / cero * 100, 1),
            'edad_mediana_min': r(pd.concat(ages).median(), 1)}


def causa_arrastre(days, snaps):
    """Causa 3: inventario simulado de Ecobici medido contra el feed a lo largo del día.

    En cada cuarto t se compara `bikes[k]` con la última foto real de estado ≤ t
    de la misma estación, corregida con las llegadas menos salidas de viajes en
    (foto, t]. Así no cuenta como diferencia que la foto sea vieja: el simulador
    aplica los viajes a su minuto y los movimientos de Ecobici a la hora de la
    foto, igual que esta referencia. Lo que queda es error acumulado (desvíos,
    recortes, cambios de etiqueta que no se pudieron aplicar).
    """
    per = []
    for d in days:
        dia, j = load(d, 'dia'), load(d, 'ecobici')
        names = dia['stations']['short_name']
        start = pd.Timestamp(dia['start'])
        tr = dia['trips']
        ev = pd.concat([pd.DataFrame({'i': tr['d'], 'm': tr['arr'], 'v': 1}),
                        pd.DataFrame({'i': tr['o'], 'm': tr['dep'], 'v': -1})])
        ev = ev[ev['i'] >= 0].sort_values('m')
        cum = {i: (g['m'].to_numpy(), np.cumsum(g['v'].to_numpy())) for i, g in ev.groupby('i')}

        def net(i, a, b):
            """Llegadas − salidas de viajes de la estación i con minuto en (a, b]."""
            if i not in cum:
                return 0
            m, c = cum[i]
            ia, ib = np.searchsorted(m, a, 'right'), np.searchsorted(m, b, 'right')
            return int((c[ib - 1] if ib else 0) - (c[ia - 1] if ia else 0))

        s = snaps[d]
        pos = {n: i for i, n in enumerate(names)}
        s = s[s['short_name'].isin(pos)]
        sm = ((s['ts'] - start) / pd.Timedelta(minutes=1)).to_numpy()
        grp = {}
        for idx, (n, m, b) in enumerate(zip(s['short_name'], sm, s['bikes'])):
            grp.setdefault(pos[n], ([], []))
            grp[pos[n]][0].append(m)
            grp[pos[n]][1].append(b)
        for k, bk in enumerate(j['bikes']):
            tk = 15 * k
            n_dif = tot = n_st = raw_dif = raw_tot = 0
            for i, (ms, bs) in grp.items():
                q = np.searchsorted(ms, tk, 'right') - 1
                if q < 0:
                    continue
                ref = bs[q] + net(i, ms[q], tk)
                dif = abs(bk[i] - ref)
                n_st += 1
                n_dif += dif > 0
                tot += dif
                raw = abs(bk[i] - bs[q])     # contra la foto tal cual, sin corregir su antigüedad
                raw_dif += raw > 0
                raw_tot += raw
            if n_st:
                per.append({'dia': d, 'k': k, 'hora': (start + pd.Timedelta(minutes=tk)).strftime('%H:%M'),
                            'estaciones': n_st, 'difieren': n_dif, 'dif_media': tot / n_st,
                            'difieren_crudo': raw_dif, 'dif_media_crudo': raw_tot / n_st})
    p = pd.DataFrame(per)
    cols = ['estaciones', 'difieren', 'dif_media', 'difieren_crudo', 'dif_media_crudo']
    g = p.groupby(['k', 'hora'])[cols].mean().reset_index()
    h = g[g['k'] >= 4]   # desde las 06:00: antes casi no hay viajes ni movimientos
    return {'estaciones_media': r(g['estaciones'].mean(), 1),
            'difieren_min': r(h['difieren'].min(), 0), 'difieren_max': r(h['difieren'].max(), 0),
            'dif_media_min': r(h['dif_media'].min(), 2), 'dif_media_max': r(h['dif_media'].max(), 2),
            'difieren_0600': r(g.loc[g['hora'] == '06:00', 'difieren'].iloc[0], 0),
            'difieren_media': r(h['difieren'].mean(), 0), 'dif_media_dia': r(h['dif_media'].mean(), 2),
            'crudo_difieren_min': r(h['difieren_crudo'].min(), 0), 'crudo_difieren_max': r(h['difieren_crudo'].max(), 0),
            'crudo_dif_media_min': r(h['dif_media_crudo'].min(), 2), 'crudo_dif_media_max': r(h['dif_media_crudo'].max(), 2),
            'por_hora': [{'hora': x.hora, 'difieren': r(x.difieren, 1), 'dif_media': r(x.dif_media, 3)}
                         for x in g.itertuples() if x.hora.endswith(':00')]}


def demanda(days):
    """¿Los desvíos extra de las políticas caen en estaciones de mucha demanda y en horas pico?

    Mucha demanda: el decil de estaciones con más salidas + llegadas en los 16
    días. Horas pico: las seis horas de reloj con más salidas. Minutos E y F por
    grupo: muestras cada 15 min de `bikes`/`dis` × 15 (aproximación; el total
    exacto por minuto solo existe para todo el sistema).
    """
    names = load(days[0], 'dia')['stations']['short_name']
    trips = []
    for d in days:
        dia = load(d, 'dia')
        assert dia['stations']['short_name'] == names
        trips.append(pd.DataFrame(dia['trips']))
    t = pd.concat(trips)
    in_w = lambda m: (m >= 0) & (m < C.WINDOW_MIN)  # noqa: E731
    dem = pd.concat([t.loc[in_w(t['dep']) & (t['o'] >= 0), 'o'],
                     t.loc[in_w(t['arr']) & (t['d'] >= 0), 'd']]).value_counts()
    n_top = int(round(len(names) * TOP))
    top = set(dem.index[:n_top])
    dep = t.loc[in_w(t['dep']) & (t['o'] >= 0)]
    hour = ((dep['dep'] // 60).astype(int) + C.DAY_START.hour) % 24
    hs = hour.value_counts()
    peak = sorted(int(h) for h in hs.index[:N_PEAK])
    out = {'estaciones_top': n_top, 'estaciones': len(names),
           'pct_viajes_top': r(dem.loc[list(top)].sum() / dem.sum() * 100, 1),
           'horas_pico': peak, 'pct_salidas_pico': r(hs.loc[peak].sum() / hs.sum() * 100, 1),
           'brazos': {}}
    for a in ARMS:
        n = n_t = n_p = 0
        e_top = e_rest = f_top = f_rest = 0
        for d in days:
            dia, j = load(d, 'dia'), load(d, a)
            for k, dv in enumerate(j['desvios']):
                # desvios[k] ocurren en el cuarto (k−1, k]: su hora es la de 15 k − 1 min.
                h = (int((15 * k - 1) // 60) + C.DAY_START.hour) % 24
                for _, _, io, _, _ in dv:
                    n += 1
                    n_t += io in top
                    n_p += h in peak
            st = dia['stations']
            cap, dd = np.array(st['cap']), np.array(st['docks_disabled'])
            istop = np.isin(np.arange(len(names)), list(top))
            b, di = np.array(j['bikes']), np.array(j['dis'])
            E, F = b == 0, b + di + dd >= cap
            e_top += 15 * E[:, istop].sum()
            e_rest += 15 * E[:, ~istop].sum()
            f_top += 15 * F[:, istop].sum()
            f_rest += 15 * F[:, ~istop].sum()
        nd = len(days)
        out['brazos'][a] = {'desvios_dia': r(n / nd, 1), 'desvios_top_dia': r(n_t / nd, 1),
                            'desvios_pico_dia': r(n_p / nd, 1),
                            'EF_top': r((e_top + f_top) / nd, 1), 'EF_resto': r((e_rest + f_rest) / nd, 1),
                            'E_top': r(e_top / nd, 1), 'E_resto': r(e_rest / nd, 1)}
    eco = out['brazos']['ecobici']
    for a, v in out['brazos'].items():
        extra = v['desvios_dia'] - eco['desvios_dia']
        v['extra_dia'] = r(extra, 1)
        v['extra_pct_top'] = r((v['desvios_top_dia'] - eco['desvios_top_dia']) / extra * 100, 1) if extra > 0 else None
        v['extra_pct_pico'] = r((v['desvios_pico_dia'] - eco['desvios_pico_dia']) / extra * 100, 1) if extra > 0 else None
        v['pct_desvios_top'] = r(v['desvios_top_dia'] / v['desvios_dia'] * 100, 1)
        v['pct_desvios_pico'] = r(v['desvios_pico_dia'] / v['desvios_dia'] * 100, 1)
    return out


EJ_DIA, EJ_EST, EJ_K = '2025-09-01', '273-274', 16   # cuarto 08:45–09:00 (cierra en la foto k = 16)


def ejemplo():
    """Caso extremo de la causa 1: estación 273-274 (Buenavista), 2025-09-01, 08:45–09:00.

    Reúne, para el texto: las fotos reales de la estación, los movimientos de
    Ecobici inferidos cerca de esa hora (`ecobici_moves.parquet`), las salidas
    reales del cuarto y la cadena de desvíos del simulador en orden de tiempo.
    """
    dia, j = load(EJ_DIA, 'dia'), load(EJ_DIA, 'ecobici')
    names = dia['stations']['short_name']
    pos = {n: i for i, n in enumerate(names)}
    start = pd.Timestamp(dia['start'])
    lo, hi = start + pd.Timedelta(minutes=15 * (EJ_K - 1)), start + pd.Timedelta(minutes=15 * EJ_K)
    hm = lambda t: pd.Timestamp(t).strftime('%H:%M')  # noqa: E731
    st = data.stations(C.as_date(EJ_DIA)).set_index('short_name')
    s = data.snapshots(EJ_DIA)
    s = s[(s['short_name'] == EJ_EST) & (s['t'] >= lo - pd.Timedelta(minutes=20)) & (s['t'] <= hi + pd.Timedelta(minutes=10))]
    fotos = [{'hora': hm(x.t - LAG), 'disponibles': int(x.bikes), 'no_rentables': int(x.disabled)}
             for x in s.itertuples()]
    trip_dep = dict(zip(dia['trips']['id'], dia['trips']['dep']))
    cadena = sorted((trip_dep[tid], names[b], m) for tid, kind, a, b, m in j['desvios'][EJ_K]
                    if kind == 'salida' and names[a] == EJ_EST)
    orden, vistos = [], {}
    for _, dest, m in cadena:
        if dest not in vistos:
            vistos[dest] = len(orden)
            orden.append({'estacion': dest, 'metros': int(round(m)), 'desvios': 0})
        orden[vistos[dest]]['desvios'] += 1
    moved = pd.read_parquet(DERIVED / 'ecobici_moves.parquet', columns=['day', 'short_name', 't1', 'delta', 'par'])
    moved = moved[(moved['day'].astype(str) == EJ_DIA) & ~moved['par'] & (moved['delta'] != 0)
                  & (moved['t1'] >= lo - pd.Timedelta(minutes=15)) & (moved['t1'] <= hi + pd.Timedelta(minutes=10))
                  & moved['short_name'].isin([EJ_EST] + [o['estacion'] for o in orden])]
    t = data.trips(EJ_DIA)
    sal = t[(t['o'] == EJ_EST) & (t['t_dep'] >= lo) & (t['t_dep'] < hi)]
    est = {row[0]: row for row in j['est'][EJ_K]}
    vecina = orden[[o['estacion'] for o in orden].index('265')] if '265' in vistos else None
    return {'dia': EJ_DIA, 'estacion': EJ_EST, 'nombre': str(st.loc[EJ_EST, 'name']),
            'desde': hm(lo), 'hasta': hm(hi), 'fotos': fotos,
            'movimientos': [{'estacion': x.short_name, 'hora': hm(x.t1), 'delta': int(x.delta)}
                            for x in moved.sort_values('t1').itertuples()],
            'salidas_reales': len(sal), 'desvios': len(cadena),
            'sim_bicis_inicio': int(j['bikes'][EJ_K - 1][pos[EJ_EST]]),
            'cadena': orden,
            'v265_bicis_inicio': int(j['bikes'][EJ_K - 1][pos['265']]),
            'v265_bicis_fin': int(j['bikes'][EJ_K][pos['265']]),
            'v265_metros': vecina['metros'] if vecina else None,
            'entregas_vecinas': {n: int(est[pos[n]][1]) for n in ('554', '547') if pos[n] in est}}


def generate():
    days = replay_days()
    assert len(days) == 16, days
    snaps = {d: real_snaps(d) for d in days}
    res152, sal152 = resultados_152()
    brazos = por_brazo(days)
    # Las salidas del simulador en los 16 días son las mismas que cuenta data.trips.
    assert brazos['ecobici']['salidas'] == sum(sal152[d] for d in days), 'salidas no cuadran'
    return {'dias16': days, 'brazos16': brazos, 'prueba': res152,
            'causa_resolucion': causa_resolucion(days), 'causa_cero': causa_cero(days, snaps),
            'causa_arrastre': causa_arrastre(days, snaps), 'demanda': demanda(days),
            'ejemplo': ejemplo()}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    text = json.dumps(generate(), ensure_ascii=False, indent=1) + '\n'
    if args.check:
        assert OUT.exists() and OUT.read_text() == text, f'{OUT.name} desactualizado o distinto de su origen'
        print(f'OK --check: {OUT.name} idéntico a lo recalculado')
    else:
        OUT.write_text(text)
        print(f'Escrito {OUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
