"""Genera `report/numeros.tex` (macros `\\nr{llave}`) y las tablas de resultados
`report/tablas/*.tex` a partir de los archivos del run 2. Así cada número de
resultados del PDF sale de un archivo, y al cerrar el run 2 basta con volver a correr:

uv run python report/figs/numeros.py

Fuentes (solo lectura):
* `RESULTS` (ver `_style.py`): resultados.csv, frozen.json, v1/v1_resumen.json, donde_falla.*,
  medicion/ecobici_stats.json, pronostico/, asignador/validation_summary.json;
* `report/figs/*.json|csv` que escriben los otros scripts de esta carpeta.
"""
import json

import numpy as np
import pandas as pd

from _style import FIGS, RESULTS

REPORT = FIGS.parent
TAB = REPORT / "tablas"
TAB.mkdir(exist_ok=True)
N = {}          # llave → texto
SRC = {}        # llave → fuente (para fuentes-numeros.md)


def fmt(x, nd=0):
    if isinstance(x, str):
        return x
    s = f"{x:,.{nd}f}"
    return s.replace("-", "$-$", 1) if s.startswith("-") else s


def put(key, val, src, nd=0, pct=False):
    txt = fmt(val, nd) + ("\\%" if pct else "")
    N[key] = txt
    SRC[key] = (txt.replace("\\%", "%"), src)


R = RESULTS
res = pd.read_csv(R / "resultados.csv", dtype={"day": str})
frozen = json.loads((R / "frozen.json").read_text())
v1 = json.loads((R / "v1" / "v1_resumen.json").read_text())
df = json.loads((R / "donde_falla.json").read_text())
dfc = pd.read_csv(R / "donde_falla.csv", dtype={"short_name": str})
efb = pd.read_csv(R / "ef_por_bloque.csv")
stats = json.loads((R / "medicion" / "ecobici_stats.json").read_text())
acc = pd.read_csv(R / "pronostico" / "accuracy_by_lead.csv")
val = json.loads((R / "asignador" / "validation_summary.json").read_text())
chk = json.loads((FIGS / "checks_datos.json").read_text())
reb = json.loads((FIGS / "rebalanceo_ecobici.json").read_text())
r2 = json.loads((FIGS / "resultados_run2.json").read_text())
FR = "resultados.csv"

# ------------------------------------------------------------------ V1
e = v1["ecobici|auto"]
src = "v1/v1_resumen.json[ecobici|auto]"
for k, kk in (("E", "mean_abs_rel_E"), ("F", "mean_abs_rel_F"), ("Eman", "mean_abs_rel_E_manana"),
              ("Fman", "mean_abs_rel_F_manana"), ("Etar", "mean_abs_rel_E_tarde"), ("Ftar", "mean_abs_rel_F_tarde"),
              ("Enoc", "mean_abs_rel_E_noche"), ("Fnoc", "mean_abs_rel_F_noche")):
    put(f"v1.{k}", 100 * e[kk], src + "." + kk, 1, pct=True)
put("v1.stockmae", e["stock_mae"], src + ".stock_mae", 2)
put("v1.danmae", e["danadas_mae"], src + ".danadas_mae", 2)
put("v1.diasabajo", e["dias_E_abajo"], src + ".dias_E_abajo")
put("v1.Eobs", e["E_obs"], src + ".E_obs")
put("v1.Fobs", e["F_obs"], src + ".F_obs")
put("v1.Esim", e["E_sim"], src + ".E_sim")
t1 = v1["ecobici_t1|auto"]
put("v1.t1E", 100 * t1["mean_abs_rel_E"], "v1/v1_resumen.json[ecobici_t1|auto].mean_abs_rel_E", 1, pct=True)
st = v1["ecobici_stock|fixed"]
put("v1.fijasE", 100 * st["mean_abs_rel_E"], "v1/v1_resumen.json[ecobici_stock|fixed].mean_abs_rel_E", 1, pct=True)
put("v1.fijasEman", 100 * st["mean_abs_rel_E_manana"], "v1/v1_resumen.json[ecobici_stock|fixed].mean_abs_rel_E_manana", 1, pct=True)
put("v1.fijasdanmae", st["danadas_mae"], "v1/v1_resumen.json[ecobici_stock|fixed].danadas_mae", 2)
put("v1.undoE", 100 * v1["ecobici_undo|auto"]["mean_abs_rel_E"], "v1/v1_resumen.json[ecobici_undo|auto].mean_abs_rel_E", 1, pct=True)

