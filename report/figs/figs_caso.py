"""Cifras y figuras nuevas del reporte del caso (reporte-caso.tex).

    uv run python3 report/figs/figs_caso.py          # regenera
    uv run python3 report/figs/figs_caso.py --check  # comprueba que nada cambió

Entradas (solo lectura): `ecosim/results/resultados.csv` (tag prueba, días de prueba),
`data/derived/ecosim/ecobici_moves.parquet` (movimientos medidos de Ecobici,
sin pares) y `data/derived/ecosim/trips_2024_01_2026_08.parquet` (viajes).

Salidas: `report/numeros-caso.tex` (macros \\c...), `report/figs/caso-*.pdf`
y `report/figs/figs_caso.json` (las mismas cifras con su origen).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # ecosim.run y ecosim.medicion
OUT = ROOT / 'report'
DERIVED = ROOT / 'data/derived/ecosim'
BLUE, GRAY, INK, RED = '#2a78d6', '#8a8a85', '#3d3d3a', '#d03b3b'
FIG_MONTHS = ('2025-09', '2025-10', '2025-11')  # mismos meses que las figuras de topes
DAY = '2025-09-01'                                # día de las capturas de la app
# Topes: p95 de Ecobici en todo 2025 (ecosim.medicion, clave anual_2025).
TOPES = json.loads((ROOT / 'ecosim/results/medicion/ecobici_stats.json').read_text())['anual_2025']
CAP_VIS, CAP_BIKES = TOPES['p95']['visitas_por_decision'], TOPES['p95']['bicis_por_visita']


def fmt(x, d=0):
    return f'{x:,.{d}f}'


class Book:
    def __init__(self):
        self.vals, self.src = {}, {}

    def add(self, key, value, source):
        self.vals[key], self.src[key] = str(value), source


def results(b: Book):
    df = pd.read_csv(ROOT / 'ecosim/results/resultados.csv', dtype={'day': str})
    src = 'ecosim/results/resultados.csv, tag prueba'
    day = df[df.day == DAY].set_index('arm')
    for arm, tag in (('sin_rebalanceo', 'Base'), ('ecobici', 'Eco'), ('lgbm_directo', 'Lgbm'),
                     ('ma_diaria', 'Ma'), ('oraculo_directo', 'Or')):
        r = day.loc[arm]
        desv = r.desvios_salida + r.desvios_llegada
        b.add(f'd{tag}E', fmt(r.E), f'{src}, {DAY}, {arm}, E')
        b.add(f'd{tag}F', fmt(r.F), f'{src}, {DAY}, {arm}, F')
        b.add(f'd{tag}EF', fmt(r.EF), f'{src}, {DAY}, {arm}, EF')
        b.add(f'd{tag}Desv', fmt(desv), f'{src}, {DAY}, {arm}, desvios_salida + desvios_llegada')
        b.add(f'd{tag}Km', fmt(r.km_desvio_medio * 1000), f'{src}, {DAY}, {arm}, km_desvio_medio × 1000 (m)')
        b.add(f'd{tag}Vis', fmt(r.visitas), f'{src}, {DAY}, {arm}, visitas')
        b.add(f'd{tag}Bicis', fmt(r.bicis_movidas), f'{src}, {DAY}, {arm}, bicis_movidas')
    eco, lg = day.loc['ecobici'], day.loc['lgbm_directo']
    b.add('dLgbmPct', fmt(100 * (1 - lg.EF / eco.EF), 1), f'{src}, {DAY}, 1 − EF lgbm_directo / EF ecobici')
    base = day.loc['sin_rebalanceo']
    b.add('dEcoPct', fmt(100 * (1 - eco.EF / base.EF), 1), f'{src}, {DAY}, 1 − EF ecobici / EF sin_rebalanceo')
    # Órdenes que no se pueden cumplir completas (media móvil, días de prueba).
    ma = df[df.arm == 'ma_diaria']
    rc = pd.DataFrame([json.loads(x) for x in ma.recortes])
    falta = rc.entrega_sin_recogida.mean()
    pedidas = ma.recogidas_aplicadas.mean() + falta
    srcr = f'{src}, días de prueba, ma_diaria'
    b.add('oRecogidas', fmt(ma.recogidas_aplicadas.mean()), srcr + ', recogidas_aplicadas')
    b.add('oFalta', fmt(falta), srcr + ', recortes.entrega_sin_recogida (bicis pedidas que no había al recoger)')
    b.add('oFaltaPct', fmt(100 * falta / pedidas, 1), srcr + ', entrega_sin_recogida / (recogidas + entrega_sin_recogida)')
    b.add('oDevueltas', fmt(ma.reubicaciones_destino.mean()), srcr + ', reubicaciones_destino (no cupieron en su destino)')
    b.add('oDevueltasPct', fmt(100 * ma.reubicaciones_destino.sum() / ma.recogidas_aplicadas.sum(), 2), srcr + ', reubicaciones_destino / recogidas_aplicadas')
    # Medias de los días de prueba: kilómetros de desvío (la tabla principal no los trae).
    for arm, tag in (('sin_rebalanceo', 'Base'), ('ecobici', 'Eco'), ('lgbm_directo', 'Lgbm'), ('ma_diaria', 'Ma')):
        a = df[df.arm == arm]
        b.add(f'm{tag}Km', fmt(a.km_desvio_medio.mean() * 1000), f'{src}, días de prueba, {arm}, media de km_desvio_medio × 1000 (m)')
        b.add(f'm{tag}Desv', fmt((a.desvios_salida + a.desvios_llegada).mean()), f'{src}, días de prueba, {arm}, media de desvíos por día')
    return df


def moves(b: Book, test_days):
    m = pd.read_parquet(DERIVED / 'ecobici_moves.parquet', columns=['day', 't0', 't1', 'delta', 'par'])
    m = m[~m.par & (m.delta != 0)].copy()
    m['day'] = m['day'].astype(str)
    src = 'data/derived/ecosim/ecobici_moves.parquet, sin pares'
    stats = 'ecosim/results/medicion/ecobici_stats.json, anual_2025'
    b.add('topeVis', fmt(CAP_VIS), f'{stats}, p95 visitas_por_decision')
    b.add('topeBicis', fmt(CAP_BIKES), f'{stats}, p95 bicis_por_visita')
    b.add('topeDias', fmt(TOPES['dias']), f'{stats}, dias ({TOPES["poblacion"]})')
    assert TOPES['primer_dia'][:4] == TOPES['ultimo_dia'][:4], 'topes de más de un año'
    b.add('topePeriodo', TOPES['primer_dia'][:4], f'{stats}, primer_dia y ultimo_dia')
    # Figura de topes: la misma población y el mismo método que el p95 (medicion.annual_caps):
    # cada foto de los días válidos de 2025, visitas × 15 / minutos (incluye fotos sin visitas)
    # y |delta| de cada visita, sin pares.
    from ecosim import medicion
    days = medicion.annual_days(int(TOPES['primer_dia'][:4]))
    samples = {d: medicion.annual_day_samples(d) for d in days}
    assert medicion.annual_caps(samples)['p95'] == TOPES['p95'], 'la población no reproduce el p95 de anual_2025'
    per15, sizes = (np.concatenate([samples[d][j] for d in sorted(samples)]) for j in (0, 1))
    srct = f'ecosim.medicion.annual_day_samples, {len(days)} días válidos de 2025, sin pares'
    b.add('figVisitas', fmt(len(sizes)), f'{srct}, visitas')
    b.add('figIntervalos', fmt(len(per15)), f'{srct}, fotos (incluye las que no tienen visitas)')
    b.add('figSinVisita', fmt((per15 == 0).mean() * 100, 1), f'{srct}, % de fotos sin visitas')
    b.add('figBajoTope', fmt((sizes <= CAP_BIKES).mean() * 100, 1), f'{srct}, % de visitas con |delta| ≤ {CAP_BIKES}')
    b.add('figVisBajoTope', fmt((per15 <= CAP_VIS).mean() * 100, 1), f'{srct}, % de fotos con visitas por 15 min ≤ {CAP_VIS}')
    b.add('figVisPNoventaCinco', fmt(np.percentile(per15, 95), 2), f'{srct}, p95 sin redondear de visitas por 15 min')
    b.add('figMedBicis', fmt(np.median(sizes)), f'{srct}, mediana de |delta|')
    b.add('figUnaDos', fmt((sizes <= 2).mean() * 100, 0), f'{srct}, % de visitas con |delta| ≤ 2')
    b.add('figMedVis', fmt(np.median(per15)), f'{srct}, mediana de visitas por 15 min (todas las fotos)')
    # Perfil por hora en los días de prueba, a la hora de la foto t1.
    t = m[m.day.isin(test_days)].copy()
    n_days = t.day.nunique()
    assert n_days == len(test_days), (n_days, len(test_days))
    t['hora'] = t.t1.dt.hour
    t['entrega'] = t.delta.clip(lower=0)
    t['recoge'] = (-t.delta).clip(lower=0)
    hourly = t.groupby('hora')[['entrega', 'recoge']].sum() / n_days
    hourly['visitas'] = t.groupby('hora').size() / n_days
    total = hourly.entrega.sum() + hourly.recoge.sum()
    day_bikes = total
    b.add('hBicisDia', fmt(day_bikes), f'{src}, días de prueba, bicis movidas (entregadas + recogidas) por día')
    b.add('hVisDia', fmt(hourly.visitas.sum()), f'{src}, días de prueba, visitas por día')
    b.add('hPorQuince', fmt(day_bikes / (19.5 * 4)), f'{src}, días de prueba, bicis movidas por día / 78 cuartos de hora')
    b.add('hVisQuince', fmt(hourly.visitas.sum() / (19.5 * 4)), f'{src}, días de prueba, visitas por día / 78 cuartos de hora')
    moved = hourly.entrega + hourly.recoge
    peak = int(moved.idxmax())
    b.add('hPicoHora', f'{peak:02d}:00', f'{src}, días de prueba, hora de reloj (t1) con más bicis movidas')
    b.add('hPicoBicis', fmt(moved.max()), f'{src}, días de prueba, bicis movidas en esa hora por día')
    for lo, hi, tag in ((6, 10, 'Manana'), (16, 21, 'Tarde')):
        share = moved[(moved.index >= lo) & (moved.index < hi)].sum() / total * 100
        b.add(f'h{tag}Pct', fmt(share, 0), f'{src}, días de prueba, % de bicis movidas con t1 en [{lo}:00, {hi}:00)')
    return None, sizes, per15, hourly, n_days


def trips_hourly(test_days):
    con = duckdb.connect()
    path = str(DERIVED / 'trips_2024_01_2026_08.parquet')
    days = ", ".join(f"'{d}'" for d in test_days)
    df = con.execute(f"""select hour(t_dep) h, count(*) n from '{path}'
        where cast(t_dep - interval 5 hour as date) in ({days}) group by 1 order by 1""").df()
    con.close()
    return df.set_index('h').n / len(test_days)


def bodega(b: Book):
    """Total anclado a las 04:45 (sistema cerrado) e IDs nuevos por día."""
    from scipy import stats
    st = pd.read_csv(ROOT / 'ecosim/results/flota/stock_0445.csv', parse_dates=['date']).sort_values('date')
    gap = st.date.diff().dt.days
    d = st.tot_c.diff()[gap == 1]
    half = stats.t.ppf(.975, len(d) - 1) * stats.sem(d)
    src = 'ecosim/results/flota/stock_0445.csv (tot_c, commit más cercano a 04:45), pares de días consecutivos'
    b.add('bDias', len(st), src + ', días')
    b.add('bPares', len(d), src + ', pares')
    b.add('bMedia', fmt(d.mean()), src + ', media de la diferencia día a día')
    b.add('bLo', fmt(d.mean() - half), src + ', IC95 t inferior')
    b.add('bHi', fmt(d.mean() + half), src + ', IC95 t superior')
    b.add('bTotMed', fmt(st.tot_c.median()), src + ', mediana del total')
    ids = pd.read_csv(ROOT / 'ecosim/results/flota/ids_por_dia.csv', index_col=0, parse_dates=True)
    sn = ids.loc['2025-09-01':'2025-11-30', 'nuevas'].fillna(0)
    act = ids.loc['2025-09-01':'2025-11-30', 'activas'].resample('MS').mean()
    for (mes, v), tag in zip(act.items(), ('Sep', 'Oct', 'Nov')):
        b.add(f'bActivas{tag}', fmt(v), f'ecosim/results/flota/ids_por_dia.csv, media de activas por día, {mes:%Y-%m}')
    b.add('bIdsNuevos', fmt(sn.sum()), 'ecosim/results/flota/ids_por_dia.csv, suma de nuevas, sep–nov 2025')
    return st


def figures(sizes, per15, hourly, trips_h, st):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'serif', 'font.serif': ['Times New Roman', 'Times'],
                         'font.size': 9, 'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.edgecolor': GRAY, 'axes.labelcolor': INK, 'xtick.color': INK,
                         'ytick.color': INK, 'pdf.fonttype': 42})
    meta = {'CreationDate': None, 'Creator': None, 'Producer': None}
    out = {}

    def save(fig, name):
        buf = io.BytesIO()
        fig.savefig(buf, format='pdf', metadata=meta)
        plt.close(fig)
        out[OUT / 'figs' / name] = buf.getvalue()

    def hist(ax, values, edges, cap, xlabel, unit, title):
        """Histograma en % (escala lineal): azul hasta el tope, gris después."""
        counts, _ = np.histogram(values, bins=edges)
        pct = 100 * counts / len(values)
        left = edges[:-1]
        colors = [BLUE if l + (edges[1] - edges[0]) <= cap + 1e-9 else GRAY for l in left]
        ax.bar(left, pct, width=np.diff(edges) * .86, align='edge', color=colors, linewidth=0)
        ax.axvline(cap + .93 if edges[1] - edges[0] == 1 else cap, color=INK, lw=.8, ls=(0, (3, 2)))
        share = (values <= cap).mean() * 100
        ax.text(.98, .97, f'tope {cap}\n{share:.1f} %\nen azul', transform=ax.transAxes,
                ha='right', va='top', color=INK, fontsize=7)
        ax.set_title(title, fontsize=8, color=INK)
        ax.set_xlabel(xlabel, fontsize=7.5)
        ax.tick_params(length=2, labelsize=7)

    fig, axes = plt.subplots(1, 2, figsize=(3.5, 1.7))
    hist(axes[0], np.minimum(per15, 120), np.arange(0, 123, 3), CAP_VIS, 'Visitas por 15 min', 'visitas',
         '(a) Visitas por intervalo')
    hist(axes[1], np.minimum(sizes, 40), np.arange(1, 42), CAP_BIKES, 'Bicis por visita', 'bicis',
         '(b) Bicis por visita')
    axes[0].set_ylabel('% del total', fontsize=7.5)
    fig.tight_layout(pad=.3, w_pad=.8)
    save(fig, 'caso-topes.pdf')

    # Ventanas de entrenamiento y prueba (walk-forward), desde ecosim/config.py FOLDS.
    sys.path.insert(0, str(ROOT))
    from ecosim import config as EC
    import matplotlib.dates as mdates
    from ecosim import run
    test_months = {d[:7] for d in run.days('prueba')}
    folds = [f for f in EC.FOLDS if f['name'] == 'elegir' or (f['name'].startswith('prueba') and f['test_month'] in test_months)]
    assert len(folds) == 1 + len(test_months), [f['name'] for f in folds]
    fig, ax = plt.subplots(figsize=(3.4, 1.75))
    meses = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
    for y, f in enumerate(folds[::-1]):
        t0, t1 = pd.Timestamp(f['train_start']), pd.Timestamp(f['train_end']) + pd.Timedelta(days=1)
        m0 = pd.Timestamp(f['test_month'] + '-01'); m1 = m0 + pd.offsets.MonthBegin(1)
        ax.barh(y, (t1 - t0).days, left=mdates.date2num(t0), height=.6, color='#c9d6e8')
        ax.barh(y, (m1 - m0).days, left=mdates.date2num(m0), height=.6,
                color=RED if f['name'] == 'elegir' else BLUE)
    labels = [('Validación' if f['name'] == 'elegir' else f"Prueba {meses[int(f['test_month'][5:]) - 1]}")
              for f in folds[::-1]]
    ax.set_yticks(range(len(folds)))
    ax.set_yticklabels(labels, fontsize=7)
    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
    from matplotlib.ticker import FuncFormatter
    ax.xaxis.set_major_formatter(FuncFormatter(
        lambda x, _: f"{meses[mdates.num2date(x).month - 1]} {mdates.num2date(x).year % 100:02d}"))
    ax.tick_params(length=2, labelsize=7)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color='#c9d6e8', label='Entrenamiento'),
                       Patch(color=RED, label='Validación'), Patch(color=BLUE, label='Prueba')],
              fontsize=6.5, frameon=False, loc='lower center', bbox_to_anchor=(.5, 1.0), ncol=3,
              handlelength=1, columnspacing=.8)
    fig.tight_layout(pad=.3)
    save(fig, 'caso-ventanas.pdf')

    fig, ax = plt.subplots(figsize=(3.4, 2.2))
    hours = np.arange(5, 25)
    idx = [h % 24 for h in hours]
    ent = hourly.entrega.reindex(idx, fill_value=0).to_numpy()
    rec = hourly.recoge.reindex(idx, fill_value=0).to_numpy()
    ax.bar(hours - .2, ent, width=.4, color=BLUE, label='Entregadas')
    ax.bar(hours + .2, rec, width=.4, color=RED, label='Recogidas')
    ax.set_xlabel('Hora del día')
    ax.set_ylabel('Bicis por día')
    ax.set_xticks([5, 8, 11, 14, 17, 20, 23])
    ax2 = ax.twinx()
    ax2.plot(hours, trips_h.reindex(idx, fill_value=0).to_numpy(), color=INK, lw=1, marker='o', ms=2,
             label='Salidas de viajes')
    ax2.set_ylabel('Viajes por día', color=INK)
    ax2.spines['top'].set_visible(False)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7, frameon=False, loc='lower center',
              bbox_to_anchor=(.5, 1.0), ncol=3, handlelength=1.2, columnspacing=1)
    ax.tick_params(length=2)
    ax2.tick_params(length=2)
    fig.tight_layout(pad=.3)
    save(fig, 'caso-por-hora.pdf')
    return out


ARMS = ['sin_rebalanceo', 'ecobici', 'ma_diaria', 'lgbm_diario', 'lgbm_directo',
        'oraculo_diario', 'oraculo_directo']
LABELS = {'sin_rebalanceo': 'Sin rebalanceo', 'ecobici': 'Ecobici (medido)',
          'ma_diaria': 'Media móvil', 'lgbm_diario': 'LightGBM diario',
          'lgbm_directo': 'LightGBM directo', 'oraculo_diario': 'Oráculo diario',
          'oraculo_directo': 'Oráculo directo'}


def table_main(df):
    """Medias por día en los días de prueba; IC95 t pareado por día contra Ecobici."""
    from scipy import stats
    fid = json.loads((OUT / 'figs/fidelidad_ecobici.json').read_text())['prueba']['brazos']
    eco = df[df.arm == 'ecobici'].set_index('day').sort_index()
    lines = [r'\begin{tabular}{@{}lrrrrrrrl@{}}', r'\toprule',
             r'Brazo & E & F & E+F & Visitas & Bicis & Desv., \% & m/desv. & Menos E+F, \% [IC95] \\',
             r'\midrule']
    for arm in ARMS:
        a = df[df.arm == arm].set_index('day').sort_index()
        from ecosim import run
        assert list(a.index) == list(eco.index) and len(a) == len(run.days('prueba'))
        assert abs(fid[arm]['EF'] - a.EF.mean()) < .1
        cell = '---'
        if arm not in ('sin_rebalanceo', 'ecobici'):
            diff = a.EF - eco.EF
            half = stats.t.ppf(.975, len(diff) - 1) * stats.sem(diff)
            e = eco.EF.mean()
            cell = f'{-diff.mean() / e * 100:.1f} [{(-diff.mean() - half) / e * 100:.1f}, {(-diff.mean() + half) / e * 100:.1f}]'
        if arm == 'oraculo_diario':
            lines.append(r'\midrule')
        lines.append(f'{LABELS[arm]} & {fmt(a.E.mean())} & {fmt(a.F.mean())} & {fmt(a.EF.mean())} & '
                     f'{fmt(a.visitas.mean())} & {fmt(a.bicis_movidas.mean())} & {fid[arm]["pct"]:.1f} & '
                     f'{fmt(a.km_desvio_medio.mean() * 1000)} & {cell} \\\\')
    return '\n'.join(lines + [r'\bottomrule', r'\end{tabular}']) + '\n'


def table_day(df):
    """Un día (las capturas de la app): sin rebalanceo, Ecobici y los dos pronósticos de las figuras."""
    day = df[df.day == DAY].set_index('arm')
    lines = [r'\begin{tabular}{@{}lrrrrr@{}}', r'\toprule',
             r'Brazo & E & F & E+F & Desvíos & m/desv. \\', r'\midrule']
    for arm in ('sin_rebalanceo', 'ecobici', 'lgbm_directo', 'oraculo_directo'):
        r = day.loc[arm]
        lines.append(f'{LABELS[arm]} & {fmt(r.E)} & {fmt(r.F)} & {fmt(r.EF)} & '
                     f'{fmt(r.desvios_salida + r.desvios_llegada)} & {fmt(r.km_desvio_medio * 1000)} \\\\')
    return '\n'.join(lines + [r'\bottomrule', r'\end{tabular}']) + '\n'


N_HORAS = 4  # n del asignador (\rnDiario); ecosim/results/frozen.json, n.diaria = n.directa = 4


def md_rows(text, header):
    """Primera tabla markdown después de la línea que empieza con `header`."""
    lines = text.splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith(header))
    i = next(k for k in range(i + 1, len(lines)) if lines[k].startswith('|'))
    head = [c.strip() for c in lines[i].strip('|').split('|')]
    out = []
    for l in lines[i + 2:]:
        if not l.startswith('|'):
            break
        out.append(dict(zip(head, [c.strip() for c in l.strip('|').split('|')])))
    return out


def forecast_table(b: Book, test_days):
    """Exactitud de cada pronóstico en los meses de prueba, agrupada con el peso n de cada mes.

    MAE = Σ|error| / n. WAPE = Σ|error| / Σ|real|, y Σ|real| = Σ|error| / WAPE de cada mes.
    """
    months = sorted({d[:7] for d in test_days})
    d = pd.read_csv(ROOT / 'ecosim/results/pronostico/accuracy_run3.csv')
    d = d[d.mes.isin(months) & d.variante.isin(('ma_diaria', 'lgbm_diario', 'lgbm_directo'))].copy()
    assert (d.wape > 0).all()
    d['abs_err'] = d.mae * d.n
    d['real'] = d.abs_err / d.wape
    g = d.groupby(['variante', 'k', 'objetivo']).agg(n=('n', 'sum'), e=('abs_err', 'sum'), real=('real', 'sum'))
    g['mae'], g['wape'] = g.e / g.n, g.e / g.real
    src = f'ecosim/results/pronostico/accuracy_run3.csv, meses {months[0]} a {months[-1]}, agrupado por n'
    names = {'ma_diaria': 'Media móvil', 'lgbm_diario': 'LightGBM diario', 'lgbm_directo': 'LightGBM directo'}
    lines = [r'\begin{tabular}{@{}lrrrrrr@{}}', r'\toprule',
             r' & \multicolumn{2}{c}{Salidas} & \multicolumn{2}{c}{Llegadas} & \multicolumn{2}{c}{Neto} \\',
             r'Pronóstico & MAE & WAPE, \% & MAE & WAPE, \% & MAE & WAPE, \% \\']
    for k, titulo in ((0, 'Primera hora'), (N_HORAS - 1, f'Hora {N_HORAS} hacia adelante')):
        lines += [r'\midrule', rf'\multicolumn{{7}}{{@{{}}l}}{{\emph{{{titulo}}}}} \\']
        for var, name in names.items():
            cells = []
            for obj in ('salidas', 'llegadas', 'neto'):
                r = g.loc[(var, k, obj)]
                cells += [f'{r.mae:.2f}', f'{100 * r.wape:.0f}']
            lines.append(f'{name} & ' + ' & '.join(cells) + r' \\')
    # Diferencia relativa del error del neto frente a la media móvil (el asignador usa el flujo neto).
    for k, pos in ((0, 'Pri'), (N_HORAS - 1, 'Ult')):
        ma = g.loc[('ma_diaria', k, 'neto')].mae
        for var, tag in (('lgbm_diario', 'D'), ('lgbm_directo', 'Dir')):
            v = g.loc[(var, k, 'neto')].mae
            b.add(f'fDif{tag}{pos}', fmt(abs(100 * (v / ma - 1)), 1), f'{src}, {var} contra ma_diaria, k={k}, neto, |MAE/MAE_MA − 1| × 100')
    for var, tag in (('lgbm_diario', 'D'), ('lgbm_directo', 'Dir')):
        mejor = all(g.loc[(var, k, o)].mae < g.loc[('ma_diaria', k, o)].mae
                    for k in (0, N_HORAS - 1) for o in ('salidas', 'llegadas', 'neto'))
        b.add(f'fMejor{tag}', 'si' if mejor else 'no', f'{src}, {var} menor MAE que ma_diaria en las seis celdas de la tabla')
    b.add('fMeses', len(months), f'{src}, meses de prueba')
    b.add('fMaeMaPri', f"{g.loc[('ma_diaria', 0, 'neto')].mae:.1f}", f'{src}, ma_diaria, k=0, neto, MAE')
    b.add('fWapeMaSal', f"{100 * g.loc[('ma_diaria', 0, 'salidas')].wape:.0f}", f'{src}, ma_diaria, k=0, salidas, WAPE × 100')
    return '\n'.join(lines + [r'\bottomrule', r'\end{tabular}']) + '\n'


def sens_table(b: Book):
    """Sensibilidades (32 días de sep–dic 2025, mismo λ): media móvil y oráculo directo frente a su caso base."""
    t = (ROOT / 'ecosim/results/tablas.md').read_text()
    src = 'ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, mismos 32 días'
    rows = {(r['variante'], r['brazo']): r for r in md_rows(t, '### Sensibilidad frente al mismo brazo')}
    fmt_s = lambda x, d=0: ('$+$' if x >= 0 else '$-$') + f'{abs(x):,.{d}f}'  # noqa: E731
    ef = lambda v, a: float(rows[(v, a)]['EF'].replace(',', ''))              # noqa: E731
    delta = lambda v, a, c: float(rows[(v, a)][c].replace(',', ''))           # noqa: E731
    base_eco, base_ma, base_or = (ef('sens_pares_sin_regla', a) for a in ('ecobici', 'ma_diaria', 'oraculo_directo'))
    b.add('xEco', fmt(base_eco), src + ', sens_pares_sin_regla ecobici EF (no cambia con ninguna variante)')
    b.add('xMa', fmt(base_ma), src + ', sens_pares_sin_regla ma_diaria EF (caso base)')
    b.add('xMaPct', fmt(100 * (1 - base_ma / base_eco), 1), src + ', 1 − EF ma_diaria base / EF ecobici')
    vs_eco = lambda x: fmt_s(100 * (x / base_eco - 1), 1)                    # noqa: E731
    lines = [r'\begin{tabular}{@{}lrrrr@{}}', r'\toprule',
             r'Variante & E+F & Cambio $\pm$ IC95 & vs.\ Ecobici, \% & Orác. dir. \\', r'\midrule',
             f'Ecobici (medido) & {fmt(base_eco)} & --- & --- & --- \\\\',
             f'Caso base, media móvil & {fmt(base_ma)} & --- & {vs_eco(base_ma)} & --- \\\\', r'\midrule']
    variants = (('sens_entrega_45', r'Entrega a los \rentregaCorta{} min', 'Corta'),
                ('sens_entrega_75', r'Entrega a los \rentregaLarga{} min', 'Larga'),
                ('sens_danadas_feed', 'No rentables según el feed', 'Dan'))
    corr = pd.read_csv(ROOT / 'ecosim/results/corridas_run3.csv', usecols=['day', 'tag'], dtype={'day': str})
    n_sens = corr[corr.tag.isin([v for v, _, _ in variants])].groupby('tag').day.nunique()
    assert n_sens.nunique() == 1, n_sens
    b.add('xDias', int(n_sens.iloc[0]), 'ecosim/results/corridas_run3.csv, días distintos por variante de sensibilidad')
    for v, label, tag in variants:
        d, lo, hi = (delta(v, 'ma_diaria', c) for c in ('delta_EF_base', 'IC95_inf', 'IC95_sup'))
        o = delta(v, 'oraculo_directo', 'delta_EF_base')
        lines.append(f'{label} & {fmt(ef(v, "ma_diaria"))} & {fmt_s(d)} $\\pm$ {fmt((hi - lo) / 2)} & {vs_eco(ef(v, "ma_diaria"))} & {fmt_s(o)} \\\\')
        b.add(f'x{tag}Pct', fmt(100 * (1 - ef(v, 'ma_diaria') / base_eco), 1), src + f', {v}, 1 − EF ma_diaria / EF ecobici base')
    return '\n'.join(lines + [r'\bottomrule', r'\end{tabular}']) + '\n'


def b_int(b, k):
    return int(b.vals[k])


def coverage(b: Book):
    """Días que cumplen el 90 % de cobertura de fotos (ecosim/days.json, coverage_by_month)."""
    m = json.loads((ROOT / 'ecosim/days.json').read_text())['coverage_by_month']
    src = 'ecosim/days.json, coverage_by_month'
    def tot(months):
        return sum(m[k]['days'] for k in months), sum(m[k]['eligible_90'] for k in months)
    y25 = [k for k in m if k.startswith('2025')]
    d, ok = tot(y25)
    b.add('vDiasVeinticinco', d, f'{src}, 2025, días')
    b.add('vOkVeinticinco', ok, f'{src}, 2025, eligible_90')
    b.add('vExcVeinticinco', d - ok, f'{src}, 2025, días − eligible_90')
    b.add('vExcJul', m['2025-07']['days'] - m['2025-07']['eligible_90'], f'{src}, 2025-07, días − eligible_90')
    # Prueba: los días de run.days('prueba') (days.json) dentro de los meses que abarcan.
    from ecosim import run
    test = run.days('prueba')
    cal = [str(x)[:10] for x in pd.date_range(test[0][:7] + '-01', pd.Timestamp(test[-1][:7] + '-01') + pd.offsets.MonthEnd(0))]
    pm = sorted({x[:7] for x in cal})
    d, ok = tot(pm)
    assert (d, ok) == (len(cal), len(test)), (d, ok, len(cal), len(test))
    srcp = f"ecosim.run.days('prueba') y {src}, {pm[0]} a {pm[-1]}"
    b.add('vDiasPrueba', d, f'{srcp}, días del calendario')
    b.add('vOkPrueba', ok, f'{srcp}, días de prueba (eligible_90)')
    b.add('vDifPrueba', d - ok, f'{srcp}, días − días de prueba')
    falta = [x for x in cal if x not in set(test)]
    assert len(falta) == 1, falta
    meses = {'09': 'septiembre', '10': 'octubre', '11': 'noviembre', '12': 'diciembre'}
    b.add('vDiaExcl', f'{int(falta[0][8:])} de {meses[falta[0][5:7]]}', f'{srcp}: día del calendario fuera de la prueba')


def replay_snaps(b: Book):
    """Las dos fotos de las capturas (08:45–09:00 y 20:45–21:00) del replay de la app."""
    base = DERIVED / 'replay' / DAY
    for arm, tag in (('sin_rebalanceo', 'Base'), ('ecobici', 'Eco'), ('lgbm_directo', 'Lgbm')):
        d = json.loads((base / f'{arm}.json').read_text())
        assert d['check']['igual'], arm
        for i, hora in ((16, 'Nueve'), (64, 'Veintiuno')):
            s = d['snap'][i]
            assert s['min_desde_anterior'] == 15
            src = f'data/derived/ecosim/replay/{DAY}/{arm}.json, snap[{i}]'
            b.add(f's{tag}{hora}EF', fmt(s['EF']), src + ', EF del cuarto de hora')
            b.add(f's{tag}{hora}Desv', fmt(s['desvios_salida'] + s['desvios_llegada']), src + ', desvíos del cuarto de hora')
            b.add(f's{tag}{hora}Acum', fmt(s['EF_acum']), src + ', EF acumulado desde 05:00')


def generate():
    b = Book()
    df = results(b)
    replay_snaps(b)
    coverage(b)
    test_days = sorted(df.day.unique())
    _, sizes, per15, hourly, n = moves(b, test_days)
    b.add('nDias', n, 'días distintos de ecobici_moves en los días de prueba de resultados.csv')
    trips_h = trips_hourly(test_days)
    b.add('hViajesPico', f'{int(trips_h.idxmax()):02d}:00', 'trips parquet, días de prueba, hora con más salidas')
    st = bodega(b)
    tab_pron = forecast_table(b, test_days)
    tab_sens = sens_table(b)
    files = figures(sizes, per15, hourly, trips_h, st)
    tex = '% Generado por report/figs/figs_caso.py. No editar a mano.\n' + ''.join(
        f'\\newcommand{{\\c{k}}}{{{v}}}\n' for k, v in b.vals.items())
    files[OUT / 'numeros-caso.tex'] = tex.encode()
    files[OUT / 'tablas/principal-caso.tex'] = table_main(df).encode()
    files[OUT / 'tablas/dia-caso.tex'] = table_day(df).encode()
    files[OUT / 'tablas/pronostico-caso.tex'] = tab_pron.encode()
    files[OUT / 'tablas/sens-caso.tex'] = tab_sens.encode()
    files[OUT / 'figs/figs_caso.json'] = (json.dumps({k: {'valor': b.vals[k], 'origen': b.src[k]} for k in b.vals},
                                                     ensure_ascii=False, indent=1) + '\n').encode()
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()
    files = generate()
    if args.check:
        bad = [str(p) for p, data in files.items() if not p.exists() or p.read_bytes() != data]
        if bad:
            print('cambió:', *bad, sep='\n  ')
            sys.exit(1)
        print('ok:', len(files), 'archivos iguales')
        return
    for p, data in files.items():
        p.write_bytes(data)
        print('escrito', p.relative_to(ROOT))


if __name__ == '__main__':
    main()
