"""Datos y modelos de producción para la Predicción en vivo.

    uv run python -m ecosim.actualizar            # baja el mes nuevo si hay y reentrena
    uv run python -m ecosim.actualizar --sin-red  # solo usa los CSV de data/ecobici/

Pasos, en orden:

1. Lee la página de datos abiertos de Ecobici y, si publica un mes más nuevo
   que el último CSV en `data/ecobici/`, lo baja ahí. Si la página o la
   descarga fallan, lo dice y sigue con los CSV locales (el usuario los deja a
   mano cada mes).
2. Audita que el esquema de cada CSV (en especial el mes nuevo) traiga las
   columnas que usa `ecosim.data.build_trips`.
3. Arma `produccion/viajes.parquet` con **todos** los CSV mensuales llamando a
   `ecosim.data.build_trips` (misma limpieza y auditoría que el run 3), con las
   rutas redirigidas solo dentro de este proceso. No toca el parquet del run 3.
4. Cuenta salidas y llegadas por estación y cuarto de hora con
   `ecosim.pronostico.load_counts` y entrena `lgbm_diario` y `lgbm_directo`
   con `ecosim.pronostico.train` (mismas variables y parámetros del run 3) con
   todos los días disponibles.
5. Escribe `produccion/manifiesto.json`: último día publicado, rango de
   entrenamiento, fecha de actualización, filas y archivos.

Los modelos del run 3 (`modelos/prueba_*`, `modelos/prod_2026_*`) y
`trips_2024_01_2026_08.parquet` no se leen ni se escriben aquí.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from ecosim import config as C

URL_DATOS = "https://ecobici.cdmx.gob.mx/datos-abiertos/"
PROD_DIR = C.DERIVED / "produccion"
VIAJES = PROD_DIR / "viajes.parquet"
AUDITORIA = PROD_DIR / "viajes_auditoria.json"
CONTEOS = PROD_DIR / "conteos.npz"
MODELOS_DIR = PROD_DIR / "modelos"
MANIFIESTO = PROD_DIR / "manifiesto.json"
INICIO = date(2024, 1, 1)
# Columnas que lee `ecosim.data.build_trips`; la fecha de arribo viene con
# guion bajo o con espacio según el mes.
COLUMNAS = ("Bici", "Ciclo_Estacion_Retiro", "Ciclo_EstacionArribo", "Fecha_Retiro",
            "Hora_Retiro", "Hora_Arribo")
COLUMNA_ARRIBO = ("Fecha_Arribo", "Fecha Arribo")
MESES = {m: i for i, m in enumerate(("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                                     "agosto", "septiembre", "octubre", "noviembre", "diciembre"), 1)}
MES_RE = re.compile(r"(20\d\d)[-_](\d\d)")


def mes_de_archivo(nombre: str) -> str | None:
    """'2025-10-1.csv' → '2025-10'; 'ecobici_2024_enero.csv' → '2024-01'."""
    base = Path(urllib.parse.urlparse(nombre).path).name.lower()
    m = MES_RE.search(base)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{m.group(2)}"
    anio = re.search(r"20\d\d", base)
    for palabra, num in MESES.items():
        if anio and palabra in base:
            return f"{anio.group(0)}-{num:02d}"
    return None


def csv_locales(carpeta: Path | None = None) -> dict[str, Path]:
    """Mes → CSV en `data/ecobici/`. Dos archivos del mismo mes es un error."""
    carpeta = carpeta or C.RAW_TRIPS_DIR
    out: dict[str, Path] = {}
    for p in sorted(carpeta.glob("*.csv")):
        mes = mes_de_archivo(p.name)
        if mes is None or mes < INICIO.strftime("%Y-%m"):
            continue
        if mes in out:
            raise ValueError(f"dos CSV para {mes}: {out[mes].name} y {p.name}")
        out[mes] = p
    return out


def enlaces_publicados(html: str) -> dict[str, str]:
    """Mes → URL absoluta de los CSV que lista la página de datos abiertos."""
    out: dict[str, str] = {}
    for href in re.findall(r'href="([^"]+\.csv)"', html, flags=re.I):
        mes = mes_de_archivo(href)
        if mes:
            out[mes] = urllib.parse.urljoin(URL_DATOS, href)
    return out


def _get(url: str, timeout: int = 60) -> bytes:
    """urllib y, si falla (el sitio de Ecobici no manda su certificado
    intermedio y Python no lo encuentra), `curl` del sistema, que usa el
    llavero de macOS. Ninguno desactiva la verificación TLS."""
    req = urllib.request.Request(url, headers={"User-Agent": "movilidad-cdmx/ecosim-actualizar"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as first:
        curl = shutil.which("curl")
        if not curl:
            raise
        p = subprocess.run([curl, "-sSfL", "--max-time", str(timeout), "-A", "movilidad-cdmx/ecosim-actualizar", url],
                           capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(f"urllib: {first}; curl: {p.stderr.decode(errors='replace').strip()}") from first
        return p.stdout


def bajar_mes_nuevo(carpeta: Path | None = None, html: str | None = None, get=_get) -> dict:
    """Baja el mes publicado más reciente si es más nuevo que el último local."""
    carpeta = carpeta or C.RAW_TRIPS_DIR
    locales = csv_locales(carpeta)
    ultimo_local = max(locales) if locales else None
    try:
        html = html if html is not None else get(URL_DATOS, 30).decode("utf-8", "replace")
        publicados = enlaces_publicados(html)
    except Exception as exc:  # red caída, página cambiada, etc.
        return {"estado": "sin_red", "detalle": f"no se pudo leer {URL_DATOS}: {exc}",
                "ultimo_local": ultimo_local}
    if not publicados:
        return {"estado": "sin_enlaces", "detalle": f"{URL_DATOS} no lista CSV reconocibles",
                "ultimo_local": ultimo_local}
    ultimo_publicado = max(publicados)
    if ultimo_local is not None and ultimo_publicado <= ultimo_local:
        return {"estado": "al_dia", "ultimo_publicado": ultimo_publicado, "ultimo_local": ultimo_local,
                "url": publicados[ultimo_publicado]}
    url = publicados[ultimo_publicado]
    nombre = Path(urllib.parse.urlparse(url).path).name
    if not MES_RE.search(nombre):  # el nombre local siempre lleva AAAA-MM (lo usa la auditoría)
        nombre = f"{ultimo_publicado}.csv"
    destino = carpeta / nombre
    try:
        cuerpo = get(url, 600)
    except Exception as exc:
        return {"estado": "descarga_fallida", "detalle": f"{url}: {exc}", "ultimo_publicado": ultimo_publicado,
                "ultimo_local": ultimo_local}
    tmp = destino.with_suffix(".descarga")
    tmp.write_bytes(cuerpo)
    tmp.replace(destino)
    return {"estado": "descargado", "ultimo_publicado": ultimo_publicado, "ultimo_local": ultimo_local,
            "url": url, "archivo": destino.name, "bytes": len(cuerpo)}


def auditar_esquema(archivos: dict[str, Path]) -> dict:
    """Encabezado de cada CSV; falla si falta una columna que usa build_trips."""
    encabezados = {}
    faltan = {}
    for mes, p in sorted(archivos.items()):
        with p.open(newline="", encoding="utf-8-sig") as fh:
            cols = next(csv.reader(fh))
        encabezados[mes] = cols
        miss = [c for c in COLUMNAS if c not in cols]
        if not any(c in cols for c in COLUMNA_ARRIBO):
            miss.append("Fecha_Arribo")
        if miss:
            faltan[mes] = miss
    meses = sorted(encabezados)
    ultimo = meses[-1] if meses else None
    previo = meses[-2] if len(meses) > 1 else None
    out = {"meses": meses, "ultimo": ultimo, "faltan": faltan,
           "ultimo_igual_al_previo": None if previo is None else encabezados[ultimo] == encabezados[previo],
           "encabezado_ultimo": encabezados.get(ultimo)}
    if faltan:
        raise ValueError(f"esquema incompatible: {faltan}")
    return out


@contextlib.contextmanager
def rutas_produccion(archivos: dict[str, Path], parquet: Path = VIAJES, auditoria: Path = AUDITORIA):
    """Redirige `build_trips`/`load_counts` a los archivos de producción.

    Solo cambia atributos de `ecosim.config` dentro de este proceso y los
    restaura al salir; `ecosim/data.py` y `ecosim/pronostico.py` no cambian.
    """
    viejo = (C.RAW_TRIP_FILES, C.TRIPS_PARQUET, C.TRIPS_AUDIT, C.RAW_TRIPS_DIR)
    carpetas = {p.parent for p in archivos.values()}
    if len(carpetas) > 1:
        raise ValueError(f"los CSV deben estar en una sola carpeta: {carpetas}")
    try:
        C.RAW_TRIPS_DIR = next(iter(carpetas)) if carpetas else C.RAW_TRIPS_DIR
        C.RAW_TRIP_FILES = [archivos[m].name for m in sorted(archivos)]
        C.TRIPS_PARQUET = parquet
        C.TRIPS_AUDIT = auditoria
        yield
    finally:
        C.RAW_TRIP_FILES, C.TRIPS_PARQUET, C.TRIPS_AUDIT, C.RAW_TRIPS_DIR = viejo


def ultimo_mes_dia(mes: str) -> date:
    y, m = map(int, mes.split("-"))
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def guardar_conteos(path: Path, days: list[date], universe: list[str], counts: np.ndarray) -> None:
    if counts.max() > np.iinfo(np.uint16).max or (counts != np.rint(counts)).any():
        raise ValueError("conteos fuera de uint16 o no enteros")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.npz")
    np.savez_compressed(tmp, days=np.array([d.isoformat() for d in days]), universe=np.array(universe),
                        counts=counts.astype(np.uint16))
    tmp.replace(path)


def leer_conteos(path: Path = CONTEOS) -> tuple[list[date], list[str], np.ndarray]:
    z = np.load(path)
    return ([date.fromisoformat(str(d)) for d in z["days"]], [str(s) for s in z["universe"]],
            z["counts"].astype(np.float32))


def leer_manifiesto(path: Path = MANIFIESTO) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def actualizar(sin_red: bool = False, forzar: bool = False, carpeta: Path | None = None,
               prod_dir: Path = PROD_DIR, entrenar: bool = True) -> dict:
    from ecosim import data, pronostico as P
    t0 = time.perf_counter()
    carpeta = carpeta or C.RAW_TRIPS_DIR
    descarga = {"estado": "omitida", "detalle": "--sin-red"} if sin_red else bajar_mes_nuevo(carpeta)
    print("descarga:", json.dumps(descarga, ensure_ascii=False), flush=True)
    if descarga["estado"] in ("sin_red", "sin_enlaces", "descarga_fallida"):
        print(f"AVISO: no se pudo automatizar la descarga ({descarga['detalle']}); "
              f"se usan los CSV que haya en {carpeta}", flush=True)
    archivos = csv_locales(carpeta)
    if not archivos:
        raise FileNotFoundError(f"sin CSV de viajes en {carpeta}")
    esquema = auditar_esquema(archivos)
    viajes, auditoria = prod_dir / "viajes.parquet", prod_dir / "viajes_auditoria.json"
    conteos, modelos_dir, manifiesto = prod_dir / "conteos.npz", prod_dir / "modelos", prod_dir / "manifiesto.json"
    previo = leer_manifiesto(manifiesto)
    nombres = [archivos[m].name for m in sorted(archivos)]
    if (previo and not forzar and previo.get("archivos") == nombres
            and all((prod_dir / x).exists() for x in previo.get("modelos", {}).values())
            and conteos.exists() and viajes.exists()):
        print("sin meses nuevos: modelos de producción al día", flush=True)
        previo["descarga"] = descarga
        previo["verificado"] = datetime.now().isoformat(timespec="seconds")
        manifiesto.write_text(json.dumps(previo, ensure_ascii=False, indent=2, default=str))
        return previo
    prod_dir.mkdir(parents=True, exist_ok=True)
    with rutas_produccion(archivos, viajes, auditoria):
        audit = data.build_trips()
        print(f"viajes: {audit['kept']:,} filas de {audit['raw_rows']:,}", flush=True)
        ultimo_mes = max(archivos)
        fin = ultimo_mes_dia(ultimo_mes)
        days, universe, counts = P.load_counts(INICIO, fin)
    por_dia = counts.sum(axis=(1, 2, 3))
    con_viajes = [d for d, n in zip(days, por_dia) if n > 0]
    if not con_viajes or con_viajes[-1] != fin:
        raise ValueError(f"el último CSV ({ultimo_mes}) no trae viajes el {fin}")
    guardar_conteos(conteos, days, universe, counts)
    modelos = {}
    if entrenar:
        modelos_dir.mkdir(parents=True, exist_ok=True)
        for nombre, direct in (("lgbm_diario", False), ("lgbm_directo", True)):
            tic = time.perf_counter()
            out = modelos_dir / f"{nombre}_{INICIO}_{fin}.joblib"
            P.train(counts, days, INICIO, fin, direct, out)
            modelos[nombre] = str(out.relative_to(prod_dir))
            print(f"entrenado {nombre} ({time.perf_counter() - tic:.0f}s) → {out}", flush=True)
    elegibles = P._eligible(counts, days, INICIO, fin)
    man = {
        "actualizado": datetime.now().isoformat(timespec="seconds"),
        "ultimo_dia_publicado": fin.isoformat(),
        "nota_ultimo_dia": "Los CSV se parten por mes de arribo: los viajes del último día que llegan "
                           "después de medianoche están en el mes siguiente (aún no publicado).",
        "fuente": URL_DATOS, "descarga": descarga, "esquema": esquema,
        "archivos": nombres, "meses": sorted(archivos),
        "viajes": {"archivo": viajes.name, "filas": int(audit["kept"]), "filas_crudas": int(audit["raw_rows"]),
                   "auditoria": auditoria.name},
        "conteos": {"archivo": conteos.name, "dias": len(days), "estaciones": len(universe),
                    "primer_dia": days[0].isoformat(), "ultimo_dia": days[-1].isoformat(),
                    "dias_sin_viajes": [d.isoformat() for d, n in zip(days, por_dia) if n == 0]},
        "entrenamiento": {"train_start": INICIO.isoformat(), "train_end": fin.isoformat(),
                          "primer_dia_etiqueta": days[elegibles[0]].isoformat() if elegibles else None,
                          "dias_etiqueta": len(elegibles),
                          "meses": sorted({d.strftime("%Y-%m") for d in days if INICIO <= d <= fin}),
                          "variables_diario": P.FEATURES_DAILY, "variables_directo": P.FEATURES_DIRECT,
                          "parametros": P.LGB_PARAMS,
                          "funcion": "ecosim.pronostico.train"},
        "modelos": modelos,
        "segundos": round(time.perf_counter() - t0, 1),
    }
    tmp = manifiesto.with_suffix(".tmp")
    tmp.write_text(json.dumps(man, ensure_ascii=False, indent=2, default=str))
    tmp.replace(manifiesto)
    # Modelos de cortes anteriores de producción: se dejan solo los vigentes.
    if entrenar:
        vigentes = {Path(x).name for x in modelos.values()}
        for p in modelos_dir.glob("*.joblib"):
            if p.name not in vigentes:
                p.unlink()
    return man


def main(argv=None):
    ap = argparse.ArgumentParser(description="Baja el mes nuevo de Ecobici y reentrena modelos de producción.")
    ap.add_argument("--sin-red", action="store_true", help="no intentar descargar; usar data/ecobici/")
    ap.add_argument("--forzar", action="store_true", help="reconstruir aunque no haya meses nuevos")
    a = ap.parse_args(argv)
    man = actualizar(sin_red=a.sin_red, forzar=a.forzar)
    print(json.dumps({k: man[k] for k in ("ultimo_dia_publicado", "actualizado", "entrenamiento", "modelos")},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
