"""Genera models/predictions_fixture.parquet con el esquema del modelo real.

El modelo de forecast lo entrena otro worker en paralelo. Este fixture tiene
exactamente el esquema acordado para que /forecast/{short_name} funcione hoy
y recoja el archivo real (models/predictions.parquet) sin tocar codigo cuando
llegue.

    uv run python3 make_fixture.py
"""

from __future__ import annotations

import json
import math
import random
import urllib.request
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
OUT = HERE.parent.parent / "models" / "predictions_fixture.parquet"

HORIZONS_MIN = [15 * i for i in range(1, 17)]  # 15, 30, ..., 240

# Se usa si no hay red: cubre simples y compuestos para ejercitar
# short_name_keys() tambien del lado del forecast.
FALLBACK_SHORT_NAMES = [
    "001", "033", "100", "176", "264-275", "268-269", "390-391", "500",
]


def fetch_short_names() -> list[str]:
    """Todos los short_name reales del feed, para que el fixture cubra las
    677 estaciones y no solo un puñado. Si no hay red, usa la lista fija."""
    try:
        req = urllib.request.Request(
            "https://gbfs.mex.lyftbikes.com/gbfs/es/station_information.json",
            headers={"User-Agent": "movilidad-cdmx/mapa-ecobici"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        names = [s.get("short_name", "") for s in data["data"]["stations"]]
        names = sorted({n for n in names if n})
        if names:
            return names
    except Exception as exc:
        print(f"Sin red o feed caido ({exc}); uso lista fija de short_names.")
    return FALLBACK_SHORT_NAMES


def synth_row(short_name: str, horizon_min: int, rng: random.Random) -> dict:
    # Curva diurna simple: mas salidas en la manana, mas llegadas en la tarde,
    # con ruido. Sirve solo para ejercitar la forma de los datos, no es un
    # modelo real.
    h = horizon_min / 60.0
    dep_base = 1.2 + 0.8 * math.sin(h / 4 * math.pi)
    arr_base = 1.0 + 0.8 * math.cos(h / 4 * math.pi)

    dep_p50 = max(0.0, dep_base + rng.uniform(-0.3, 0.3))
    arr_p50 = max(0.0, arr_base + rng.uniform(-0.3, 0.3))

    dep_spread = 0.6 + 0.15 * (horizon_min / 15)
    arr_spread = 0.6 + 0.15 * (horizon_min / 15)

    return {
        "short_name": short_name,
        "origin_bucket": "weekday_am" if h < 12 else "weekday_pm",
        "horizon_min": horizon_min,
        "dep_p10": round(max(0.0, dep_p50 - dep_spread), 3),
        "dep_p50": round(dep_p50, 3),
        "dep_p90": round(dep_p50 + dep_spread, 3),
        "arr_p10": round(max(0.0, arr_p50 - arr_spread), 3),
        "arr_p50": round(arr_p50, 3),
        "arr_p90": round(arr_p50 + arr_spread, 3),
    }


def main() -> None:
    rng = random.Random(42)
    short_names = fetch_short_names()
    rows = [
        synth_row(sn, h, rng)
        for sn in short_names
        for h in HORIZONS_MIN
    ]
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"Escrito {OUT} ({len(df)} filas, {df['short_name'].nunique()} estaciones)")


if __name__ == "__main__":
    main()
