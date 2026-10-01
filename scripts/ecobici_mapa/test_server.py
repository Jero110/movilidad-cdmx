"""Asserts contra las dos rarezas del feed documentadas en README.md.

Ambas mis-flaguean estaciones en silencio si se rompen; ver README.md
"Dos rarezas del feed que el codigo maneja". Los strings de entrada son los
reales citados ahi, no inventados.

    uv run pytest scripts/ecobici_mapa/test_server.py
"""

from server import api_rebalance, api_rebalance_station, parse_station_numbers, short_name_keys


def test_parse_station_numbers_expands_ranges_and_singles():
    text = "Cuauhtémoc: 176, 264 a 269 y 271 a 275"
    assert parse_station_numbers(text) == {
        "176", "264", "265", "266", "267", "268", "269", "271", "272", "273", "274", "275",
    }


def test_parse_station_numbers_ignores_date_ranges():
    # La fecha "del 13 al 16 de septiembre" comparte forma con un rango de
    # estaciones ("264 a 269"); debe descartarse, no leerse como 13..16.
    text = (
        "Cuauhtémoc: 176, 264 a 269 y 271 a 275 estarán fuera de servicio "
        "del 13 al 16 de septiembre."
    )
    found = parse_station_numbers(text)
    assert not ({"13", "14", "15", "16"} & found), (
        f"la fecha se coló como numero de estacion: {found}"
    )
    assert {"176", "264", "269", "271", "275"} <= found


def test_short_name_keys_single():
    assert short_name_keys("033") == {"33"}


def test_short_name_keys_composite_range():
    # Compuesto real del feed: una estacion fisica cubriendo varios numeros.
    assert short_name_keys("264-275") == {str(n) for n in range(264, 276)}


def test_short_name_keys_composite_pair():
    assert short_name_keys("268-269") == {"268", "269"}
    assert short_name_keys("390-391") == {"390", "391"}


def test_short_name_keys_and_alert_numbers_share_id_space():
    # El caso que el README marca como el riesgo real: una alerta que
    # menciona "264 a 269" debe cruzar contra la estacion compuesta
    # "264-275" del feed, no quedarse sin marcar por comparar en el espacio
    # equivocado (station_id en vez de short_name).
    alerted_names = parse_station_numbers("Cuauhtémoc: 264 a 269")
    assert short_name_keys("264-275") & alerted_names


def test_rebalance_summary_is_consistent_with_routes_fixture():
    payload = api_rebalance()

    assert payload["day"] == "2025-09-17"
    assert payload["n_routes"] == len(payload["routes"])
    assert payload["n_bikes"] == sum(route["n_bikes"] for route in payload["routes"])
    # same_day (days_crossed=0) + overnight (days_crossed=1) no siempre suma
    # n_bikes: algunas bicis reaparecen 2+ dias despues (mantenimiento, baja
    # temporal), y esas rutas no caen en ninguna de las dos categorias.
    same_day_bikes = sum(r["n_bikes"] for r in payload["routes"] if r["days_crossed"] == 0)
    overnight_bikes = sum(r["n_bikes"] for r in payload["routes"] if r["days_crossed"] == 1)
    assert payload["n_bikes_same_day"] == same_day_bikes
    assert payload["n_bikes_overnight"] == overnight_bikes
    assert payload["n_bikes"] >= payload["n_bikes_same_day"] + payload["n_bikes_overnight"]
    assert all(route["from_station"] != route["to_station"] for route in payload["routes"])
    assert all(route["days_crossed"] >= 0 for route in payload["routes"])


def test_rebalance_station_returns_detail_matching_its_summary():
    route = api_rebalance()["routes"][0]
    detail = api_rebalance_station(route["from_station"])

    assert detail["short_name"] == route["from_station"]
    assert detail["n_out"] + detail["n_in"] == len(detail["moves"])
    assert all(move["direction"] in {"in", "out"} for move in detail["moves"])
    assert all(move["bici"] > 0 for move in detail["moves"])