# ------------------------------------------------------------------ selección
put("sel.lam", frozen["lam"], "frozen.json.lam")
put("sel.retiro", frozen["retiro"], "frozen.json.retiro")
re_ = frozen["retiro_eleccion"]
put("sel.retirogana", re_["gana_en"], "frozen.json.retiro_eleccion.gana_en")
put("sel.retiroempata", re_["empata_en"], "frozen.json.retiro_eleccion.empata_en")
put("sel.retirode", re_["de"], "frozen.json.retiro_eleccion.de")
put("sel.hmax", frozen["h_max"], "frozen.json.h_max")
for a, b in frozen["best"].items():
    put(f"sel.{a}.H", b["H"], f"frozen.json.best.{a}.H")
    put(f"sel.{a}.f", b["f"], f"frozen.json.best.{a}.f")
    put(f"sel.{a}.EF", b["EF_seleccion"], f"frozen.json.best.{a}.EF_seleccion")
put("sel.bestreal", frozen["best_real"], "frozen.json.best_real")
for arm, d in frozen["h_busqueda"]["EF"].items():
    for h, v in d.items():
        put(f"hsel.{arm}.{h}", v, f"frozen.json.h_busqueda.EF.{arm}.{h}")
for c in r2["curva_lambda"]:
    put(f"lam.{c['lam']:g}.EF", c["EF"], f"frozen.json.curva[retiro={frozen['retiro']},lam={c['lam']:g}].EF")
    put(f"lam.{c['lam']:g}.moves", c["moves"], f"frozen.json.curva[...lam={c['lam']:g}].moves")
put("lam.knee.moves", frozen["kneedle"]["knee_moves"], "frozen.json.kneedle.knee_moves")
put("selEco.EF", r2["seleccion_ecobici"]["EF"], "report/figs/ecobici_seleccion.csv (media arm=ecobici)")
put("selEco.moves", r2["seleccion_ecobici"]["moves"], "report/figs/ecobici_seleccion.csv (media arm=ecobici)")
put("selEco.bikes", r2["seleccion_ecobici"]["bikes_moved"], "report/figs/ecobici_seleccion.csv (media arm=ecobici)")
put("selBase.EF", r2["seleccion_baseline_EF"], "report/figs/ecobici_seleccion.csv (media arm=baseline)")

# ------------------------------------------------------------------ brazos (evaluación)
ev = res[(res.split == "evaluacion") & (res.tag == "eval")]
eco_day = ev[(ev.arm == "ecobici") & (ev.damage == "auto")].set_index("day")["EF"]
B = r2["brazos_eval"]
for lab, key in (("Ecobici", "eco"), ("daily", "daily"), ("ma", "ma"), ("model", "model"), ("oracle", "oracle"),
                 ("no hacer nada", "base")):
    b = B[lab]
    s = f"{FR} [split=evaluacion, tag=eval, arm={lab}] media por día"
    put(f"arm.{key}.EF", b["EF"], s)
    put(f"arm.{key}.moves", b["moves"], s)
    for fr in ("manana", "tarde", "noche"):
        put(f"arm.{key}.{fr}", b[fr], s)
    if "bikes_moved" in b:
        put(f"arm.{key}.bikes", b["bikes_moved"], s)
    if key not in ("eco", "base"):
        put(f"arm.{key}.vseco", -b["vs_ecobici_pct"], s + " vs Ecobici", 0, pct=True)
        put(f"arm.{key}.gana", b["gana"], s + " días con E+F < Ecobici")
        put(f"arm.{key}.rmin", -b["rango_vs_ecobici_pct"][1], s + " vs Ecobici, peor día", 0, pct=True)
        put(f"arm.{key}.rmax", -b["rango_vs_ecobici_pct"][0], s + " vs Ecobici, mejor día", 0, pct=True)
        put(f"arm.{key}.recbod", b["recorte_bodega"], s + " recorte_bodega")
