"""Reconstruct real bike relocations for one day from bike-ID trip data.

A bike has a unique ID (`Bici`) in the monthly trip CSVs. If a bike ends a
trip at station A and its *next* trip (by any user) starts at station B != A,
with no trip in between, nobody pedaled it there -- something moved it. This
is a direct reconstruction from the same trip data used for flows, not a
simulation: it recovers the actual origin/destination pair per relocated
bike, which the aggregate departures/arrivals counts throw away.

No time-of-day window is assumed. Earlier versions of this script only kept
jumps inside an 18:00-00:35 -> 05:00-10:00 window, assuming relocation is an
overnight-only phenomenon. Measured against the full dataset (see
wiki/Rebalanceo-Ecobici-suposicion-horario.md), that assumption doesn't hold:
70.6% of all detected jumps land the *same calendar day* they were dropped
off (median gap 2.3h, peaking around 8-9am drop-offs), and only 26.1% cross
into the next calendar day with the gap-9-10h/night-drop-off-morning-pickup
shape that overnight relocation would actually look like. Every jump is kept
and labeled `days_crossed` (0 = same day, 1 = next day, 2+ = longer gap) so
the app can show both instead of assuming one.

Usage: uv run python3 -m analysis.rebalance_routes 2025-09-17
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
JUMP_DISTANCE_FLOOR_M = 300  # below this, treat as name-collision noise, not a real move


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def compute_moves(month_csvs: list[Path], day: str) -> pd.DataFrame:
    """One row per relocated bike touching `day`: dropped off on `day` (picked
    up later that same day or the next), OR dropped off the day before and
    picked up on `day` (the overnight tail feeding into this day's morning).
    Without the day-before half, every jump whose drop-off/pickup straddles
    midnight into `day` would be invisible when looking at `day` alone. No
    hour-of-day filter -- see module docstring for why.

    `month_csvs` takes 1-2 files because the day before `day` can fall in the
    previous calendar month (e.g. day=2025-10-01)."""
    df = pd.concat([pd.read_csv(f) for f in month_csvs], ignore_index=True)
    df["arr_dt"] = pd.to_datetime(
        df["Fecha_Arribo"] + " " + df["Hora_Arribo"], format="%d/%m/%Y %H:%M:%S", errors="coerce"
    )
    df["ret_dt"] = pd.to_datetime(
        df["Fecha_Retiro"] + " " + df["Hora_Retiro"], format="%d/%m/%Y %H:%M:%S", errors="coerce"
    )
    df = df.dropna(subset=["arr_dt", "ret_dt"]).sort_values(["Bici", "ret_dt"]).reset_index(drop=True)

    df["next_ret_station"] = df.groupby("Bici")["Ciclo_Estacion_Retiro"].shift(-1)
    df["next_ret_dt"] = df.groupby("Bici")["ret_dt"].shift(-1)

    jumps = df[
        df["next_ret_station"].notna() & (df["next_ret_station"] != df["Ciclo_EstacionArribo"])
    ].copy()
    jumps = jumps.dropna(subset=["next_ret_dt"])

    day_date = pd.Timestamp(day).date()
    prev_date = day_date - pd.Timedelta(days=1)
    # Toca `day` en cualquiera de las dos puntas: se dejo ese dia (aunque se
    # recogiera despues), o se dejo la vispera y se recogio justo ese dia
    # (la cola overnight que entra a la manana de `day`). Un salto dejado Y
    # recogido integramente el dia anterior no cuenta como parte de `day`.
    touches_day = (jumps["arr_dt"].dt.date == day_date) | (
        (jumps["arr_dt"].dt.date == prev_date) & (jumps["next_ret_dt"].dt.date == day_date)
    )
    day_jumps = jumps[touches_day].copy()

    coords = pd.read_csv(ROOT / "analysis" / "station_coords.csv")[["short_name", "lat", "lon"]]
    day_jumps = (
        day_jumps.merge(coords, left_on="Ciclo_EstacionArribo", right_on="short_name", how="left")
        .rename(columns={"lat": "lat_from", "lon": "lon_from"})
        .drop(columns="short_name")
    )
    day_jumps = (
        day_jumps.merge(coords, left_on="next_ret_station", right_on="short_name", how="left")
        .rename(columns={"lat": "lat_to", "lon": "lon_to"})
        .drop(columns="short_name")
    )
    day_jumps = day_jumps.dropna(subset=["lat_from", "lon_from", "lat_to", "lon_to"])

    day_jumps["dist_m"] = haversine_m(
        day_jumps.lat_from, day_jumps.lon_from, day_jumps.lat_to, day_jumps.lon_to
    )
    real_moves = day_jumps[day_jumps.dist_m > JUMP_DISTANCE_FLOOR_M].copy()
    real_moves["days_crossed"] = (
        real_moves["next_ret_dt"].dt.date - real_moves["arr_dt"].dt.date
    ).apply(lambda d: d.days)

    return real_moves.rename(
        columns={"Ciclo_EstacionArribo": "from_station", "next_ret_station": "to_station"}
    )[
        [
            "Bici",
            "from_station",
            "to_station",
            "arr_dt",
            "next_ret_dt",
            "days_crossed",
            "lat_from",
            "lon_from",
            "lat_to",
            "lon_to",
            "dist_m",
        ]
    ].sort_values(["from_station", "arr_dt"])


def aggregate_routes(moves: pd.DataFrame) -> pd.DataFrame:
    """Un renglon por (origen, destino, days_crossed): agregar por
    days_crossed en vez de mezclarlo es lo que le permite a la tab dibujar
    por separado los saltos overnight (days_crossed=1, la cola que entra
    desde la vispera) de los del mismo dia calendario (days_crossed=0), en
    vez de forzar una sola categoria como hacia la version anterior."""
    return (
        moves.groupby(["from_station", "to_station", "days_crossed"])
        .agg(
            n_bikes=("Bici", "count"),
            lat_from=("lat_from", "first"),
            lon_from=("lon_from", "first"),
            lat_to=("lat_to", "first"),
            lon_to=("lon_to", "first"),
            dist_m=("dist_m", "first"),
        )
        .reset_index()
        .sort_values("n_bikes", ascending=False)
    )


def add_station_occupancy(moves: pd.DataFrame, day: str) -> pd.DataFrame:
    """Bikes available at the start (00:00) and end (23:45) of `day` per
    station, for context -- not an overnight-close/open assumption."""
    occ = pd.read_parquet(
        ROOT / "data" / "derived" / "occupancy_15min.parquet",
        columns=["short_name", "bucket", "num_bikes_available", "capacity", "snapshot_stale"],
    )
    start_bucket = pd.Timestamp(day)
    end_bucket = pd.Timestamp(day) + pd.Timedelta(hours=23, minutes=45)

    start = occ[(occ.bucket == start_bucket) & (~occ.snapshot_stale)].set_index("short_name")
    end = occ[(occ.bucket == end_bucket) & (~occ.snapshot_stale)].set_index("short_name")

    moves = moves.copy()
    moves["from_bikes_at_close"] = moves["from_station"].map(end["num_bikes_available"])
    moves["from_capacity"] = moves["from_station"].map(end["capacity"])
    moves["to_bikes_at_open"] = moves["to_station"].map(start["num_bikes_available"])
    moves["to_capacity"] = moves["to_station"].map(start["capacity"])
    return moves


def _month_csv(month: str) -> Path | None:
    path = ROOT / "data" / "ecobici" / f"{month}.csv"
    return path if path.exists() else None


def main():
    day = sys.argv[1]
    day_date = pd.Timestamp(day).date()
    prev_month = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime("%Y-%m")
    this_month = day_date.strftime("%Y-%m")

    month_csvs = [p for p in {_month_csv(prev_month), _month_csv(this_month)} if p]
    if not month_csvs:
        raise FileNotFoundError(f"No monthly CSV found for {day} (looked for {prev_month}, {this_month})")

    moves = compute_moves(month_csvs, day)
    moves = add_station_occupancy(moves, day)
    routes = aggregate_routes(moves)

    moves_path = ROOT / "data" / "derived" / f"rebalance_moves_{day}.parquet"
    routes_path = ROOT / "data" / "derived" / f"rebalance_routes_{day}.parquet"
    moves.to_parquet(moves_path, index=False)
    routes.to_parquet(routes_path, index=False)
    same_day = (moves["days_crossed"] == 0).sum()
    next_day = (moves["days_crossed"] == 1).sum()
    print(
        f"{len(moves)} bikes ({same_day} same-day, {next_day} overnight-into-{day}), "
        f"{len(routes)} routes -> {moves_path.name}, {routes_path.name}"
    )


if __name__ == "__main__":
    main()
