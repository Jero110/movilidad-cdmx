"""Reglas observables de medición Ecobici run 3."""
import pandas as pd
import pytest

from ecosim import medicion as M, contracts as K

DAY = "2025-09-03"
T = lambda hm: pd.Timestamp(f"{DAY} {hm}")  # noqa: E731


def snaps(states, station="001"):
    return pd.DataFrame([{"short_name": station, "t": T(f"05:{i * 15:02d}"),
                          "bikes": b, "disabled": dis, "docks": 30 - b - dis, "blank": False}
                         for i, (b, dis) in enumerate(states)])


def trips(rows=()):
    return pd.DataFrame([{"o": o, "d": d, "t_dep": T(dep), "t_arr": T(arr)}
                         for o, d, dep, arr in rows], columns=["o", "d", "t_dep", "t_arr"])


@pytest.mark.parametrize("states,expected", [
    ([(5, 2), (4, 3)], 0),      # reportada en sitio
    ([(5, 2), (2, 2)], -3),     # camión toma tres disponibles
    ([(5, 2), (5, 1)], -1),     # camión toma la no rentable
])
def test_tres_casos_del_wiki(states, expected):
    iv = M.intervals(snaps(states), trips(), DAY)
    assert iv.delta.tolist() == [expected]
    assert M.flow_stats(M.moves_from_intervals(iv))["neto"] == expected
    K.validate_ecobici_moves(M.moves_from_intervals(iv))
    ev = M.damage_events(iv)
    assert ev["kind"].tolist() == (["sube"] if states[1][1] > states[0][1] else
                                   ["baja"] if states[1][1] < states[0][1] else [])
    assert (ev["t"] == T("05:15")).all()
    K.validate_damage_events(ev)


def test_ejemplo_con_viajes_del_wiki():
    # 8 + 2 → 4 + 3; 5 llegadas y 6 salidas.
    tr = trips([("999", "001", "05:01", "05:05")] * 5 +
               [("001", "999", "05:02", "05:06")] * 6)
    iv = M.intervals(snaps([(8, 2), (4, 3)]), tr, DAY)
    assert (iv.arrivals.iloc[0], iv.departures.iloc[0], iv.delta.iloc[0]) == (5, 6, -2)


@pytest.mark.parametrize("size", [1, 2, 3])
def test_pares_de_cualquier_tamano_y_neto(size):
    iv = M.intervals(snaps([(10, 2), (10 + size, 2), (10, 2)]), trips(), DAY)
    assert iv.delta.tolist() == [size, -size]
    assert iv.par_start.tolist() == [True, False]
    assert iv.par.tolist() == [True, True]
    assert M.main_moves(M.moves_from_intervals(iv)).empty
    assert M.flow_stats(M.moves_from_intervals(iv))["neto"] == 0


def test_cadena_impar_sin_traslape_y_estaciones_aisladas():
    iv = M.intervals(snaps([(10, 0), (12, 0), (10, 0), (12, 0)]), trips(), DAY)
    assert iv.par.tolist() == [True, True, False]
    assert iv.par_start.tolist() == [True, False, False]
    assert M.main_moves(M.moves_from_intervals(iv)).delta.tolist() == [2]
    assert M.impact_table(iv.assign(day=DAY), 1).set_index("regla").loc["cualquier tamaño", "neto"] == 2
    other = snaps([(10, 0), (8, 0)], "002")
    mix = M.intervals(pd.concat([snaps([(10, 0), (12, 0)]), other]), trips(), DAY)
    assert not mix.par.any()


def test_impacto_no_empareja_dias_distintos():
    a = M.intervals(snaps([(5, 0), (6, 0)]), trips(), DAY).assign(day=DAY)
    b = M.intervals(snaps([(6, 0), (5, 0)]), trips(), DAY).assign(day="2025-09-04")
    both = pd.concat([a, b], ignore_index=True)
    assert M.impact_table(both, 2).set_index("regla").loc["cualquier tamaño", "visitas"] == 1


def test_blanco_y_dano_no_generan_movimientos_falsos():
    s = snaps([(5, 2), (0, 0), (4, 3), (5, 2)])
    s.loc[1, "blank"] = True
    iv = M.intervals(s, trips(), DAY)
    assert iv.blank_touch.tolist() == [True, True, False]
    assert M.moves_from_intervals(iv).empty
    assert M.damage_events(iv)[["kind", "n"]].values.tolist() == [["baja", 1]]


def test_clasificacion_de_danadas_y_evidencia_de_viaje_cercano():
    x = M.intervals(snaps([(10, 1), (10, 2), (10, 1)]), trips(), DAY).assign(day=DAY)
    classification = M.damage_classification(x)
    assert classification.set_index(["tipo", "total"])["bicis"].to_dict() == {
        ("sube", "sube"): 1, ("baja", "baja"): 1}
    tr = trips([("001", "999", "05:14:50", "05:25")]).assign(bike="B1")
    y = M.intervals(snaps([(10, 0), (10, 0), (9, 0)]), tr, DAY).assign(day=DAY)
    rates, close, examples = M.pair_evidence(y, tr)
    assert rates["pares"].sum() == 1
    assert M.pair_rates_by_size(y).query('tamaño == 1')["pares"].sum() == 1
    assert len(examples) == 1 and examples[0]["trips"][0]["bike"] == "B1"
    assert close.iloc[0]["viaje_30s_%"] == 100


def test_cap_usa_duracion_y_fotos_sin_visitas():
    s = snaps([(5, 0), (7, 0), (7, 0), (7, 0)])
    iv = M.intervals(s, trips(), DAY).assign(day=DAY)
    caps = M.visit_caps(iv)
    assert caps["fotos"] == 3
    assert caps["bicis_p95"] == 2
    assert caps["visitas_p95"] == 1


def test_ef_observado_incluye_0500_y_no_cierre():
    s = snaps([(0, 0), (30, 0), (5, 0)])
    ef = M.observed_ef(s, DAY, 0, 45).iloc[0]
    assert ef.E == pytest.approx(15)
    assert ef.F == pytest.approx(15)


def test_evento_en_foto_pertenece_al_intervalo_que_termina_ahi():
    iv = M.intervals(snaps([(5, 0), (4, 0), (4, 0)]),
                     trips([("001", "999", "05:15", "05:20")]), DAY)
    assert iv.departures.tolist() == [1, 0]
    assert iv.delta.tolist() == [0, 0]