put("arm.oracle.vsbase", 100 * (1 - B["oracle"]["EF"] / B["no hacer nada"]["EF"]), FR + " oracle vs baseline", 0, pct=True)
put("arm.daily.vsbase", 100 * (1 - B["daily"]["EF"] / B["no hacer nada"]["EF"]), FR + " daily vs baseline", 0, pct=True)
put("arm.eco.vsbase", 100 * (1 - B["Ecobici"]["EF"] / B["no hacer nada"]["EF"]), FR + " ecobici vs baseline", 0, pct=True)
put("gap.daily.oracle", B["daily"]["EF"] - B["oracle"]["EF"], FR + " daily − oracle")
put("gap.eco.daily", B["Ecobici"]["EF"] - B["daily"]["EF"], FR + " ecobici − daily")
pol = res[res.tag.ne("v1") & ~res.arm.isin(["baseline", "ecobici", "ecobici_stock", "ecobici_t1", "ecobici_undo"])]
put("runs.pol", len(pol), FR + " corridas de política (todas las etiquetas)")
put("runs.fallbacks", pol.fallbacks.sum(), FR + " suma de fallbacks")
put("runs.recmov", pol.recorte_por_movimiento.max(), FR + " máx recorte_por_movimiento")
put("runs.decisions", pol.n_decisions.sum(), FR + " suma de n_decisions")
put("runs.nonopt", pol.non_optimal.sum(), FR + " suma de non_optimal")
put("runs.dec_s", pol.decision_s_mean.mean(), FR + " media de decision_s_mean", 2)
put("runs.bodmax", pol.bodega_max.max(), FR + " máx bodega_max (políticas)")
put("runs.bodmin", pol.bodega_min.min(), FR + " mín bodega_min (políticas)")

# L ∈ {45, 30}
evL = res[(res.split == "evaluacion") & (res.tag == "eval_L")]
for (arm, L), g in evL.groupby(["arm", "L"]):
    put(f"L{int(L)}.{arm}", g.EF.mean(), f"{FR} [tag=eval_L, arm={arm}, L={int(L)}]")

# sensibilidades
sens = res[(res.split == "evaluacion") & res.tag.str.startswith(("sens_", "diag_"))]
for (tag, arm), g in sens.groupby(["tag", "arm"]):
    put(f"sens.{tag}.{arm}", g.EF.mean(), f"{FR} [tag={tag}, arm={arm}]")
    put(f"sens.{tag}.{arm}.moves", g.moves.mean(), f"{FR} [tag={tag}, arm={arm}] moves")
    put(f"sens.{tag}.{arm}.manana", g.EF_manana.mean(), f"{FR} [tag={tag}, arm={arm}] EF_manana")
    gana = int((g.set_index("day").EF < eco_day).sum())
    put(f"sens.{tag}.{arm}.gana", gana, f"{FR} [tag={tag}, arm={arm}] días que gana a Ecobici")
eco_t1 = ev[(ev.arm == "ecobici_stock")]
put("arm.ecostock.EF", eco_t1.EF.mean(), f"{FR} [tag=eval, arm=ecobici_stock]")
put("arm.ecostock.manana", eco_t1.EF_manana.mean(), f"{FR} [tag=eval, arm=ecobici_stock] EF_manana")
put("arm.ecostock.moves", eco_t1.moves.mean(), f"{FR} [tag=eval, arm=ecobici_stock] moves")
bf = ev[(ev.arm == "baseline") & (ev.damage == "fixed")]
put("arm.basefix.manana", bf.EF_manana.mean(), f"{FR} [tag=eval, baseline fixed] EF_manana")
for arm in ("daily", "ma", "model", "oracle"):
    g = ev[(ev.arm == arm)]
    put(f"arm.{arm}.movman", g.moves_manana.mean(), f"{FR} [tag=eval, arm={arm}] moves_manana")
put("arm.eco.movman", ev[(ev.arm == "ecobici") & (ev.damage == "auto")].moves_manana.mean(), FR + " ecobici moves_manana")

# ------------------------------------------------------------------ run 1 (congelado)
r1 = pd.read_csv(R / "run1" / "resultados.csv", dtype={"day": str}) if (R / "run1" / "resultados.csv").exists() else None
r1c = json.loads((R / "run1" / "frozen.json").read_text()) if (R / "run1" / "frozen.json").exists() else None
RUN1 = {"baseline": 72477, "ecobici": 34325, "oracle": 13798, "ma": 18822, "model": 18888, "daily": 19647}
SRC_RUN1 = "docs/executor/runs/2026-09-28-ecosim-run1/NOTAS-post-run.md y results/run1/tablas.md"
for k, v in RUN1.items():
    put(f"run1.{k}", v, SRC_RUN1)

