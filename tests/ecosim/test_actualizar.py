"""Datos y modelos de producción: descarga del mes nuevo, esquema, parquet con
toda la data y manifiesto. No toca los artefactos del run 3."""
import json
from datetime import date

import pytest

from ecosim import actualizar as A, config as C

HEADER = "Genero_Usuario,Edad_Usuario,Bici,Ciclo_Estacion_Retiro,Fecha_Retiro,Hora_Retiro,Ciclo_EstacionArribo,Fecha_Arribo,Hora_Arribo\n"
HTML = """<a href="/wp-content/uploads/2024/02/ecobici_2024_enero.csv">x</a>
<a href="/wp-content/uploads/2026/09/public_data_web_2026-08_2.csv">x</a>
<a href="/wp-content/uploads/2026/10/public_data_web_2026-09.csv">x</a>"""


def test_mes_de_archivo():
    assert A.mes_de_archivo("ecobici_2024_enero.csv") == "2024-01"
    assert A.mes_de_archivo("datos_abiertos_2024_03-1-1.csv") == "2024-03"
    assert A.mes_de_archivo("2025-10-1.csv") == "2025-10"
    assert A.mes_de_archivo("/wp-content/uploads/2026/10/public_data_web_2026-09.csv") == "2026-09"
    assert A.mes_de_archivo("leeme.csv") is None


def test_enlaces_publicados():
    links = A.enlaces_publicados(HTML)
    assert max(links) == "2026-09"
    assert links["2026-09"] == "https://ecobici.cdmx.gob.mx/wp-content/uploads/2026/10/public_data_web_2026-09.csv"


def test_baja_solo_si_hay_mes_nuevo(tmp_path):
    (tmp_path / "public_data_web_2026-08_2.csv").write_text(HEADER)
    pedidos = []

    def get(url, timeout=60):
        pedidos.append(url)
        return HTML.encode() if url == A.URL_DATOS else HEADER.encode()
    r = A.bajar_mes_nuevo(tmp_path, get=get)
    assert r["estado"] == "descargado" and r["archivo"] == "public_data_web_2026-09.csv"
    assert (tmp_path / "public_data_web_2026-09.csv").read_text() == HEADER
    r = A.bajar_mes_nuevo(tmp_path, get=get)
    assert r["estado"] == "al_dia" and len(pedidos) == 3


def test_sin_red_usa_los_csv_locales(tmp_path):
    (tmp_path / "2026-09.csv").write_text(HEADER)

    def get(url, timeout=60):
        raise OSError("sin red")
    r = A.bajar_mes_nuevo(tmp_path, get=get)
    assert r["estado"] == "sin_red" and r["ultimo_local"] == "2026-09"


def test_esquema_incompatible_falla(tmp_path):
    (tmp_path / "2026-08.csv").write_text(HEADER)
    (tmp_path / "2026-09.csv").write_text(HEADER.replace("Ciclo_EstacionArribo", "Estacion_Arribo"))
    with pytest.raises(ValueError, match="esquema incompatible"):
        A.auditar_esquema(A.csv_locales(tmp_path))
    (tmp_path / "2026-09.csv").write_text(HEADER)
    out = A.auditar_esquema(A.csv_locales(tmp_path))
    assert out["ultimo"] == "2026-09" and out["ultimo_igual_al_previo"] is True


def test_rutas_produccion_se_restauran(tmp_path):
    antes = (C.RAW_TRIP_FILES, C.TRIPS_PARQUET, C.TRIPS_AUDIT, C.RAW_TRIPS_DIR)
    with A.rutas_produccion({"2026-09": tmp_path / "2026-09.csv"}, tmp_path / "v.parquet", tmp_path / "a.json"):
        assert C.TRIPS_PARQUET == tmp_path / "v.parquet" and C.RAW_TRIP_FILES == ["2026-09.csv"]
    assert (C.RAW_TRIP_FILES, C.TRIPS_PARQUET, C.TRIPS_AUDIT, C.RAW_TRIPS_DIR) == antes
    assert C.TRIPS_PARQUET.name == "trips_2024_01_2026_08.parquet"


def test_actualizar_de_punta_a_punta_con_csv_chicos(tmp_path):
    raw = tmp_path / "ecobici"
    raw.mkdir()
    filas = {"2025-01": ["M,30,b1,001,31/01/2025,08:00:00,002,31/01/2025,08:10:00",
                         "F,25,b2,002,15/01/2025,09:00:00,001,15/01/2025,09:20:00"],
             "2025-02": ["M,30,b1,002,10/02/2025,07:00:00,001,10/02/2025,07:30:00",
                         "M,40,b3,001,28/02/2025,18:00:00,002,28/02/2025,18:05:00"]}
    for mes, rows in filas.items():
        # Como en los CSV reales: un mes con "Fecha Arribo" (2024) y otro con "Fecha_Arribo".
        head = HEADER.replace("Fecha_Arribo", '"Fecha Arribo"') if mes == "2025-01" else HEADER
        (raw / f"{mes}.csv").write_text(head + "\n".join(rows) + "\n")
    prod = tmp_path / "produccion"
    man = A.actualizar(sin_red=True, carpeta=raw, prod_dir=prod, entrenar=False)
    assert man["ultimo_dia_publicado"] == "2025-02-28"
    assert man["viajes"]["filas"] == 4 and man["meses"] == ["2025-01", "2025-02"]
    assert (prod / "viajes.parquet").exists() and (prod / "conteos.npz").exists()
    days, universe, counts = A.leer_conteos(prod / "conteos.npz")
    assert universe == ["001", "002"] and days[0] == date(2024, 1, 1) and days[-1] == date(2025, 2, 28)
    assert counts.sum() == 8  # 4 salidas y 4 llegadas
    assert C.TRIPS_PARQUET.name == "trips_2024_01_2026_08.parquet"
    # Sin meses nuevos no reconstruye (sin modelos se reconstruye: aquí no se piden).
    again = A.actualizar(sin_red=True, carpeta=raw, prod_dir=prod, entrenar=False)
    assert again["archivos"] == man["archivos"]


def test_manifiesto_de_produccion():
    if not A.MANIFIESTO.exists():
        pytest.skip("sin datos de producción: corre `uv run python -m ecosim.actualizar`")
    man = json.loads(A.MANIFIESTO.read_text())
    locales = sorted(A.csv_locales())
    assert man["ultimo_dia_publicado"] == f"{locales[-1]}-{A.ultimo_mes_dia(locales[-1]).day:02d}"
    assert man["meses"] == locales and man["entrenamiento"]["meses"] == locales
    assert man["entrenamiento"]["train_start"] == "2024-01-01"
    assert man["entrenamiento"]["train_end"] == man["ultimo_dia_publicado"]
    assert set(man["modelos"]) == {"lgbm_diario", "lgbm_directo"}
    for rel in man["modelos"].values():
        assert (A.PROD_DIR / rel).exists()
        assert "modelos/" in rel and not rel.startswith("..")
    assert man["viajes"]["filas"] > 50_000_000
