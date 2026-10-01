"""Descarga dos snapshots diarios de disponibilidad Ecobici (05:30 y 12:30
hora CDMX) desde el historico publico de terceros y los acumula en
data/derived/daily_snapshots.parquet.

Fuente: MaxHalford/bike-sharing-history, bucket GCS publico, sin
credenciales. Ver wiki/Pruebas-MVP-fuentes-datos.md seccion 4.

    uv run python3 scripts/fetch_daily_snapshots.py                # todo el rango disponible
    uv run python3 scripts/fetch_daily_snapshots.py --since 2026-01-01
    uv run python3 scripts/fetch_daily_snapshots.py --since 2026-09-01 --until 2026-09-20
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

BUCKET_GLOB = "s3://bike-sharing-history/mexico-city/ecobici/*/*.parquet"
SLOTS = {"05:30": "05:30:00", "12:30": "12:30:00"}

HERE = Path(__file__).parent
OUT_PATH = HERE.parent / "data" / "derived" / "daily_snapshots.parquet"

COLUMNS = [
    "date",
    "snapshot_slot",
    "station_id",
    "short_name",
    "name",
    "latitude",
    "longitude",
    "capacity",
    "num_bikes_available",
    "num_bikes_disabled",
    "num_docks_available",
    "num_docks_disabled",
    "is_installed",
    "is_renting",
    "is_returning",
    "committed_at_utc",
]


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("SET s3_endpoint='storage.googleapis.com'")
    con.execute("SET s3_region='auto'")
    return con


def fetch_slots(
    con: duckdb.DuckDBPyConnection, since: str | None, until: str | None
) -> pd.DataFrame:
    """Para cada dia y cada slot (05:30/12:30), toma el snapshot real mas
    cercano a esa hora local. El collector no publica exactamente a esa hora
    (cadencia ~15min, con huecos), asi que "mas cercano" es la unica
    definicion honesta de "la snapshot de las 5:30"."""
    date_filter = []
    params: list[str] = []
    if since:
        date_filter.append("committed_at_utc::date >= ?")
        params.append(since)
    if until:
        date_filter.append("committed_at_utc::date <= ?")
        params.append(until)
    where = f"WHERE {' AND '.join(date_filter)}" if date_filter else ""

    frames = []
    for slot_label, slot_time in SLOTS.items():
        query = f"""
            SELECT * FROM (
                SELECT
                    committed_at_utc::date AS date,
                    '{slot_label}' AS snapshot_slot,
                    station_id, short_name, name, latitude, longitude,
                    capacity, num_bikes_available, num_bikes_disabled,
                    num_docks_available, num_docks_disabled,
                    is_installed, is_renting, is_returning, committed_at_utc,
                    ROW_NUMBER() OVER (
                        PARTITION BY committed_at_utc::date, station_id
                        ORDER BY ABS(EPOCH(committed_at_utc) - EPOCH(
                            (committed_at_utc::date + TIME '{slot_time}')::timestamptz
                        ))
                    ) AS rn
                FROM READ_PARQUET('{BUCKET_GLOB}')
                {where}
            )
            WHERE rn = 1
        """
        df = con.execute(query, params).fetch_df()
        df = df.drop(columns=["rn"], errors="ignore")
        frames.append(df)

    return pd.concat(frames, ignore_index=True)[COLUMNS]


def merge_and_save(new_rows: pd.DataFrame) -> pd.DataFrame:
    """Upsert por (date, snapshot_slot, station_id): reemplaza filas
    existentes para las fechas/slots que se acaban de descargar, conserva el
    resto. Permite re-correr el script sin duplicar ni perder historia ya
    guardada."""
    key = ["date", "snapshot_slot", "station_id"]

    if OUT_PATH.exists():
        existing = pd.read_parquet(OUT_PATH)
        touched = new_rows[["date", "snapshot_slot"]].drop_duplicates()
        keep_mask = ~existing.set_index(["date", "snapshot_slot"]).index.isin(
            touched.set_index(["date", "snapshot_slot"]).index
        )
        existing = existing[keep_mask]
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows

    combined = combined.sort_values(["date", "snapshot_slot", "station_id"]).reset_index(
        drop=True
    )
    assert not combined.duplicated(key).any(), "upsert dejo duplicados"

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(OUT_PATH, index=False)
    return combined


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="YYYY-MM-DD, inclusive (default: todo el historico)")
    parser.add_argument("--until", help="YYYY-MM-DD, inclusive (default: hasta hoy)")
    args = parser.parse_args()

    con = connect()
    print(f"Descargando snapshots {list(SLOTS)} desde {BUCKET_GLOB} ...")
    new_rows = fetch_slots(con, args.since, args.until)
    print(f"{len(new_rows)} filas nuevas ({new_rows['date'].nunique()} dias).")

    combined = merge_and_save(new_rows)
    print(
        f"Guardado {OUT_PATH.relative_to(HERE.parent)}: "
        f"{len(combined)} filas totales, {combined['date'].nunique()} dias, "
        f"rango {combined['date'].min()} a {combined['date'].max()}."
    )


if __name__ == "__main__":
    main()