# ------------------------------------------------------------------ dónde falla
put("df.top20pct", 100 * df["EF_top20_mejor_real"] / df["EF_total_mejor_real_15dias"], "donde_falla.json", 1, pct=True)
row = dfc[(dfc.short_name == "273-274") & (dfc.hora == "07:30")].iloc[0]
for k in ("EF", "EF_oracle", "EF_ecobici", "sal_pron", "sal_real", "dias_censurado"):
    put(f"df.hub.{k}", row[k], f"donde_falla.csv [273-274, 07:30].{k}")
b0 = efb[efb.block == 0].iloc[0]
put("df.h0", b0["oracle"], "ef_por_bloque.csv [block=0].oracle")
put("df.h0pct", 100 * b0["oracle"] / B["oracle"]["EF"], "ef_por_bloque.csv / resultados.csv", 0, pct=True)

# ------------------------------------------------------------------ pronóstico y asignador
for split in ("evaluacion", "seleccion"):
    a = acc[acc["split"] == split] if "split" in acc.columns else acc
put("val.spearman", val["spearman_hull"]["mediana"], "ecosim/results/asignador/validation_summary.json.spearman_hull.mediana", 3)
put("val.spearmanmin", val["spearman_hull"]["min"], "validation_summary.json.spearman_hull.min", 3)
put("val.nge", val["spearman_hull"]["n_ge_0.8"], "validation_summary.json.spearman_hull.n_ge_0.8")
put("val.n", val["n_decisiones"], "validation_summary.json.n_decisiones")


# ------------------------------------------------------------------ datos (checks_datos.json)
C = "report/figs/checks_datos.json"
put("d.viajes", chk["viajes"], C + ".viajes")
put("d.viajesM", chk["viajes"] / 1e6, C + ".viajes (millones)", 1)
put("d.estaciones", chk["estaciones_distintas"], C + ".estaciones_distintas")
put("d.bicis", chk["bicis_distintas"], C + ".bicis_distintas")
put("d.dias", chk["dias"], C + ".dias")
put("d.durmed", chk["duracion_mediana_min"], C + ".duracion_mediana_min", 1)
put("d.durp95", chk["duracion_p95_min"], C + ".duracion_p95_min", 1)
put("d.largos", chk["viajes_mas_de_1_dia"], C + ".viajes_mas_de_1_dia")
put("d.pordia", chk["viajes_por_dia_media"], C + ".viajes_por_dia_media")
put("d.pordiamed", chk["viajes_por_dia_mediana"], C + ".viajes_por_dia_mediana")
put("d.pordiamin", chk["viajes_por_dia_min"][1], C + ".viajes_por_dia_min")
put("d.pordiamindia", chk["viajes_por_dia_min"][0], C + ".viajes_por_dia_min")
put("d.pordiamax", chk["viajes_por_dia_max"][1], C + ".viajes_por_dia_max")
put("d.pordiamaxdia", chk["viajes_por_dia_max"][0], C + ".viajes_por_dia_max")
put("d.wd", chk["viajes_por_dia_por_tipo"]["weekday"], C + ".viajes_por_dia_por_tipo.weekday")
put("d.we", chk["viajes_por_dia_por_tipo"]["weekend"], C + ".viajes_por_dia_por_tipo.weekend")
put("d.hol", chk["viajes_por_dia_por_tipo"]["holiday"], C + ".viajes_por_dia_por_tipo.holiday")
put("d.nwd", chk["dias_por_tipo"]["weekday"], C + ".dias_por_tipo.weekday")
put("d.nwe", chk["dias_por_tipo"]["weekend"], C + ".dias_por_tipo.weekend")
put("d.nhol", chk["dias_por_tipo"]["holiday"], C + ".dias_por_tipo.holiday")
for dname, v in chk["viajes_por_dia_semana"].items():
    put(f"d.dow.{dname.replace('é', 'e').replace('á', 'a')}", v, C + f".viajes_por_dia_semana.{dname}")
