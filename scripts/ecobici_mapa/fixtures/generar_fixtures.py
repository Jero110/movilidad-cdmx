"""Genera los fixtures JSON del frontend con la forma de los contratos del plan ecosim3-ux.

Son datos de prueba para desarrollar y probar la UI sin el backend nuevo, no resultados:
- `snapshot.json`: feed GBFS real del momento (station_information + station_status) con `raw`.
- `replay_*`: un día real del replay run 3 (2025-09-01, brazos `ma_diaria` y `ecobici`) al que se
  le agregan `snap`, `est`, `desvios` y `trips.id`. Las salidas, llegadas y cambios de etiqueta de
  cada foto se reconstruyen para que la identidad del cuadre se cumpla; los desvíos son ilustrativos.
- `live_*`: modelos, un pronóstico y una plantilla de asignación construidos sobre el snapshot.
- `zonas.json`: contrato de GET /api/zonas (AGEB urbanas). Las geometrías son una cuadrícula de prueba de
  ~700 m alrededor de las estaciones, NO AGEB reales del INEGI; `cvegeo` empieza con "PRUEBA-" para que
  no se confunda.

Uso (desde la raíz del repo):  uv run python3 scripts/ecobici_mapa/fixtures/generar_fixtures.py
"""
from __future__ import annotations

import json
import random
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent.parent
REPLAY = REPO / "data" / "derived" / "ecosim" / "replay"
DAY = "2025-09-01"
ARMS = ["ma_diaria", "ecobici"]
GBFS = "https://gbfs.mex.lyftbikes.com/gbfs/es"


