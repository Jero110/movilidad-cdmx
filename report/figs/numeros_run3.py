"""Cifras, tablas y figuras del reporte final, todas desde resultados guardados.

    uv run python3 report/figs/numeros_run3.py          # regenera
    uv run python3 report/figs/numeros_run3.py --check  # comprueba que nada cambió

Entradas (solo lectura): `ecosim/results/` (resultados.csv, frozen.json,
tablas.md, medicion/REPORT.md, flota/rutas_osm_resumen.csv),
`report/figs/checks_datos.json`, `report/figs/fidelidad_ecobici.json` (de
`fidelidad_ecobici.py`: Ecobici real contra Ecobici simulado), las constantes de `ecosim/config.py` y
`ecosim/pronostico.py`, y dos cachés derivados del checkout principal
(`data/derived/ecosim/ecobici_moves.parquet` y `damage_events.parquet`).

Salidas: `report/numeros-run3.tex` (macros \\r...), `report/tablas/*.tex`,
tres figuras PDF en `report/figs/` y `report/fuentes-numeros.md`.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
R = ROOT / 'ecosim/results'
OUT = ROOT / 'report'
DERIVED = ROOT / 'data/derived/ecosim'

ARMS = ['sin_rebalanceo', 'ecobici', 'ma_diaria', 'lgbm_diario', 'lgbm_directo',
        'oraculo_diario', 'oraculo_directo']
LABELS = {'sin_rebalanceo': 'Sin rebalanceo', 'ecobici': 'Ecobici (medido)',
          'ma_diaria': 'Media móvil', 'lgbm_diario': 'LightGBM diario',
          'lgbm_directo': 'LightGBM directo', 'oraculo_diario': 'Oráculo diario',
          'oraculo_directo': 'Oráculo directo'}
BLUE, GRAY, INK = '#2a78d6', '#8a8a85', '#3d3d3a'
RESULTS_SRC = 'ecosim/results/resultados.csv (tag=prueba), media por día'


def fmt(x, d=0):
    return f'{x:,.{d}f}'


def rows(path):
    with (R / path).open(newline='') as f:
        return list(csv.DictReader(f))


def mean(rs, key):
    return sum(float(r[key]) for r in rs) / len(rs)


def md_table(text, header_start):
    """Primera tabla markdown después de la línea que empieza con header_start."""
    lines = text.splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith(header_start))
    i = next(k for k in range(i + 1, len(lines)) if lines[k].startswith('|'))
    head = [c.strip() for c in lines[i].strip('|').split('|')]
    out = []
    for l in lines[i + 2:]:
        if not l.startswith('|'):
            break
        out.append(dict(zip(head, [c.strip() for c in l.strip('|').split('|')])))
    return out


def num(s):
    return float(s.replace(',', ''))


MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
         'septiembre', 'octubre', 'noviembre', 'diciembre']
FIG_MONTHS = ('2025-09', '2025-10', '2025-11')   # población de las figuras de topes


def mes(ym):
    """'2025-09' → 'septiembre de 2025'."""
    return f'{MESES[int(ym[5:7]) - 1]} de {ym[:4]}'


def periodo(a, b, conj='a'):
    """Dos meses 'YYYY-MM' → 'septiembre a noviembre de 2025' o '... de 2025 a enero de 2026'."""
    if a[:4] == b[:4]:
        return f'{MESES[int(a[5:7]) - 1]} {conj} {mes(b)}'
    return f'{mes(a)} {conj} {mes(b)}'


def sgn(x):
    """Cifra con signo para texto: el menos va en modo matemático."""
    return ('$-$' if x < 0 else '$+$') + fmt(abs(x))


class Book:
    """Macros con su origen; cada valor queda en fuentes-numeros.md."""

    def __init__(self):
        self.vals, self.src = {}, {}

    def add(self, key, value, source):
        assert re.fullmatch(r'[A-Za-z]+', key), key
        self.vals[key], self.src[key] = value, source


def paired_ci(rs, ref):
    import scipy.stats as st
    a = sorted(rs, key=lambda r: r['day'])
    b = sorted(ref, key=lambda r: r['day'])
    assert [r['day'] for r in a] == [r['day'] for r in b]
    diff = [float(x['EF']) - float(y['EF']) for x, y in zip(a, b)]
    m = sum(diff) / len(diff)
    half = st.t.ppf(.975, len(diff) - 1) * st.sem(diff)
    return m, half


def results_macros(b: Book):
    test = [r for r in rows('resultados.csv') if r['tag'] == 'prueba']
    by = {a: [r for r in test if r['arm'] == a] for a in ARMS}
    eco = by['ecobici']
    n = len(eco)
    from ecosim import run
    assert n == len(run.days('prueba')) and all(len(v) == n for v in by.values())
    e = mean(eco, 'EF')
    b.add('dias', n, RESULTS_SRC + '; número de días')
    for arm, key in [('ecobici', 'eco'), ('sin_rebalanceo', 'base'), ('ma_diaria', 'ma')]:
        rs = by[arm]
        b.add(key + 'EF', fmt(mean(rs, 'EF')), RESULTS_SRC + f'; arm={arm}, EF')
        b.add(key + 'E', fmt(mean(rs, 'E')), RESULTS_SRC + f'; arm={arm}, E')
        b.add(key + 'F', fmt(mean(rs, 'F')), RESULTS_SRC + f'; arm={arm}, F')
    for arm, key in [('ecobici', 'eco'), ('ma_diaria', 'ma')]:
        b.add(key + 'Vis', fmt(mean(by[arm], 'visitas')), RESULTS_SRC + f'; arm={arm}, visitas')
        b.add(key + 'Bicis', fmt(mean(by[arm], 'bicis_movidas')), RESULTS_SRC + f'; arm={arm}, bicis_movidas')
    pct = {}
    for arm in ARMS[2:]:
        m, half = paired_ci(by[arm], eco)
        pct[arm] = (-m / e * 100, (-m - half) / e * 100, (-m + half) / e * 100)
    ma = by['ma_diaria']
    b.add('maPct', f'{pct["ma_diaria"][0]:.1f}', RESULTS_SRC + '; 1 − EF(ma_diaria)/EF(ecobici)')
    b.add('maPctLo', f'{pct["ma_diaria"][1]:.1f}', RESULTS_SRC + '; IC95 t pareado por día, límite inferior')
    b.add('maPctHi', f'{pct["ma_diaria"][2]:.1f}', RESULTS_SRC + '; IC95 t pareado por día, límite superior')
    for arm, key in [('lgbm_diario', 'lgbmD'), ('lgbm_directo', 'lgbmDir'),
                     ('oraculo_diario', 'orD'), ('oraculo_directo', 'orDir')]:
        b.add(key + 'Pct', f'{pct[arm][0]:.1f}', RESULTS_SRC + f'; 1 − EF({arm})/EF(ecobici)')
        b.add(key + 'EF', fmt(mean(by[arm], 'EF')), RESULTS_SRC + f'; arm={arm}, EF')
    wins = sum(float(x['EF']) < float(y['EF']) for x, y in
               zip(sorted(ma, key=lambda r: r['day']), sorted(eco, key=lambda r: r['day'])))
    b.add('maGana', wins, RESULTS_SRC + '; días con EF(ma_diaria) < EF(ecobici)')
    b.add('maVisPct', f'{(1 - mean(ma, "visitas") / mean(eco, "visitas")) * 100:.1f}',
          RESULTS_SRC + '; 1 − visitas(ma_diaria)/visitas(ecobici)')
    b.add('maGap', fmt(mean(ma, 'EF') - mean(by['oraculo_diario'], 'EF')),
          RESULTS_SRC + '; EF(ma_diaria) − EF(oraculo_diario)')
    b.add('lgbmDirGap', fmt(mean(by['lgbm_directo'], 'EF') - mean(by['oraculo_directo'], 'EF')),
          RESULTS_SRC + '; EF(lgbm_directo) − EF(oraculo_directo)')
    b.add('oracleDirectGap', fmt(mean(by['oraculo_diario'], 'EF') - mean(by['oraculo_directo'], 'EF')),
          RESULTS_SRC + '; EF(oraculo_diario) − EF(oraculo_directo)')
    b.add('maDesvSal', fmt(mean(ma, 'desvios_salida')), RESULTS_SRC + '; arm=ma_diaria, desvios_salida')
    b.add('maDesvLleg', fmt(mean(ma, 'desvios_llegada')), RESULTS_SRC + '; arm=ma_diaria, desvios_llegada')
    b.add('maKm', f'{mean(ma, "km_desvio_medio"):.2f}', RESULTS_SRC + '; arm=ma_diaria, km_desvio_medio')
    b.add('ecoDesv', fmt(mean(eco, 'desvios_salida') + mean(eco, 'desvios_llegada')),
          RESULTS_SRC + '; arm=ecobici, desvios_salida + desvios_llegada')
    b.add('ecoKm', f'{mean(eco, "km_desvio_medio"):.2f}', RESULTS_SRC + '; arm=ecobici, km_desvio_medio')
    b.add('ecoNeto', f'{mean(eco, "replay_neto"):.1f}', RESULTS_SRC + '; arm=ecobici, replay_neto')
    for month, key in [(max(r['day'] for r in eco)[:7], 'dic')]:  # último mes de prueba
        m_ = [r for r in ma if r['day'].startswith(month)]
        e_ = [r for r in eco if r['day'].startswith(month)]
        b.add(key + 'Pct', f'{(1 - mean(m_, "EF") / mean(e_, "EF")) * 100:.1f}',
              RESULTS_SRC + f'; días de {month}')
        b.add(key + 'Dias', len(m_), RESULTS_SRC + f'; días de {month}')
        b.add(key + 'Mes', mes(month), RESULTS_SRC + f'; mes {month}')
    policy = [r for r in test if r['arm'] in ARMS[2:]]
    b.add('decTot', fmt(sum(int(r['decisiones']) for r in policy)),
          RESULTS_SRC + '; suma de decisiones de los cinco brazos con asignador')
    b.add('decPNoventaCinco', f'{max(float(r["decision_p95_s"]) for r in policy):.1f}',
          RESULTS_SRC + '; máximo por día de decision_p95_s')
    return by, pct, e


def table_main(by, pct, fid):
    desv = fid['prueba']['brazos']
    lines = [r'\begin{tabular}{@{}lrrrrr@{}}', r'\toprule',
             r'Brazo & E+F & Visitas & Bicis & Desv., \% & Menos E+F, \% \\', r'\midrule']
    for arm in ARMS:
        rs = by[arm]
        assert abs(desv[arm]['EF'] - mean(rs, 'EF')) < .1
        cell = '---' if arm not in pct else f'{pct[arm][0]:.1f} [{pct[arm][1]:.1f}, {pct[arm][2]:.1f}]'
        if arm == 'oraculo_diario':
            lines.append(r'\midrule')
        lines.append(f'{LABELS[arm]} & {fmt(mean(rs, "EF"))} & {fmt(mean(rs, "visitas"))} & '
                     f'{fmt(mean(rs, "bicis_movidas"))} & {desv[arm]["pct"]:.1f} & {cell} \\\\')
    return '\n'.join(lines + [r'\bottomrule', r'\end{tabular}']) + '\n'


def frozen_macros(b: Book, frozen):
    src = 'ecosim/results/frozen.json'
    b.add('nDiario', frozen['n']['diaria'], src + ' n.diaria')
    b.add('nDirecto', frozen['n']['directa'], src + ' n.directa')
    b.add('selDias', len(frozen['seleccion']), src + ' seleccion (número de días)')
    lam = frozen['lambda_por_brazo']
    for arm, key in [('ma_diaria', 'lamMa'), ('lgbm_diario', 'lamLgbmD'), ('lgbm_directo', 'lamLgbmDir'),
                     ('oraculo_diario', 'lamOrD'), ('oraculo_directo', 'lamOrDir')]:
        b.add(key, f'{lam[arm]["lambda"]:.1f}', src + f' lambda_por_brazo.{arm}.lambda')
    b.add('lamDifMax', f"{max(abs(v['diferencia_pct']) for v in lam.values()):.2f}",
          src + ' lambda_por_brazo.*.diferencia_pct (máximo en valor absoluto)')
    b.add('lamRondasMax', max(v['rondas'] for v in lam.values()), src + ' lambda_por_brazo.*.rondas (máximo)')
    b.add('lamExtension', sum(bool(v['extension_rejilla']) for v in lam.values()),
          src + ' lambda_por_brazo.*.extension_rejilla (brazos con rejilla extendida)')
    grid = frozen['configuracion']['lambda_rejilla']
    b.add('lamGridMin', min(grid), src + ' configuracion.lambda_rejilla (mínimo)')
    b.add('lamGridMax', max(grid), src + ' configuracion.lambda_rejilla (máximo)')
    b.add('lamGrid', ', '.join(str(x) for x in grid), src + ' configuracion.lambda_rejilla')
    b.add('mejorReal', LABELS[frozen['mejor_real']].lower(), src + ' mejor_real')
    b.add('selEcoVis', fmt(lam['ma_diaria']['visitas_ecobici'], 1), src + ' lambda_por_brazo.ma_diaria.visitas_ecobici')
    b.add('selMaVis', fmt(lam['ma_diaria']['visitas_confirmadas'], 1), src + ' lambda_por_brazo.ma_diaria.visitas_confirmadas')
    for key, path in [('topeVis', ('topes_base', 'visitas_por_decision')),
                      ('topeBicis', ('topes_base', 'bicis_por_visita'))]:
        b.add(key, frozen[path[0]][path[1]], src + ' ' + '.'.join(path))


def code_macros(b: Book):
    from ecosim import config as C
    from ecosim.pronostico import LGB_PARAMS
    src = 'ecosim/config.py'
    b.add('pickMin', C.PICKUP_MIN, src + ' PICKUP_MIN')
    b.add('delMin', C.DELIVERY_MIN, src + ' DELIVERY_MIN')
    b.add('stepMin', C.STEP_MIN, src + ' STEP_MIN')
    b.add('lagS', C.GBFS_COMMIT_LAG_S, src + ' GBFS_COMMIT_LAG_S')
    b.add('covMin', int(C.COVERAGE_MIN * 100), src + ' COVERAGE_MIN')
    b.add('transMin', C.DELIVERY_MIN - C.PICKUP_MIN, src + ' DELIVERY_MIN − PICKUP_MIN')
    b.add('entregaCorta', min(C.DELIVERY_SENS_MIN), src + ' DELIVERY_SENS_MIN (mínimo)')
    b.add('entregaLarga', max(C.DELIVERY_SENS_MIN), src + ' DELIVERY_SENS_MIN (máximo)')
    b.add('ventanaHoras', f'{C.WINDOW_MIN / 60:.1f}', src + ' WINDOW_MIN / 60')
    b.add('abiertaPct', int(C.INITIAL_OPEN_RENTING_MIN * 100), src + ' INITIAL_OPEN_RENTING_MIN')
    b.add('refDias', len(C.RUN1_EVAL_DAYS), src + ' RUN1_EVAL_DAYS (días de referencia de topes y validación)')
    b.add('nMax', max(C.N_GRID), src + ' N_GRID')
    b.add('lamGridN', ', '.join(str(x) for x in C.LAMBDA_GRID_N), src + ' LAMBDA_GRID_N')
    fold = next(f for f in C.FOLDS if f['name'] == 'prueba_1')
    months = (fold['train_end'].year - fold['train_start'].year) * 12 + fold['train_end'].month - fold['train_start'].month + 1
    b.add('trainMeses', months, src + ' FOLDS prueba_1 (meses de train_start a train_end)')
    corr = rows('corridas_run3.csv')
    sens = {r['day'] for r in corr if r['tag'] == 'sens_pares_sin_regla'}
    b.add('curvaDias', len(sens), 'ecosim/results/corridas_run3.csv, días de tag=sens_pares_sin_regla (sensibilidades)')
    pron = (ROOT / 'ecosim/pronostico.py').read_text()
    hist_days = {int(x) for x in re.findall(r'range\(max\(0, (?:self\.)?i - (\d+)\)', pron)}
    assert len(hist_days) == 1 and next(iter(hist_days)) % 7 == 0, hist_days
    b.add('semanasHist', next(iter(hist_days)) // 7, 'ecosim/pronostico.py, range(max(0, i − 28), i) / 7 días')
    b.add('cadaTicks', re.search(r'\.days % (\d+)', pron).group(1), 'ecosim/pronostico.py `_ticks` (fase módulo 12)')
    src = 'ecosim/pronostico.py LGB_PARAMS'
    b.add('lgbArboles', LGB_PARAMS['n_estimators'], src + ' n_estimators')
    b.add('lgbTasa', LGB_PARAMS['learning_rate'], src + ' learning_rate')
    b.add('lgbHojas', LGB_PARAMS['num_leaves'], src + ' num_leaves')
    b.add('lgbMinHoja', LGB_PARAMS['min_child_samples'], src + ' min_child_samples')
    b.add('lgbFilas', LGB_PARAMS['subsample'], src + ' subsample')
    b.add('lgbCols', LGB_PARAMS['colsample_bytree'], src + ' colsample_bytree')


def data_macros(b: Book):
    ch = json.loads((OUT / 'figs/checks_datos.json').read_text())
    src = 'report/figs/checks_datos.json '
    b.add('viajes', fmt(ch['viajes']), src + 'viajes (ene–nov 2025)')
    year = ch['viajes_por_dia_min'][0][:4]
    months = sorted(int(m) for m in ch['viajes_por_mes'])
    b.add('datosPeriodo', f'{MESES[months[0] - 1]} y {MESES[months[-1] - 1]} de {year}',
          src + 'viajes_por_mes (meses) y viajes_por_dia_min (año)')
    b.add('viajesDia', fmt(ch['viajes_por_dia_media']), src + 'viajes_por_dia_media')
    b.add('estaciones', ch['estaciones_distintas'], src + 'estaciones_distintas')
    b.add('bicisDistintas', fmt(ch['bicis_distintas']), src + 'bicis_distintas')
    b.add('durMed', f'{ch["duracion_mediana_min"]:.0f}', src + 'duracion_mediana_min')
    b.add('durP', f'{ch["duracion_p95_min"]:.0f}', src + 'duracion_p95_min')
    b.add('huecoMed', f'{ch["hueco_mediana_min"]:.1f}', src + 'hueco_mediana_min')
    b.add('huecoTarde', f'{ch["hueco_mediana_18_21"]:.1f}', src + 'hueco_mediana_18_21')
    b.add('huecoResto', f'{ch["hueco_mediana_resto"]:.1f}', src + 'hueco_mediana_resto')
    rutas = {r['']: r for r in rows('flota/rutas_osm_resumen.csv')}
    src = 'ecosim/results/flota/rutas_osm_resumen.csv '
    b.add('calleMed', f'{float(rutas["p50"]["calle km"]):.1f}', src + 'p50, calle km')
    b.add('picoMed', f'{float(rutas["p50"]["min a pico 13.5 km/h"]):.0f}', src + 'p50, min a pico 13.5 km/h')
    b.add('picoNoventa', f'{float(rutas["p90"]["min a pico 13.5 km/h"]):.0f}', src + 'p90, min a pico 13.5 km/h')
    b.add('picoNN', f'{float(rutas["p99"]["min a pico 13.5 km/h"]):.0f}', src + 'p99, min a pico 13.5 km/h')


def tables_md_macros(b: Book):
    t = (R / 'tablas.md').read_text()
    v1 = md_table(t, '## V1')[0]
    src = 'ecosim/results/tablas.md, sección V1'
    v1days = {r['day'] for r in rows('v1_run3.csv')}
    b.add('vDias', len(v1days), 'ecosim/results/v1_run3.csv, días (el encabezado de tablas.md dice 15, pero la tabla promedia estos días)')
    for key, col in [('vEobs', 'E_obs'), ('vFobs', 'F_obs'), ('vErep', 'E_replay'), ('vFrep', 'F_replay')]:
        b.add(key, fmt(num(v1[col])), src + ', ' + col)
    b.add('vRel', f'{(1 - num(v1["E_replay"]) / num(v1["E_obs"])) * 100:.0f}', src + ', 1 − E_replay/E_obs')
    b.add('vRelF', f'{(1 - num(v1["F_replay"]) / num(v1["F_obs"])) * 100:.0f}', src + ', 1 − F_replay/F_obs')
    sens = {(r['variante'], r['brazo']): r for r in md_table(t, '### Sensibilidad frente al mismo brazo')}
    src = 'ecosim/results/tablas.md, Sensibilidad frente al mismo brazo, '
    for var, key in [('sens_entrega_45', 'sCuarenta'), ('sens_entrega_75', 'sSetenta'),
                     ('sens_danadas_feed', 'sDan')]:
        r = sens[(var, 'ma_diaria')]
        b.add(key, sgn(num(r['delta_EF_base'])), src + var + ' ma_diaria delta_EF_base')
        b.add(key + 'Lo', sgn(num(r['IC95_inf'])), src + var + ' ma_diaria IC95_inf')
        b.add(key + 'Hi', sgn(num(r['IC95_sup'])), src + var + ' ma_diaria IC95_sup')
        o = sens[(var, 'oraculo_directo')]
        b.add(key + 'Or', sgn(num(o['delta_EF_base'])), src + var + ' oraculo_directo delta_EF_base')
    b.add('sSinRegla', fmt(num(sens[('sens_pares_sin_regla', 'ecobici')]['visitas'])),
          src + 'sens_pares_sin_regla ecobici visitas')
    b.add('sSoloUno', fmt(num(sens[('sens_pares_solo_1', 'ecobici')]['visitas'])),
          src + 'sens_pares_solo_1 ecobici visitas')
    b.add('sEcoEF', fmt(num(sens[('sens_pares_sin_regla', 'ecobici')]['EF'])),
          src + 'sens_pares_sin_regla ecobici EF')
    acc = {(r['mes'], r['variante']): r for r in md_table(t, '## Exactitud del pronóstico')}
    b.add('maeMes', mes('2025-09'), 'ecosim/results/tablas.md, Exactitud del pronóstico: primer mes de prueba')
    src = 'ecosim/results/tablas.md, Exactitud del pronóstico (neto, k=0), 2025-09, '
    for var, key in [('ma_diaria', 'maeMa'), ('lgbm_diario', 'maeLgbmD'), ('lgbm_directo', 'maeLgbmDir')]:
        b.add(key, f'{num(acc[("2025-09", var)]["mae"]):.2f}', src + var + ' mae')
        b.add(key + 'W', f'{num(acc[("2025-09", var)]["wape"]):.2f}', src + var + ' wape')


def asignador_macros(b: Book):
    t = (R / 'asignador/REPORT.md').read_text()
    rho = re.search(r'Spearman entre costo incremental pronosticado y E\+F real: mediana ([0-9.]+)', t).group(1)
    b.add('spearman', rho, 'ecosim/results/asignador/REPORT.md, mediana de Spearman')


def medicion_tables(b: Book):
    t = (R / 'medicion/REPORT.md').read_text()
    m = re.search(r'^## Topes \((\w+)–(\w+) (\d{4})', t, re.M)
    b.add('antPeriodo', f'{m.group(1)} a {m.group(2)} de {m.group(3)}',
          'ecosim/results/medicion/REPORT.md, encabezado de Topes (población de los topes hacia adelante)')
    imp = md_table(t, '## Impacto, ventana 05:00')
    src = 'ecosim/results/medicion/REPORT.md, Impacto, ventana 05:00–00:30'
    names = {'sin regla': 'Sin regla', '±1': r'Solo $\pm1$', 'hasta ±2': r'Hasta $\pm2$',
             'hasta ±3': r'Hasta $\pm3$', 'cualquier tamaño': 'Cualquier tamaño'}
    lines = [r'\begin{tabular}{@{}lrrrr@{}}', r'\toprule',
             r'Regla & Visitas & Entran & Salen & Visitas que quedan, \% \\', r'\midrule']
    for r in imp:
        lines.append(f'{names[r["regla"]]} & {r["visitas"]} & {r["A"]} & {r["R"]} & {r["% visitas"]} \\\\')
    b.add('visSinRegla', imp[0]['visitas'], src + ', sin regla, visitas')
    b.add('visConRegla', imp[-1]['visitas'], src + ', cualquier tamaño, visitas')
    b.add('visQuedan', imp[-1]['% visitas'], src + ', cualquier tamaño, % visitas')
    return '\n'.join(lines + [r'\bottomrule', r'\end{tabular}']) + '\n'


def onsite_macros(b: Book, test_days):
    """Cambios de no rentables: ¿cambia también el total de la estación?"""
    import pandas as pd
    m = pd.read_parquet(DERIVED / 'ecobici_moves.parquet', columns=['day', 'short_name', 't1', 'delta', 'par'])
    d = pd.read_parquet(DERIVED / 'damage_events.parquet')
    m = m[m['day'].astype(str).isin(test_days)].copy()
    d = d[d['day'].astype(str).isin(test_days)]
    m['total'] = m['delta'].where(~m['par'], 0)   # un par cuenta como total sin cambio
    x = d.merge(m[['day', 'short_name', 't1', 'total']].rename(columns={'t1': 't'}),
                on=['day', 'short_name', 't'], how='left')
    x['total'] = x['total'].fillna(0)
    same = x.loc[x['total'] == 0, 'n'].sum()
    src = ('data/derived/ecosim/damage_events.parquet × ecobici_moves.parquet, 152 días de prueba; '
           'cálculo en report/figs/numeros_run3.py')
    b.add('etiquetasDia', fmt(x['n'].sum() / len(test_days)), src + ' (bicis que cambian de etiqueta por día)')
    b.add('enSitioPct', f'{same / x["n"].sum() * 100:.0f}', src + ' (% de esos cambios con total de la estación igual)')


def figures(b: Book):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
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

    moves = pd.read_parquet(DERIVED / 'ecobici_moves.parquet', columns=['day', 't0', 't1', 'delta', 'par'])
    moves = moves[moves['day'].astype(str).str[:7].isin(set(FIG_MONTHS))
                  & ~moves['par'] & (moves['delta'] != 0)]
    src = ('data/derived/ecosim/ecobici_moves.parquet, sep–nov 2025, sin pares; '
           'cálculo en report/figs/numeros_run3.py')
    b.add('figPeriodo', periodo(FIG_MONTHS[0], FIG_MONTHS[-1]), 'FIG_MONTHS en report/figs/numeros_run3.py')
    sizes = moves['delta'].abs().to_numpy()
    cap_b = int(b.vals['topeBicis'])
    b.add('figVisitas', fmt(len(sizes)), src + ' (visitas en la figura de tamaños)')
    b.add('figBajoTope', f'{(sizes <= cap_b).mean() * 100:.1f}', src + ' (% de visitas con bicis ≤ tope)')

    def hist(values, edges, cut, xlabel, label, name, logy):
        """Barras con borde izquierdo < cut en azul (masa hasta el tope); el resto, gris."""
        fig, ax = plt.subplots(figsize=(3.4, 1.9))
        counts, _ = np.histogram(values, bins=edges)
        left = edges[:-1]
        colors = [BLUE if l < cut else GRAY for l in left]
        ax.bar(left, counts, width=np.diff(edges) * .86, align='edge', color=colors, linewidth=0)
        if logy:
            ax.set_yscale('log')
        line = cut - (edges[1] - edges[0]) * .07
        ax.axvline(line, color=INK, lw=.8, ls=(0, (3, 2)))
        top = ax.get_ylim()[1]
        ax.text(line + (edges[1] - edges[0]) * .6, top * (.5 if logy else .92), label,
                color=INK, fontsize=8, va='top')
        ax.set_xlabel(xlabel)
        ax.set_ylabel('Visitas' if logy else 'Intervalos')
        ax.tick_params(length=2)
        fig.tight_layout(pad=.3)
        save(fig, name)

    hist(np.minimum(sizes, 40), np.arange(1, 42), cap_b + 1,
         'Bicis por visita (40 incluye valores mayores)',
         f'tope = {cap_b} (p95)', 'tamanos-run3.pdf', True)
    g = moves.groupby(['day', 't0', 't1']).size().reset_index(name='v')
    per15 = (g['v'] * 15 / ((g['t1'] - g['t0']).dt.total_seconds() / 60)).to_numpy()
    cap_v = int(b.vals['topeVis'])
    b.add('figIntervalos', fmt(len(per15)), src + ' (intervalos en la figura de visitas)')
    b.add('figVisBajoTope', f'{(per15 <= cap_v).mean() * 100:.1f}', src + ' (% de intervalos con visitas ≤ tope)')
    hist(np.clip(per15, cap_v % 3, 121), np.arange(cap_v % 3, 125, 3), cap_v,
         'Visitas por intervalo, normalizadas a 15 min', f'tope = {cap_v} (p95)',
         'visitas-tope-run3.pdf', False)
    return out


def figure_ef(by):
    import matplotlib.pyplot as plt
    import numpy as np
    order = ['sin_rebalanceo', 'ecobici', 'ma_diaria', 'lgbm_diario', 'lgbm_directo',
             'oraculo_diario', 'oraculo_directo']
    vals = [mean(by[a], 'EF') / 1000 for a in order]
    fig, ax = plt.subplots(figsize=(3.4, 2.0))
    y = np.arange(len(order))[::-1]
    ax.barh(y, vals, height=.62, color=[BLUE if a == 'ecobici' else GRAY for a in order], linewidth=0)
    for yi, v in zip(y, vals):
        ax.text(v + 3, yi, f'{v:,.1f}', va='center', fontsize=8, color=INK)
    ax.set_yticks(y, [LABELS[a] for a in order])
    ax.set_xlim(0, max(vals) * 1.15)
    ax.set_xlabel('Miles de minutos-estación E+F por día')
    ax.spines['left'].set_visible(False)
    ax.tick_params(length=0, axis='y')
    ax.tick_params(length=2, axis='x')
    fig.tight_layout(pad=.3)
    buf = io.BytesIO()
    fig.savefig(buf, format='pdf', metadata={'CreationDate': None, 'Creator': None, 'Producer': None})
    plt.close(fig)
    return {OUT / 'figs/ef-brazos-run3.pdf': buf.getvalue()}


def table_arms(b: Book):
    v = b.vals
    lines = [r'\begin{tabular}{@{}llr@{}}', r'\toprule', r'Brazo & Qué decide las órdenes & $\lambda$ \\', r'\midrule',
             r'Sin rebalanceo & nada; solo viajes y etiquetas & --- \\',
             r'Ecobici (medido) & movimientos inferidos del feed & --- \\',
             f'Media móvil & asignador + media de 4 semanas & {v["lamMa"]} \\\\',
             f'LightGBM diario & asignador + LightGBM a las 05:00 & {v["lamLgbmD"]} \\\\',
             f'LightGBM directo & asignador + LightGBM cada 15 min & {v["lamLgbmDir"]} \\\\',
             f'Oráculo diario & asignador + viajes reales por hora & {v["lamOrD"]} \\\\',
             f'Oráculo directo & asignador + viajes reales, hora móvil & {v["lamOrDir"]} \\\\',
             r'\bottomrule', r'\end{tabular}']
    return '\n'.join(lines) + '\n'


FID_SRC = 'report/figs/fidelidad_ecobici.json '


def fecha(ds):
    """'2025-09-01' → '1 de septiembre de 2025'."""
    return f'{int(ds[8:])} de {mes(ds[:7])}'


def fidelidad_macros(b: Book, fid):
    """Ecobici real contra simulado: cifras de report/figs/fidelidad_ecobici.py."""
    pr, br16 = fid['prueba'], fid['brazos16']
    src = FID_SRC + 'prueba ('
    b.add('fdFeedE', fmt(pr['feed']['E']), src + 'E observado en las fotos; ecosim/results/medicion/ecobici_observado.csv)')
    b.add('fdFeedF', fmt(pr['feed']['F']), src + 'F observado en las fotos)')
    b.add('fdFeedEF', fmt(pr['feed']['EF']), src + 'E+F observado en las fotos)')
    b.add('fdSalidasDia', fmt(pr['salidas_dia']), src + 'salidas por día, data.trips en 05:00–00:30)')
    for arm, key in [('ecobici', 'eco'), ('ma_diaria', 'ma'), ('oraculo_directo', 'orDir'),
                     ('lgbm_directo', 'lgbmDir'), ('sin_rebalanceo', 'base')]:
        b.add(key + 'DesvPct', f"{pr['brazos'][arm]['pct']:.1f}",
              src + f'arm={arm}: (desvios_salida + desvios_llegada) / salidas, %)')
    b.add('fdEcoDesv', fmt(pr['brazos']['ecobici']['desvios']), src + 'arm=ecobici, desvíos por día)')
    src = FID_SRC + 'brazos16 ('
    b.add('cfDias', len(fid['dias16']), FID_SRC + 'dias16 (días con los siete brazos simulados)')
    b.add('cfPrimero', fecha(fid['dias16'][0]), FID_SRC + 'dias16 (primer día)')
    b.add('cfUltimo', fecha(fid['dias16'][-1]), FID_SRC + 'dias16 (último día)')
    e16 = br16['ecobici']
    b.add('cfSalidas', fmt(e16['salidas']), src + 'Σ snap[k].salidas)')
    b.add('cfEcoDesv', fmt(e16['desvios_salida'] + e16['desvios_llegada']), src + 'ecobici, Σ desvíos)')
    b.add('cfEcoPct', f"{e16['pct']:.2f}", src + 'ecobici, desvíos / salidas, %)')
    c1 = fid['causa_resolucion']
    src = FID_SRC + 'causa_resolucion ('
    b.add('cUnoSal', f"{c1['pct_sal']:.0f}", src + 'salidas desviadas en estación con entrega de Ecobici en el mismo cuarto, %)')
    b.add('cUnoLle', f"{c1['pct_lle']:.0f}", src + 'llegadas desviadas en estación con recogida de Ecobici en el mismo cuarto, %)')
    b.add('cUnoTSal', f"{c1['pct_tsal']:.1f}", src + 'todas las salidas en estación con entrega en el mismo cuarto, %)')
    b.add('cUnoTLle', f"{c1['pct_tlle']:.1f}", src + 'todas las llegadas en estación con recogida en el mismo cuarto, %)')
    c2 = fid['causa_cero']
    src = FID_SRC + 'causa_cero ('
    b.add('cDosPct', f"{c2['pct']:.1f}", src + 'salidas reales desde estación con 0 disponibles en la última foto previa, %)')
    b.add('cDosMin', f"{c2['pct_min']:.1f}", src + 'mínimo por día, %)')
    b.add('cDosMax', f"{c2['pct_max']:.1f}", src + 'máximo por día, %)')
    b.add('cDosNR', f"{c2['pct_no_rentables']:.0f}", src + 'de esas salidas, estación con no rentables > 0, %)')
    b.add('cDosEdad', f"{c2['edad_mediana_min']:.1f}", src + 'antigüedad mediana de la foto, min)')
    b.add('cDosSalidas', fmt(c2['salidas']), src + 'salidas reales con foto previa)')
    c3 = fid['causa_arrastre']
    hora = {x['hora']: x for x in c3['por_hora']}
    peak = max(c3['por_hora'], key=lambda x: x['difieren'])
    src = FID_SRC + 'causa_arrastre ('
    b.add('cTresEst', fmt(c3['estaciones_media']), src + 'estaciones comparadas por cuarto, media)')
    b.add('cTresSiete', fmt(hora['07:00']['difieren']), src + 'por_hora 07:00, estaciones que difieren)')
    b.add('cTresDoce', fmt(hora['12:00']['difieren']), src + 'por_hora 12:00, estaciones que difieren)')
    b.add('cTresPico', fmt(peak['difieren']), src + 'por_hora, máximo de estaciones que difieren)')
    b.add('cTresPicoHora', peak['hora'], src + 'por_hora, hora del máximo)')
    b.add('cTresPicoDif', f"{peak['dif_media']:.1f}", src + 'por_hora, diferencia media en la hora del máximo, bicis por estación)')
    b.add('cTresCrudoMin', fmt(c3['crudo_difieren_min']), src + 'sin corregir la antigüedad de la foto, mínimo desde 06:00)')
    b.add('cTresCrudoMax', fmt(c3['crudo_difieren_max']), src + 'sin corregir, máximo)')
    b.add('cTresCrudoDifMax', f"{c3['crudo_dif_media_max']:.1f}", src + 'sin corregir, diferencia media máxima)')
    dm = fid['demanda']
    eco, ma = dm['brazos']['ecobici'], dm['brazos']['ma_diaria']
    src = FID_SRC + 'demanda ('
    b.add('dTop', dm['estaciones_top'], src + 'estaciones del decil con más viajes)')
    b.add('dEst', dm['estaciones'], src + 'estaciones)')
    b.add('dTopViajes', f"{dm['pct_viajes_top']:.0f}", src + 'salidas + llegadas en ese decil, %)')
    h = dm['horas_pico']
    blocks, run = [], [h[0]]
    for x in h[1:]:
        if x == run[-1] + 1:
            run.append(x)
        else:
            blocks.append(run)
            run = [x]
    blocks.append(run)
    b.add('dPicoHoras', ' y '.join(f'{r[0]:02d}:00--{r[-1]:02d}:59' for r in blocks), src + 'horas_pico, las seis horas con más salidas)')
    b.add('dPicoSal', f"{dm['pct_salidas_pico']:.0f}", src + 'salidas en horas pico, %)')
    for key, arm_v, field in [('dEFTopEco', eco, 'EF_top'), ('dEFTopMa', ma, 'EF_top'),
                              ('dEFRestoEco', eco, 'EF_resto'), ('dEFRestoMa', ma, 'EF_resto'),
                              ('dETopEco', eco, 'E_top'), ('dETopMa', ma, 'E_top')]:
        who = 'ecobici' if arm_v is eco else 'ma_diaria'
        b.add(key, fmt(arm_v[field]), src + f'{who}, {field}, minutos por día muestreados cada 15 min)')
    b.add('dEFTopRed', f"{(1 - ma['EF_top'] / eco['EF_top']) * 100:.0f}", src + '1 − EF_top(ma_diaria)/EF_top(ecobici), %)')
    b.add('dEFRestoRed', f"{(1 - ma['EF_resto'] / eco['EF_resto']) * 100:.0f}", src + '1 − EF_resto(ma_diaria)/EF_resto(ecobici), %)')
    b.add('dMaExtra', fmt(ma['extra_dia']), src + 'ma_diaria, desvíos por día menos los de ecobici)')
    b.add('dMaExtraTop', f"{ma['extra_pct_top']:.0f}", src + 'ma_diaria, % de los desvíos extra en el decil)')
    b.add('dMaExtraPico', f"{ma['extra_pct_pico']:.0f}", src + 'ma_diaria, % de los desvíos extra en horas pico)')
    b.add('dEcoPico', f"{eco['pct_desvios_pico']:.0f}", src + 'ecobici, % de sus desvíos en horas pico)')
    lg = [dm['brazos'][a]['extra_pct_top'] for a in ('lgbm_diario', 'lgbm_directo')]
    b.add('dLgbmExtraTopMin', f'{min(lg):.0f}', src + 'lgbm_diario y lgbm_directo, % de desvíos extra en el decil, mínimo)')
    b.add('dLgbmExtraTopMax', f'{max(lg):.0f}', src + 'lgbm_diario y lgbm_directo, % de desvíos extra en el decil, máximo)')
    ej = fid['ejemplo']
    src = FID_SRC + 'ejemplo ('
    b.add('ejEst', ej['estacion'], src + 'estación)')
    b.add('ejNombre', ej['nombre'].removeprefix(f"CE-{ej['estacion']} ").replace(' - ', ' -- ').replace('Av. ', ''),
          src + 'nombre en station_information)')
    b.add('ejDia', fecha(ej['dia']), src + 'día)')
    b.add('ejDesde', ej['desde'], src + 'inicio del cuarto)')
    b.add('ejHasta', ej['hasta'], src + 'fin del cuarto)')
    fotos = ej['fotos']
    for key, f in zip(['ejFotoA', 'ejFotoB', 'ejFotoC'], fotos):
        b.add(key, f['disponibles'], src + f"foto de las {f['hora']}, disponibles)")
        b.add(key + 'Hora', f['hora'], src + 'hora del estado de la foto)')
    assert len(fotos) == 3
    mv = [m for m in ej['movimientos'] if m['estacion'] == ej['estacion'] and m['delta'] > 0]
    assert len(mv) == 2
    for key, m in zip(['ejMovA', 'ejMovB'], mv):
        b.add(key, m['delta'], src + f"movimiento de Ecobici en {ej['estacion']}, ecobici_moves.parquet)")
        b.add(key + 'Hora', m['hora'], src + 'hora del movimiento (t1))')
    vec = {m['estacion']: m for m in ej['movimientos'] if m['delta'] > 0}
    assert vec['554']['hora'] == vec['547']['hora']
    b.add('ejMovCincuenta', vec['554']['delta'], src + 'movimiento de Ecobici en 554)')
    b.add('ejMovCuarenta', vec['547']['delta'], src + 'movimiento de Ecobici en 547)')
    b.add('ejMovVecHora', vec['554']['hora'], src + 'hora de esos movimientos)')
    b.add('ejSal', ej['salidas_reales'], src + 'salidas reales del cuarto desde la estación)')
    b.add('ejDesv', ej['desvios'], src + 'salidas desviadas en el simulador en ese cuarto)')
    cad = {c['estacion']: c for c in ej['cadena']}
    for st, key in [('265', 'Dos'), ('554', 'Cinco'), ('547', 'Cuatro'), ('029', 'Cero')]:
        b.add('ej' + key + 'N', cad[st]['desvios'], src + f'cadena, desvíos a la {st})')
        b.add('ej' + key + 'M', cad[st]['metros'], src + f'cadena, distancia en línea recta a la {st}, m)')
    b.add('ejVecIni', ej['v265_bicis_inicio'], src + 'bicis simuladas en 265 al inicio del cuarto)')
    b.add('ejVecFin', ej['v265_bicis_fin'], src + 'bicis simuladas en 265 al final del cuarto)')
    b.add('ejNVec', len(ej['cadena']), src + 'cadena, estaciones distintas que reciben desvíos)')


def table_fidelidad(fid):
    pr = fid['prueba']
    lines = [r'\begin{tabular}{@{}lrrrr@{}}', r'\toprule',
             r' & E & F & E+F & Desvíos \\', r'\midrule',
             f"Feed real (fotos) & {fmt(pr['feed']['E'])} & {fmt(pr['feed']['F'])} & {fmt(pr['feed']['EF'])} & 0 \\\\"]
    for arm, name in [('ecobici', 'Ecobici simulado'), ('ma_diaria', 'Media móvil')]:
        v = pr['brazos'][arm]
        lines.append(f"{name} & {fmt(v['E'])} & {fmt(v['F'])} & {fmt(v['EF'])} & "
                     f"{fmt(v['desvios'])} ({v['pct']:.1f}\\%) \\\\")
    return '\n'.join(lines + [r'\bottomrule', r'\end{tabular}']) + '\n'


def table_causa_cero(fid):
    lines = [r'\begin{tabular}{@{}lrrrr@{}}', r'\toprule',
             r'Día & Salidas & En 0, \% & Con no rent., \% & Antig., min \\', r'\midrule']
    for x in fid['causa_cero']['por_dia']:
        lines.append(f"{x['dia']} & {fmt(x['salidas'])} & {x['pct']:.1f} & {x['pct_no_rentables']:.0f} & "
                     f"{x['edad_mediana_min']:.1f} \\\\")
    c = fid['causa_cero']
    lines += [r'\midrule', f"Total & {fmt(c['salidas'])} & {c['pct']:.1f} & {c['pct_no_rentables']:.0f} & "
                            f"{c['edad_mediana_min']:.1f} \\\\"]
    return '\n'.join(lines + [r'\bottomrule', r'\end{tabular}']) + '\n'


def table_brazos16(fid):
    lines = [r'\begin{tabular}{@{}lrrr@{}}', r'\toprule',
             r'Brazo & Desvíos & Salidas & \% \\', r'\midrule']
    for arm in ARMS:
        v = fid['brazos16'][arm]
        lines.append(f"{LABELS[arm]} & {fmt(v['desvios_salida'] + v['desvios_llegada'])} & "
                     f"{fmt(v['salidas'])} & {v['pct']:.2f} \\\\")
    return '\n'.join(lines + [r'\bottomrule', r'\end{tabular}']) + '\n'


PROSE = [
    ('1,990 de 4,139 respuestas eligen "no siempre hay bicis disponibles" como principal desventaja; '
     '114 eligen "no siempre hay espacios para anclar"',
     'Encuesta ECOBICI 2025, pregunta 18, `encuesta2025` en referencias.bib '
     '(https://ecobici.cdmx.gob.mx/wp-content/uploads/2026/02/Encuesta-ECOBICI-2025-1.pdf)'),
    ('34.56 millones de viajes en un día entre semana', 'EOD 2017 del INEGI, `inegi2017`'),
    ('9,308 bicicletas; BikeSantiago y BikeItaú, 3,500 cada uno', 'El Universal 2025-07-23 con cifras de Semovi, `eluniversal2025semovi`'),
    ('unas 687 estaciones', 'Expansión Política 2026-08-31, `expansion2026`'),
    ('más de 19.4 millones de viajes y 284,289 personas usuarias en 2025', 'Ecobici 2025-12-22, `ecobici2025balance`'),
    ('05:00 a 00:30', 'Términos y condiciones de Ecobici, `ecobici_horario`'),
    ('13.5 km/h en hora pico', 'TomTom Traffic Index 2025, `tomtom2025`'),
    ('trayectos de hasta unos 70 minutos en hora pico', 'consulta en Waze (sin fuente archivada; cifra de contexto)'),
    ('el feed se actualiza cada diez segundos', 'campo `ttl` = 10 s de https://gbfs.mex.lyftbikes.com/gbfs/gbfs.json'),
    ('12:07:09, bici 4201141, estación 002 (ejemplo de par)',
     'ecosim/results/medicion/REPORT.md, Ejemplos con viajes exactos'),
    ('estación con cinco disponibles y dos no rentables (Tabla de etiquetas)', 'ejemplo ilustrativo, no es dato medido'),
]


def manifest(b: Book):
    lines = ['# Procedencia de cada cifra del reporte (reporte-caso.tex)', '',
             'Regenerar: `uv run python3 report/figs/fidelidad_ecobici.py` (lee las simulaciones guardadas y las fotos; '
             'escribe `figs/fidelidad_ecobici.json`) y después `uv run python3 report/figs/numeros_run3.py`. '
             'Los dos aceptan `--check`.',
             'Comprobar sin escribir: `uv run python3 report/figs/numeros_run3.py --check` '
             '(recalcula todo desde las fuentes y exige que macros, tablas, figuras y este archivo no cambien).',
             '', '## Macros (`report/numeros-run3.tex`)', '', '| macro | valor | origen |', '|---|---:|---|']
    lines += [f'| `\\r{k}` | {v} | {b.src[k]} |' for k, v in b.vals.items()]
    lines += ['', '## Tablas y figuras', '',
              '| artefacto | origen |', '|---|---|',
              '| `tablas/principal-run3.tex` | resultados.csv (tag=prueba), IC95 t pareado por día |',
              '| `tablas/brazos-run3.tex` | frozen.json lambda_por_brazo |',
              '| `tablas/pares-run3.tex` | medicion/REPORT.md, Impacto, ventana 05:00–00:30 |',
              '| `tablas/fidelidad-run3.tex` | fidelidad_ecobici.json `prueba` (días de prueba): E y F de ecobici_observado.csv y resultados.csv; desvíos de resultados.csv / salidas de data.trips |',
              '| `tablas/causa-cero-run3.tex` | fidelidad_ecobici.json `causa_cero.por_dia` (16 días) |',
              '| `tablas/desvios16-run3.tex` | fidelidad_ecobici.json `brazos16` (16 días, Σ snap[k]) |',
              '| `figs/tamanos-run3.pdf` | ecobici_moves.parquet, sep–nov 2025, sin pares; tope de frozen.json |',
              '| `figs/visitas-tope-run3.pdf` | ecobici_moves.parquet, sep–nov 2025, visitas por intervalo × 15 / minutos del intervalo |',
              '| `figs/ef-brazos-run3.pdf` | resultados.csv (tag=prueba), media de EF por brazo |',
              '', '## Cifras externas o ilustrativas escritas en prosa', '']
    lines += [f'- {what}: {src}.' for what, src in PROSE]
    return '\n'.join(lines) + '\n'


def macros_tex(b: Book):
    return ''.join(f'\\newcommand{{\\r{k}}}{{{v}}}\n' for k, v in b.vals.items())


def generate():
    b = Book()
    by, pct, _ = results_macros(b)
    frozen = json.loads((R / 'frozen.json').read_text())
    frozen_macros(b, frozen)
    code_macros(b)
    data_macros(b)
    tables_md_macros(b)
    pares = medicion_tables(b)
    test_days = sorted({r['day'] for r in by['ecobici']})
    b.add('pruebaPeriodo', periodo(test_days[0][:7], test_days[-1][:7]),
          RESULTS_SRC + '; primer y último día de prueba')
    sel = sorted(d['day'] if isinstance(d, dict) else d for d in frozen['seleccion'])
    assert sel[0][:7] == sel[-1][:7]
    b.add('selMes', mes(sel[0][:7]), 'ecosim/results/frozen.json seleccion (mes de los días)')
    from ecosim import config as C
    ref = sorted(C.RUN1_EVAL_DAYS)
    b.add('refPeriodo', periodo(ref[0][:7], ref[-1][:7]), 'ecosim/config.py RUN1_EVAL_DAYS (primer y último día)')
    onsite_macros(b, test_days)
    fid = json.loads((OUT / 'figs/fidelidad_ecobici.json').read_text())
    assert fid['prueba']['dias'] == len(test_days)
    fidelidad_macros(b, fid)
    files = figures(b)
    files.update(figure_ef(by))
    files[OUT / 'tablas/principal-run3.tex'] = table_main(by, pct, fid).encode()
    files[OUT / 'tablas/fidelidad-run3.tex'] = table_fidelidad(fid).encode()
    files[OUT / 'tablas/causa-cero-run3.tex'] = table_causa_cero(fid).encode()
    files[OUT / 'tablas/desvios16-run3.tex'] = table_brazos16(fid).encode()
    files[OUT / 'tablas/brazos-run3.tex'] = table_arms(b).encode()
    files[OUT / 'tablas/pares-run3.tex'] = pares.encode()
    files[OUT / 'numeros-run3.tex'] = macros_tex(b).encode()
    files[OUT / 'fuentes-numeros.md'] = manifest(b).encode()
    return files, b


def check_prose():
    """Las cifras externas citadas en prosa siguen escritas igual en el texto."""
    tex = (OUT / 'reporte-caso.tex').read_text()
    for literal in ('1,990', '4,139', '114', '34.56', '9,308', '3,500', '687', '19.4', '284,289', '13.5', '05:00', '00:30'):
        assert literal in tex, f'Falta en el texto la cifra externa {literal}'
    med = (R / 'medicion/REPORT.md').read_text()
    for literal in ('4201141', '12:07:09'):
        assert literal in med and literal in tex, f'Ejemplo de par no coincide: {literal}'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    files, b = generate()
    if args.check:
        bad = [str(path.relative_to(ROOT)) for path, content in files.items()
               if not path.exists() or path.read_bytes() != content]
        assert not bad, f'Desactualizado o distinto de su origen: {bad}'
        tex = (OUT / 'reporte-caso.tex').read_text()
        latex = {'ef', 'aggedbottom', 'aggedright', 'enewcommand', 'floor', 'ho'}  # \ref, \raggedbottom, \renewcommand, \rfloor, \rho
        used = set(re.findall(r'\\r([A-Za-z]+)', tex)) - latex
        missing = sorted(u for u in used if u not in b.vals)
        assert not missing, f'Macros usadas sin origen: {missing}'
        check_prose()
        print(f'OK --check: {len(b.vals)} macros, {len(files)} artefactos idénticos a su origen; '
              f'{len(used)} macros usadas en el texto, todas con origen')
    else:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        print(f'Escritos {len(files)} artefactos y {len(b.vals)} macros')


if __name__ == '__main__':
    main()