w = chk["viajes_por_semana"]
put("d.sem", w["media"], C + ".viajes_por_semana.media")
put("d.semmin", w["min"][1], C + ".viajes_por_semana.min")
put("d.semmindia", w["min"][0], C + ".viajes_por_semana.min (domingo de cierre)")
put("d.semmax", w["max"][1], C + ".viajes_por_semana.max")
put("d.semmaxdia", w["max"][0], C + ".viajes_por_semana.max (domingo de cierre)")
put("d.nsem", w["n_semanas"], C + ".viajes_por_semana.n_semanas")
put("d.maxhora", chk["max_viajes_por_hora"][1], C + ".max_viajes_por_hora")
put("d.maxhoraen", chk["max_viajes_por_hora"][0][:16], C + ".max_viajes_por_hora")
put("d.picowd", chk["perfil_hora_entre_semana_pico"][1], C + ".perfil_hora_entre_semana_pico")
put("d.picowdh", chk["perfil_hora_entre_semana_pico"][0], C + ".perfil_hora_entre_semana_pico")
put("d.picowe", chk["perfil_hora_fin_semana_pico"][1], C + ".perfil_hora_fin_semana_pico")
put("d.picoweh", chk["perfil_hora_fin_semana_pico"][0], C + ".perfil_hora_fin_semana_pico")
put("d.v0000", chk["viajes_00_00_a_00_30_por_dia"], C + ".viajes_00_00_a_00_30_por_dia")
put("d.v0030", chk["viajes_00_30_a_05_00_por_dia"], C + ".viajes_00_30_a_05_00_por_dia", 1)
put("f.dias", chk["snapshots_dias"], C + ".snapshots_dias")
put("f.renglones", chk["snapshots_renglones"], C + ".snapshots_renglones")
put("f.commits", chk["commits_por_dia_media"], C + ".commits_por_dia_media", 0)
put("f.huecomed", chk["hueco_mediana_min"], C + ".hueco_mediana_min", 1)
put("f.huecop90", chk["hueco_p90_min"], C + ".hueco_p90_min", 1)
put("f.hueco1821", chk["hueco_mediana_18_21"], C + ".hueco_mediana_18_21", 1)
put("f.huecoresto", chk["hueco_mediana_resto"], C + ".hueco_mediana_resto", 1)
cz = chk["cierre"]
put("f.rentapre", 100 * cz["renta_00_00_00_30"], C + ".cierre.renta_00_00_00_30", 1, pct=True)
put("f.rentapost", 100 * cz["renta_00_30_01_00"], C + ".cierre.renta_00_30_01_00", 1, pct=True)
put("f.disppre", cz["disponibles_00_00_00_30"], C + ".cierre.disponibles_00_00_00_30")
put("f.disppost", cz["disponibles_00_30_01_00"], C + ".cierre.disponibles_00_30_01_00")
put("f.danpre", cz["danadas_00_00_00_30"], C + ".cierre.danadas_00_00_00_30")
put("f.danpost", cz["danadas_00_30_01_00"], C + ".cierre.danadas_00_30_01_00")

# ------------------------------------------------------------------ medición de Ecobici (rebalanceo_ecobici.json)
C = "report/figs/rebalanceo_ecobici.json"
for key, name in (("run1_manana", None),):
    for arm, lab in (("ecobici sin ±1", "r1sin"), ("ecobici (todo)", "r1todo"), ("ma", "r1ma"), ("oracle", "r1or")):
        d = reb["run1_manana"][arm]
        for q in ("p50", "p90", "p95", "p99", "max"):
            put(f"bpm.{lab}.{q}", d[q], C + f".run1_manana[{arm}].{q}")
        put(f"bpm.{lab}.n", d["n"], C + f".run1_manana[{arm}].n")
for src_k, lab in (("run2_dia_completo_principal_todos", "r2"), ("run2_dia_completo_principal_eval", "r2ev"),
                   ("run2_manana_principal_eval", "r2man"), ("run2_dia_completo_con_undo_todos", "r2undo")):
    d = reb[src_k]
    for q in ("p50", "p90", "p95", "p99", "max"):
        put(f"bpm.{lab}.{q}", d[q], C + f".{src_k}.{q}")
    put(f"bpm.{lab}.n", d["n"], C + f".{src_k}.n")
    put(f"bpm.{lab}.rank22", d["rango_percentil_de_22"], C + f".{src_k}.rango_percentil_de_22", 1)
    put(f"bpm.{lab}.rank14", d["rango_percentil_de_14"], C + f".{src_k}.rango_percentil_de_14", 1)
    put(f"bpm.{lab}.gt42", d["pct_mayor_42"], C + f".{src_k}.pct_mayor_42", 2, pct=True)
    put(f"bpm.{lab}.gt22", d["pct_mayor_22"], C + f".{src_k}.pct_mayor_22", 1, pct=True)