def dump(name: str, obj) -> None:
    (HERE / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    print("escrito", name, f"{(HERE / name).stat().st_size/1e3:.0f} kB")


def gbfs(feed: str) -> dict:
    req = urllib.request.Request(f"{GBFS}/{feed}.json", headers={"User-Agent": "movilidad-cdmx/fixtures"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def snapshot() -> dict:
    info, status = gbfs("station_information"), gbfs("station_status")
    by_id = {s["station_id"]: s for s in status["data"]["stations"]}
    out = []
    for st in info["data"]["stations"]:
        live = by_id.get(st["station_id"])
        if not live:
            continue
        out.append({"id": st["station_id"], "name": st["name"], "short_name": st.get("short_name", ""),
                    "lat": st["lat"], "lon": st["lon"], "capacity": st.get("capacity", 0),
                    "bikes": live["num_bikes_available"], "docks": live["num_docks_available"],
                    "bikes_disabled": live.get("num_bikes_disabled", 0), "docks_disabled": live.get("num_docks_disabled", 0),
                    "renting": bool(live.get("is_renting", 0)), "returning": bool(live.get("is_returning", 0)),
                    "installed": bool(live.get("is_installed", 0)), "last_reported": live.get("last_reported"),
                    "alerted": False, "raw": {**st, **live}})
    return {"generated_at": int(time.time()), "last_updated": status.get("last_updated"), "stale": False,
            "stations": out, "alerts": []}


def replay() -> None:
    idx = json.loads((REPLAY / "index.json").read_text())
    day_entry = idx["days"][DAY]
    idx["days"] = {DAY: {**day_entry, "arms": {a: day_entry["arms"][a] for a in ARMS}}}
    idx["arms"] = ARMS
    dump("replay_index.json", idx)

    dia = json.loads((REPLAY / DAY / "dia.json").read_text())
    t = dia["trips"]
    t["id"] = list(range(len(t["o"])))
    dump(f"replay_{DAY}_dia.json", dia)
    n_st = len(dia["stations"]["short_name"])
    nf = dia["n_frames"]

    rng = random.Random(7)
    lat, lon = dia["stations"]["lat"], dia["stations"]["lon"]

    def vecina(i: int) -> int:
        """Una de las 3 estaciones más cercanas a i (los desvíos van a la más cercana con lugar)."""
        if lat[i] is None:
            return i
        d = sorted((((lat[j] - lat[i]) ** 2 + (lon[j] - lon[i]) ** 2), j) for j in range(n_st) if j != i and lat[j] is not None)
        return d[rng.randrange(3)][1]

    for arm in ARMS:
        a = json.loads((REPLAY / DAY / f"{arm}.json").read_text())
        bikes, dis = a["bikes"], a["dis"]
        # applied[k] del run 3 ya refleja lo aplicado entre t_k-1 y t_k (bikes[k] lo incluye).
        applied = a["applied"]
        a["applied"] = applied
        snap, est, desv = [], [], []
        for k in range(nf):
            if k == 0:
                snap.append({"min_desde_anterior": 0, "salidas": 0, "llegadas": 0, "desvios_salida": 0,
                             "desvios_llegada": 0, "emitidas": len((a["decision"][0] or {}).get("orders", [])),
                             "recogidas": 0, "entregadas": 0, "a_rentable": 0, "a_no_rentable": 0, "E": 0, "F": 0,
                             "EF": 0, "EF_acum": 0, "cuadre_ok": True, "descuadre_estaciones": 0,
                             "bicis_sistema": sum(bikes[0]) + sum(dis[0])})
                est.append([]); desv.append([])
                continue
            lo, hi = 15 * (k - 1), 15 * k
            sal = [0] * n_st; lle = [0] * n_st; ent = [0] * n_st; rec = [0] * n_st
            for o, dep in zip(t["o"], t["dep"]):
                if o >= 0 and lo < dep <= hi:
                    sal[o] += 1
            for d, arr in zip(t["d"], t["arr"]):
                if d >= 0 and lo < arr <= hi:
                    lle[d] += 1
            for i, _delta, ap, _ik in applied[k]:
                if i >= 0:
                    (ent if ap > 0 else rec)[i] += abs(ap)
            rows, dk = [], []
            ds = dl = 0
            for i in range(n_st):
                dd = dis[k][i] - dis[k - 1][i]
                anr, ar = max(dd, 0), max(-dd, 0)
                expected = bikes[k - 1][i] - sal[i] + lle[i] + ent[i] - rec[i] + ar - anr
                r = bikes[k][i] - expected
                if r > 0:
                    lle[i] += r; dl += r
                    for _ in range(min(r, 2)):
                        dk.append([rng.randrange(len(t["o"])), "llegada", vecina(i), i, rng.randint(150, 600)])
                elif r < 0:
                    sal[i] += -r; ds += -r
                    for _ in range(min(-r, 2)):
                        dk.append([rng.randrange(len(t["o"])), "salida", vecina(i), i, rng.randint(150, 600)])
                if ent[i] or rec[i] or ar or anr or sal[i] or lle[i]:
                    rows.append([i, ent[i], rec[i], ar, anr, sal[i], lle[i]])
            E = a["cum"]["E"][k] - a["cum"]["E"][k - 1]
            F = a["cum"]["F"][k] - a["cum"]["F"][k - 1]
            snap.append({"min_desde_anterior": 15, "salidas": sum(sal), "llegadas": sum(lle),
                         "desvios_salida": ds, "desvios_llegada": dl,
                         "emitidas": len((a["decision"][k] or {}).get("orders", [])),
                         "recogidas": sum(rec), "entregadas": sum(ent),
                         "a_rentable": sum(r[3] for r in rows), "a_no_rentable": sum(r[4] for r in rows),
                         "E": E, "F": F, "EF": E + F, "EF_acum": a["cum"]["E"][k] + a["cum"]["F"][k],
                         "cuadre_ok": True, "descuadre_estaciones": 0,
                         "bicis_sistema": sum(bikes[k]) + sum(dis[k])})
            est.append(rows); desv.append(dk)
        a["snap"], a["est"], a["desvios"] = snap, est, desv
        dump(f"replay_{DAY}_{arm}.json", a)


def live(snap: dict) -> None:
    dump("live_models.json", {
        "datos": {"ultimo_dia_publicado": "2026-09-30", "actualizado": "2026-10-05T12:10:00"},
        "modelos": [
            {"key": "ma_diaria", "forma": "diaria", "label": "Media móvil diaria", "disponible": True, "motivo": None,
             "corte": {"train_start": "2024-01-01", "train_end": "2026-09-30"}, "n": 4, "lambda": 72.8507,
             "nota": "Promedio de los días publicados del mismo tipo."},
            {"key": "lgbm_diario", "forma": "diaria", "label": "LightGBM diario", "disponible": True, "motivo": None,
             "corte": {"train_start": "2024-01-01", "train_end": "2026-09-30"}, "n": 4, "lambda": 72.7447,
             "nota": "Pronóstico del día completo con rezagos de 7 y 14 días."},
            {"key": "lgbm_directo", "forma": "directa", "label": "LightGBM directo", "disponible": True, "motivo": None,
             "corte": {"train_start": "2024-01-01", "train_end": "2026-09-30"}, "n": 4, "lambda": 70.1,
             "nota": "Usa lo observado hoy (salidas y llegadas inferidas del feed)."},
            {"key": "oraculo_diario", "forma": "diaria", "label": "Oráculo diario", "disponible": False,
             "motivo": "Necesita los viajes reales del día; solo existe en el experimento.",
             "corte": None, "n": 4, "lambda": 57.58, "nota": None},
        ]})
    m = json.loads((HERE / "live_models.json").read_text())
    for x in m["modelos"]:
        x["horizontes"] = ["dia"] if x["forma"] == "diaria" else [1, 2, 3, 4]
    dump("live_models.json", m)
    st = [s for s in snap["stations"] if s["installed"]]
    rng = random.Random(11)
    steps = 16
    bikes_now = [s["bikes"] for s in st]
    proy, sal, lle = [], [], []
    cur = list(bikes_now)
    trend = [rng.choice([-1, -1, 0, 1, 1, 2, -2]) for _ in st]
    for _ in range(steps):
        s_row = [max(0, round(rng.random() * 3 + max(0, -trend[i]))) for i in range(len(st))]
        l_row = [max(0, round(rng.random() * 3 + max(0, trend[i]))) for i in range(len(st))]
        cur = [max(0, min(st[i]["capacity"], cur[i] - s_row[i] + l_row[i])) for i in range(len(st))]
        proy.append(cur); sal.append(s_row); lle.append(l_row)
    riesgos = []
    for i, s in enumerate(st):
        for j, row in enumerate(proy):
            if row[i] == 0:
                riesgos.append({"short_name": s["short_name"], "tipo": "vacia", "minutos": 15 * (j + 1)}); break
            if row[i] >= s["capacity"] and s["capacity"]:
                riesgos.append({"short_name": s["short_name"], "tipo": "llena", "minutos": 15 * (j + 1)}); break
    riesgos.sort(key=lambda r: r["minutos"])
    dump("live_forecast.json", {
        "id": "fx-forecast", "issued_at": "2026-10-05T13:30:12", "t_feed": "2026-10-05T13:29:48", "model": "lgbm_directo",
        "referencia": {"rezagos": "reales", "dia_referencia": None,
                       "feed_hoy": {"desde": "2026-10-05T05:00", "fotos": 510, "completo": True},
                       "viajes_del_dia": "inferidos_feed"},
        "minutes": [15 * (j + 1) for j in range(steps)],
        "stations": {"short_name": [s["short_name"] for s in st], "name": [s["name"] for s in st],
                     "lat": [s["lat"] for s in st], "lon": [s["lon"] for s in st], "cap": [s["capacity"] for s in st]},
        "bikes_now": bikes_now, "proyeccion": proy, "salidas": sal, "llegadas": lle, "riesgos": riesgos})
    pasos = []
    for p in range(4):
        hh = 13 + (30 + 15 * p) // 60; mm = (30 + 15 * p) % 60
        t = f"2026-10-05T{hh:02d}:{mm:02d}"
        em = []
        for _ in range(rng.randint(5, 12)):
            s = rng.choice(st)
            acc = rng.choice(["recoger", "entregar"])
            em.append({"short_name": s["short_name"], "accion": acc, "n": rng.randint(2, 12), "emitida": t,
                       "recoge": f"2026-10-05T{(hh + (mm + 15)//60):02d}:{(mm + 15) % 60:02d}",
                       "entrega": f"2026-10-05T{hh + 1:02d}:{mm:02d}"})
        pasos.append({"t": t, "t_feed": t + ":05", "emitidas": em, "aplicadas": pasos[-1]["emitidas"][:3] if pasos else [],
                      "bicis_a_mover": sum(o["n"] for o in em), "visitas": len(em),
                      "salidas_est": rng.randint(150, 400), "llegadas_est": rng.randint(150, 400)})
    dump("live_assign_template.json", {"model": "lgbm_directo", "pasos": pasos})


def zonas(snap: dict) -> None:
    """Cuadrícula de prueba con el contrato de /api/zonas: {cvegeo, alcaldia, estaciones}."""
    paso = 0.0065
    celdas: dict[tuple[int, int], list[str]] = {}
    for s in snap["stations"]:
        celdas.setdefault((int(s["lon"] // paso), int(s["lat"] // paso)), []).append(s["short_name"])
    feats = []
    for n, ((ix, iy), est) in enumerate(sorted(celdas.items())):
        x0, y0 = ix * paso, iy * paso
        anillo = [[x0, y0], [x0 + paso, y0], [x0 + paso, y0 + paso], [x0, y0 + paso], [x0, y0]]
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[round(a, 6), round(b, 6)] for a, b in anillo]]},
                      "properties": {"cvegeo": f"PRUEBA-{n:04d}", "alcaldia": "Cuadrícula de prueba", "estaciones": sorted(est)}})
    dump("zonas.json", {"type": "FeatureCollection", "features": feats})


if __name__ == "__main__":
    snap = snapshot()
    dump("snapshot.json", snap)
    replay()
    live(snap)
    zonas(snap)
