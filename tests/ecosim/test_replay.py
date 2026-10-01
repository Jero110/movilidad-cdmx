"""El replay usa el mismo motor que los resultados: E+F final = resultados.csv
(tolerancia 0). Lee frozen.json/resultados.csv de `replay.results_dir()`
(ECOSIM_RESULTS_DIR o ecosim/results)."""

from __future__ import annotations

import json

import pytest

from ecosim import replay
from ecosim.days import load_days


@pytest.fixture(scope="module")
def fz():
    try:
        return replay.load_frozen()
    except (FileNotFoundError, ValueError) as e:
        pytest.skip(f"sin frozen.json del run 2 completo en {replay.results_dir()}: {e}")


@pytest.fixture(scope="module")
def day():
    return load_days("evaluacion")[0]


@pytest.fixture(scope="module")
def real(fz, day):
    return replay.replay_day_arm(day, fz["best_real"], fz, write=False)


@pytest.mark.parametrize("arm", ["best_real", "baseline", "ecobici"])
def test_ef_final_igual_a_resultados(fz, day, real, arm):
    arm = fz["best_real"] if arm == "best_real" else arm
    out = real if arm == fz["best_real"] else replay.replay_day_arm(day, arm, fz, write=False)
    row = replay.resultados_row(replay.spec(fz, arm, day))
    assert row is not None, f"sin renglón de ({day}, {arm}) en resultados.csv"
    assert out["final"]["EF"] == int(row["EF"])
    assert out["final"]["E"] == int(row["E"]) and out["final"]["F"] == int(row["F"])
    assert out["final"]["moves"] == int(row["moves"])
    assert out["check"]["igual"] is True


def test_frames_consistentes(fz, real):
    n = replay.N_FRAMES
    assert len(real["bikes"]) == n and len(real["decision"]) == n and len(real["applied"]) == n
    cum = real["cum"]
    assert cum["E"][0] == 0 and cum["E"][-1] + cum["F"][-1] == real["final"]["EF"]
    assert all(a <= b for a, b in zip(cum["moves"], cum["moves"][1:]))
    assert cum["moves"][-1] == real["final"]["moves"]
    applied = sum(1 for fr in real["applied"] for a in fr if a[2] != 0)
    assert applied == real["final"]["moves"]
    # cada decisión: sus órdenes se hacen efectivas en t + L (paso k + L/15)
    L = real["spec"]["L"]
    for k, dec in enumerate(real["decision"]):
        if dec is None:
            continue
        issued = sorted((i, d) for i, d in dec["orders"])
        eff = sorted((a[0], a[1]) for a in real["applied"][k + L // replay.STEP] if a[7] == k)
        assert issued == eff
        if dec["plan"]:
            assert dec["plan"]["moves_avail"] <= real["spec"]["tope_hora"]
    # no hay decisiones con t + L ≥ 00:30
    last = max(k for k, d in enumerate(real["decision"]) if d is not None)
    assert 15 * last + L < 1140


def test_archivos_precalculados_coinciden(fz):
    """Si ya se precalculó (`python -m ecosim.replay`) con este frozen.json,
    cada archivo reproduce su renglón de resultados.csv."""
    idx = replay.OUT_DIR / "index.json"
    if not idx.exists():
        pytest.skip("replay no precalculado")
    ix = json.loads(idx.read_text())
    if ix["frozen_sha"] != replay.frozen_sha():
        pytest.skip("el replay precalculado es de otro frozen.json")
    n = 0
    for d, info in ix["days"].items():
        for arm, a in info["arms"].items():
            if a["check"]["EF_resultados"] is not None:
                assert a["check"]["igual"] is True, (d, arm)
                assert a["final"]["EF"] == a["check"]["EF_resultados"]
                assert a["cum"]["EF"][-1] == a["final"]["EF"]
                n += 1
    assert n > 0