put("bpm.r1sin.rank22", reb["run1_manana"]["ecobici sin ±1"]["rango_percentil_de_22"], C + ".run1_manana[ecobici sin ±1].rango_percentil_de_22", 1)
put("bpm.r1sin.gt42", reb["run1_manana"]["ecobici sin ±1"]["pct_mayor_42"], C + ".run1_manana[ecobici sin ±1].pct_mayor_42", 2, pct=True)
put("m.dias", reb["n_dias_medidos"], C + ".n_dias_medidos")
th = reb["tope_hora"]
put("m.topehora", th["p95_ceil"], C + ".tope_hora.p95_ceil")
put("m.topehoramed", th["mediana"], C + ".tope_hora.mediana")
put("m.topehoramax", th["max"], C + ".tope_hora.max")
put("m.topehorap99", th["p99_ceil"], C + ".tope_hora.p99_ceil")
put("m.nvent", th["n_ventanas"], C + ".tope_hora.n_ventanas")
bo = reb["bodega"]
put("m.bodega", bo["tope_ceil"], C + ".bodega.tope_ceil")
put("m.bodegamean", bo["media_abs_A_menos_R"], C + ".bodega.media_abs_A_menos_R", 1)
put("m.bodegapos", bo["dias_positivos"], C + ".bodega.dias_positivos")
put("m.A", bo["A_media"], C + ".bodega.A_media")
put("m.R", bo["R_media"], C + ".bodega.R_media")
put("m.movs", bo["movs_por_dia_media"], C + ".bodega.movs_por_dia_media")
put("m.movsev", bo["movs_por_dia_media_eval"], C + ".bodega.movs_por_dia_media_eval")
put("m.taller", reb["taller_retiro_por_dia_media"], C + ".taller_retiro_por_dia_media")
put("m.undopct", reb["pares_undo_pct_intervalos"], C + ".pares_undo_pct_intervalos", 1, pct=True)
put("m.pctman", reb["pct_movs_05_30_12_30"], C + ".pct_movs_05_30_12_30", 0, pct=True)
put("m.pcttar", reb["pct_movs_12_30_18_30"], C + ".pct_movs_12_30_18_30", 0, pct=True)
put("m.pctnoc", reb["pct_movs_18_30_00_30"], C + ".pct_movs_18_30_00_30", 0, pct=True)
S2 = "ecosim/results/medicion/ecobici_stats.json"
put("m.danos", stats["mean_danos"], S2 + ".mean_danos")
put("m.rep", stats["mean_reparaciones"], S2 + ".mean_reparaciones")
put("m.stockAR", stats["mean_stock_warehouse"], S2 + ".mean_stock_warehouse", 1)
put("m.mantallerAR", stats["mean_manana_stock_warehouse"], S2 + ".mean_manana_stock_warehouse", 1)
put("m.mantaller", stats["mean_manana_taller_retiro"], S2 + ".mean_manana_taller_retiro", 1)
put("m.bodegarama", stats["tope_bodega_rama_delta_pos_reparacion"], S2 + ".tope_bodega_rama_delta_pos_reparacion")
put("m.bodegataller", stats["mean_abs_stock_warehouse"], S2 + ".mean_abs_stock_warehouse", 0)
put("m.movsundo", stats["mean_undo_moves"], S2 + ".mean_undo_moves")
put("m.intervalos", stats["moves_total"], S2 + ".moves_total")
put("m.eventos", stats["damage_events_total"], S2 + ".damage_events_total")
put("m.topehoraundo", stats["tope_hora_con_undo"], S2 + ".tope_hora_con_undo")


# ------------------------------------------------------------------ razones derivadas (texto)
def m(key_src):
    return float(str(N[key_src]).replace(",", "").replace("\\%", "").replace("$-$", "-"))
