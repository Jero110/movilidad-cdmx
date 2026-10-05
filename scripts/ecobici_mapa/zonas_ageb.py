"""Zonas para la coropleta: AGEB urbanas del INEGI con al menos una estación Ecobici.

    uv run python scripts/ecobici_mapa/zonas_ageb.py
    uv run python scripts/ecobici_mapa/zonas_ageb.py --shp ruta/09_ciudaddemexico.zip   # o ruta/09a.shp

1. Baja el Marco Geoestadístico 2024 de INEGI para la entidad 09 (Ciudad de
   México) a `data/inegi/` (se reutiliza si ya está). Si la descarga falla, lo
   dice y pide `--shp` con el ZIP o la capa `09a.shp` local.
2. Lee la capa de AGEB urbanas (`09a`) y las alcaldías (`09mun`, nombre
   `NOMGEO`), en Cónica Conforme de Lambert (ITRF2008).
3. Asigna cada estación de `station_information` (feed oficial) a la AGEB que
   la contiene; si cae fuera de toda AGEB urbana, a la más cercana (se anota en
   `metadata.cercanas` con la distancia en metros).
4. Se queda con las AGEB que tienen estaciones, simplifica la geometría
   (`TOLERANCIA_M` metros, sin cambiar la topología de cada polígono),
   reproyecta a WGS84 (EPSG:4326) y escribe `zonas_ageb.geojson`.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MARCO = "Marco Geoestadístico 2024 (INEGI), corte de actualización cartográfica agosto 2024"
URL = ("https://www.inegi.org.mx/contenidos/productos/prod_serv/contenidos/espanol/bvinegi/productos/"
       "geografia/marcogeo/794551132173/09_ciudaddemexico.zip")
CACHE = REPO_ROOT / "data" / "inegi" / "09_ciudaddemexico.zip"
GBFS_INFO = "https://gbfs.mex.lyftbikes.com/gbfs/es/station_information.json"
SALIDA = HERE / "zonas_ageb.geojson"
TOLERANCIA_M = 4.0
DECIMALES = 6  # ~0.1 m


def bajar(url: str = URL, destino: Path = CACHE) -> Path:
    if destino.exists() and destino.stat().st_size > 0:
        return destino
    from ecosim.actualizar import _get  # urllib y, si falla, curl del sistema
    destino.parent.mkdir(parents=True, exist_ok=True)
    cuerpo = _get(url, 900)
    tmp = destino.with_suffix(".descarga")
    tmp.write_bytes(cuerpo)
    tmp.replace(destino)
    return destino


def capas(origen: Path):
    """AGEB urbanas y alcaldías desde el ZIP del INEGI o desde `09a.shp` suelto."""
    import geopandas as gpd
    origen = Path(origen)
    if origen.suffix.lower() == ".zip":
        ageb = gpd.read_file(f"zip://{origen}!conjunto_de_datos/09a.shp")
        mun = gpd.read_file(f"zip://{origen}!conjunto_de_datos/09mun.shp")
    else:
        ageb = gpd.read_file(origen)
        p = origen.with_name("09mun.shp")
        mun = gpd.read_file(p) if p.exists() else None
    return ageb, mun


def estaciones(url: str = GBFS_INFO) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "movilidad-cdmx/zonas-ageb"})
    with urllib.request.urlopen(req, timeout=30) as r:
        info = json.loads(r.read())
    return [{"short_name": str(s.get("short_name") or s["station_id"]), "lat": float(s["lat"]), "lon": float(s["lon"])}
            for s in info["data"]["stations"]]


def asignar(ageb, puntos: list[dict]):
    """Estación → índice de AGEB (contiene; si no, la más cercana) y anotaciones."""
    import geopandas as gpd
    pts = gpd.GeoDataFrame(puntos, geometry=gpd.points_from_xy([p["lon"] for p in puntos],
                                                                [p["lat"] for p in puntos]), crs="EPSG:4326").to_crs(ageb.crs)
    dentro = gpd.sjoin(pts, ageb[["geometry"]], how="left", predicate="within")
    dentro = dentro[~dentro.index.duplicated(keep="first")]  # en un borde exacto, la primera
    fuera = dentro.index_right.isna()
    cercanas = []
    if fuera.any():
        near = gpd.sjoin_nearest(pts[fuera], ageb[["geometry"]], how="left", distance_col="metros")
        near = near[~near.index.duplicated(keep="first")]
        dentro.loc[near.index, "index_right"] = near.index_right
        for i, r in near.iterrows():
            cercanas.append({"short_name": pts.loc[i, "short_name"], "cvegeo": ageb.loc[r.index_right, "CVEGEO"],
                             "metros": round(float(r.metros), 1)})
    return dentro.index_right.astype(int), cercanas


def _redondear(c):
    if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
        return [round(float(x), DECIMALES) for x in c]
    return [_redondear(x) for x in c]


def construir(origen: Path, puntos: list[dict], fuente: dict) -> dict:
    from shapely.geometry import mapping
    ageb, mun = capas(origen)
    nombres = dict(zip(mun.CVE_MUN, mun.NOMGEO)) if mun is not None else {}
    idx, cercanas = asignar(ageb, puntos)
    por_ageb: dict[int, list[str]] = {}
    for p, i in zip(puntos, idx):
        por_ageb.setdefault(int(i), []).append(p["short_name"])
    sel = ageb.loc[sorted(por_ageb)].copy()
    sel["geometry"] = sel.geometry.simplify(TOLERANCIA_M, preserve_topology=True)
    sel = sel.to_crs("EPSG:4326")
    feats = []
    for i, r in sel.iterrows():
        geom = mapping(r.geometry)
        feats.append({"type": "Feature",
                      "properties": {"cvegeo": r.CVEGEO, "alcaldia": nombres.get(r.CVE_MUN, r.CVE_MUN),
                                     "estaciones": sorted(por_ageb[i])},
                      "geometry": {"type": geom["type"], "coordinates": _redondear(geom["coordinates"])}})
    feats.sort(key=lambda f: f["properties"]["cvegeo"])
    return {"type": "FeatureCollection",
            "metadata": {**fuente, "capa": "09a (AGEB urbanas)", "proyeccion_origen": str(ageb.crs.name),
                         "crs": "EPSG:4326", "simplificacion_m": TOLERANCIA_M, "zonas": len(feats),
                         "estaciones": len(puntos), "cercanas": cercanas,
                         "generado": datetime.now().isoformat(timespec="seconds")},
            "features": feats}


def main(argv=None):
    ap = argparse.ArgumentParser(description="AGEB urbanas del INEGI con estaciones Ecobici → GeoJSON")
    ap.add_argument("--shp", help="ZIP 09_ciudaddemexico.zip del INEGI o la capa 09a.shp local")
    ap.add_argument("--salida", default=str(SALIDA))
    a = ap.parse_args(argv)
    if a.shp:
        origen, fuente = Path(a.shp), {"fuente": MARCO, "url": URL, "archivo": Path(a.shp).name}
    else:
        try:
            origen = bajar()
        except Exception as exc:
            sys.exit(f"No se pudo bajar el Marco Geoestadístico de {URL}: {exc}\n"
                     "Bájalo a mano y corre de nuevo con --shp ruta/09_ciudaddemexico.zip (o ruta/09a.shp).")
        fuente = {"fuente": MARCO, "url": URL, "archivo": origen.name}
    puntos = estaciones()
    gj = construir(origen, puntos, fuente)
    out = Path(a.salida)
    out.write_text(json.dumps(gj, ensure_ascii=False, separators=(",", ":")))
    m = gj["metadata"]
    print(f"{m['zonas']} AGEB con {m['estaciones']} estaciones → {out} ({out.stat().st_size / 1e6:.2f} MB); "
          f"{len(m['cercanas'])} asignadas a la AGEB más cercana: {m['cercanas']}")


if __name__ == "__main__":
    main()
