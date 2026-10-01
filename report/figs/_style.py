"""Estilo común de las figuras del reporte final (parámetros fijados en el brief)."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams.update({
    "font.family": "Menlo",
    "figure.figsize": (12, 6),
    "figure.dpi": 100,
    "axes.titlesize": 32,
    "axes.labelsize": 26,
    "xtick.labelsize": 22,
    "ytick.labelsize": 22,
    "lines.linewidth": 2.5,
})

FIGS = Path(__file__).resolve().parent
REPO = FIGS.parent.parent
DATA = REPO / "data" / "derived" / "ecosim"
# Resultados finales del run 2 ya integrados en main. Se puede cambiar con la
# variable ECOSIM_RESULTS para auditar otro checkout o una reproducción aislada.
import os  # noqa: E402

RESULTS = Path(os.environ.get(
    "ECOSIM_RESULTS",
    REPO / "ecosim" / "results"))

C1, C2, C3, C4, GRAY = "#1f5f99", "#d1495b", "#edae49", "#00798c", "#8a8a8a"


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIGS / f"{name}.png")
    plt.close(fig)
    print("escrita", FIGS / f"{name}.png")