red = {a: 100 * (1 - m(f"L30.{a}") / m(f"arm.{a}.EF")) for a in ("oracle", "ma", "model")}
put("der.L30min", min(red.values()), "1 − L30/L60 por brazo (resultados.csv), mínimo", 0)
put("der.L30max", max(red.values()), "1 − L30/L60 por brazo (resultados.csv), máximo", 0)
put("der.dailymovs", 100 * (m("arm.daily.moves") / m("arm.eco.moves") - 1), "daily vs Ecobici movimientos", 0, pct=True)
put("der.dailybikes", 100 * (B["daily"]["bikes_moved"] / B["Ecobici"]["bikes_moved"] - 1), "daily vs Ecobici bicis movidas", 0, pct=True)
put("der.lamratio", c_l60 := [c for c in r2["curva_lambda"] if c["lam"] == 60][0]["EF"] / [c for c in r2["curva_lambda"] if c["lam"] == 5][0]["EF"], "E+F λ60 / λ5 (frozen.json.curva)", 1)
put("der.lammoves", [c for c in r2["curva_lambda"] if c["lam"] == 5][0]["moves"] / [c for c in r2["curva_lambda"] if c["lam"] == 60][0]["moves"], "movs λ5 / λ60 (frozen.json.curva)", 1)
adv = [100 * (1 - m(f"sens.diag_como_run1.{a}.manana") / m(f"arm.{a}.manana")) for a in ("oracle", "daily")]
put("der.run1min", min(adv), "1 − diag_como_run1.manana / eval.manana (oracle, daily), mínimo", 0)
put("der.run1max", max(adv), "1 − diag_como_run1.manana / eval.manana (oracle, daily), máximo", 0)
put("der.bodtaller", 100 * (m("sens.sens_bodega_taller.daily") / m("arm.daily.EF") - 1), "sens_bodega_taller.daily / eval.daily − 1", 0, pct=True)
tope = max(abs(m(f"sens.sens_tope_con_undo.{a}") / m(f"arm.{a}.EF") - 1) * 100 for a in ("oracle", "daily"))
put("der.tope474", tope, "máx |sens_tope_con_undo / eval − 1| (oracle, daily)", 1, pct=True)
put("der.lamselx", m("selEco.EF") / m("lam.30.EF"), "Ecobici selección / oracle λ30 (E+F)", 1)


# ------------------------------------------------------------------ sesgos del replay y tiempos de decisión
v1d = pd.read_csv(R / "v1" / "v1_por_dia.csv")
g = v1d.groupby("label").EF.mean()
put("rep.t1", g["ecobici_t1|auto"], "v1/v1_por_dia.csv [label=ecobici_t1|auto] media EF")
put("rep.undo", g["ecobici_undo|auto"], "v1/v1_por_dia.csv [label=ecobici_undo|auto] media EF")
put("rep.t1pct", 100 * (g["ecobici_t1|auto"] / g["ecobici|auto"] - 1), "v1_por_dia.csv t1 / t0 − 1", 0, pct=True)
put("rep.undopct", 100 * (1 - g["ecobici_undo|auto"] / g["ecobici|auto"]), "v1_por_dia.csv 1 − undo / principal", 0, pct=True)
evm = res[(res.split == "evaluacion") & (res.tag == "eval") & res.decision_s_max.notna()]
put("time.mainmax", evm.decision_s_max.max(), FR + " [tag=eval, políticas] máx decision_s_max", 1)
put("time.lam5eval", res[res.tag == "sens_lam_min_seleccion"].decision_s_max.max(), FR + " [tag=sens_lam_min_seleccion] máx decision_s_max", 0)
put("time.lam5sel", res[(res.split == "seleccion") & (res.lam == 5)].decision_s_max.max(), FR + " [split=seleccion, lam=5] máx decision_s_max", 0)
put("runs.total", len(res), FR + " renglones")

# ------------------------------------------------------------------ escribir
lines = ["% Generado por report/figs/numeros.py — no editar a mano.",
         "\\makeatletter",
         "\\newcommand{\\nr}[1]{\\ifcsname nr@#1\\endcsname\\csname nr@#1\\endcsname"
         "\\else\\errmessage{numero no definido: #1}\\fi}"]
for k, v in N.items():
    lines.append(f"\\expandafter\\def\\csname nr@{k}\\endcsname{{{v}}}")
lines.append("\\makeatother")
(REPORT / "numeros.tex").write_text("\n".join(lines) + "\n")
(FIGS / "numeros_fuentes.json").write_text(json.dumps(SRC, indent=1, ensure_ascii=False))


# ------------------------------------------------------------------ tablas LaTeX
def tex_table(rows, header, spec):
    out = [f"\\begin{{tabular}}{{{spec}}}", "\\toprule", " & ".join(header) + " \\\\", "\\midrule"]
    out += [" & ".join(r) + " \\\\" for r in rows]
    out += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(out) + "\n"


def k(x):
    return f"{x / 1000:.1f}"


