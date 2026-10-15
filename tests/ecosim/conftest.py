"""Pruebas que usan datos de la investigación que no vienen en el paquete de la app (`make datos`): fotos del feed
(`ecosim.data build-snapshots`), eventos de bicis dañadas y CSV mensuales de viajes. Si faltan, se saltan con el
motivo; con los datos completos corren igual que siempre."""
from __future__ import annotations

import pytest

from ecosim import config as C

_SNAP = C.DERIVED / "snapshots"
_DANADAS = C.DERIVED / "damage_events.parquet"
_COBERTURA = C.DERIVED / "snapshot_coverage_run3.json"

# función de prueba → archivos que necesita
_NECESITA = {
    "test_real_december_state_and_trip_span": [_SNAP],
    "test_unusable_opening_raises": [_SNAP],
    "test_coverage_audit_and_march_cutoff": [_COBERTURA, _SNAP],
    "test_replay_cuadra_cada_foto": [_SNAP, _DANADAS],
    "test_replay_day_arm_ef_igual_resultados_y_cuadre": [_SNAP, _DANADAS],
    "test_repara_dia_obsoleto_sin_recalcular_brazos": [_SNAP, _DANADAS],
    "test_variante_de_alfa_cuadra_y_baja_los_km": [_SNAP, _DANADAS],
}


def _hay(p):
    return p.is_file() or (p.is_dir() and any(p.iterdir()))


def pytest_collection_modifyitems(config, items):
    csv = any(C.RAW_TRIPS_DIR.glob("*.csv")) if C.RAW_TRIPS_DIR.is_dir() else False
    for item in items:
        nombre = getattr(item, "originalname", item.name)
        faltan = [p for p in _NECESITA.get(nombre, []) if not _hay(p)]
        if nombre == "test_manifiesto_de_produccion" and not csv:
            faltan.append(C.RAW_TRIPS_DIR / "*.csv")
        if faltan:
            rel = ", ".join(str(p.relative_to(C.REPO_ROOT)) if p.is_relative_to(C.REPO_ROOT) else str(p) for p in faltan)
            item.add_marker(pytest.mark.skip(reason=f"datos de la investigación que no vienen con la app: falta {rel}"))