rows = []
for lab, key in (("Sin rebalanceo", "no hacer nada"), ("Ecobici (replay)", "Ecobici"), ("oracle", "oracle"),
                 ("daily", "daily"), ("ma", "ma"), ("model", "model")):
    b = B[key]
    cfg = ""
    if key in ("oracle", "daily", "ma", "model"):
        cfg = (f"h={int(b['H'])}" if key in ("oracle", "daily") else f"f={int(b['f'])}, h={int(b['H'])}")
    vs = "" if key in ("no hacer nada", "Ecobici") else f"$-${-b['vs_ecobici_pct']:.0f}\\%"
    gana = "" if key in ("no hacer nada", "Ecobici") else f"{b['gana']}/15"
    rows.append([lab, cfg, k(b["EF"]), k(b["manana"]), k(b["tarde"]), k(b["noche"]), vs, gana,
                 f"{b['moves']:,.0f}", f"{b.get('bikes_moved', 0) / 1000:.1f}"])
(TAB / "brazos.tex").write_text(tex_table(
    rows, ["Brazo", "Config.", "E+F", "Mañ.", "Tarde", "Noche", "vs Eco.", "Gana", "Movs.", "Bicis"],
    "llrrrrrrrr"))

S = [("Principal", "eval", None), ("Tope por movimiento 14", "sens_mm14", None),
     ("Tope por movimiento 42", "sens_mm42", None), ("Sin tope por movimiento", "diag_mm_sin_tope", None),
     ("Tope por hora con $\\pm$1 (474)", "sens_tope_con_undo", None), ("Dañadas fijas", "sens_danadas_fijas", None),
     ("Bodega ilimitada", "sens_bodega_ilimitada", None), ("Bodega con taller (74)", "sens_bodega_taller", None),
     ("$\\mu = 0$", "sens_mu0", None), ("Cota de retiro $\\lfloor p\\rfloor$", "sens_retiro_floor", None),
     ("$\\lambda = 5$", "sens_lam_min_seleccion", None), ("Todo como el run 1", "diag_como_run1", None)]
rows = []
for lab, tag, _ in S:
    vals = []
    for arm in ("oracle", "daily"):
        g = res[(res.split == "evaluacion") & (res.tag == tag) & (res.arm == arm) & (res.damage.eq("auto") | tag.startswith(("sens_danadas", "diag_como")))]
        vals.append(g)
    o, d = vals
    rows.append([lab, k(o.EF.mean()), f"{o.moves.mean():,.0f}", k(d.EF.mean()), f"{d.moves.mean():,.0f}",
                 f"{int((d.set_index('day').EF < eco_day).sum())}/15"])
(TAB / "sensibilidades.tex").write_text(tex_table(
    rows, ["Cambio", "oracle E+F", "movs.", "daily E+F", "movs.", "daily gana"], "lrrrrr"))

rows = []
for lab, key, r1k, cfg1 in (("Sin rebalanceo", "no hacer nada", "baseline", ""),
                            ("Ecobici", "Ecobici", "ecobici", ""),
                            ("oracle", "oracle", "oracle", "H=3"), ("daily", "daily", "daily", "H=3, bloque 30"),
                            ("ma", "ma", "ma", "H=3"), ("model", "model", "model", "H=3")):
    rows.append([lab, k(RUN1[r1k]), cfg1, k(B[key]["manana"])])
(TAB / "run1.tex").write_text(tex_table(rows, ["Brazo", "Run 1", "Config. run 1", "Run 2 (mañana)"], "lrlr"))

rows = []
for lab, key in (("oracle", "oracle"), ("daily", "daily"), ("ma f=60", "ma")):
    d = frozen["h_busqueda"]["EF"].get(key)
    if d is None:
        continue
    rows.append([lab] + [k(d.get(str(h), np.nan)) for h in range(1, 7)])
hrows = []
for lab, key in (("oracle", "oracle"), ("\\texttt{ma} ($f$=60)", "ma")):
    d = frozen["h_busqueda"]["EF"][key]
    best = min(d, key=lambda h: d[h])
    hrows.append([lab] + [("\\textbf{%s}" if h == best else "%s") % k(d[h]) if h in d else "" for h in map(str, range(1, 7))])
(TAB / "hsel.tex").write_text(tex_table(hrows, ["$h$"] + [str(h) for h in range(1, 7)], "@{}lrrrrrr@{}"))
print(f"escritas {len(N)} macros en {REPORT / 'numeros.tex'} y tablas en {TAB}")
