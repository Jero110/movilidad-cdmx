# App desde un clon limpio:  make up  →  http://127.0.0.1:8000
# Necesita uv (https://docs.astral.sh/uv/). Los datos de la app (replay precalculado y modelos de producción) vienen
# en el repo, en datos-app/: no se baja nada aparte; uv solo baja Python y las dependencias la primera vez.
PORT ?= 8000
APP := datos-app/derived/ecosim
DST := data/derived/ecosim

.PHONY: up deps datos test

# Arranca en el 8000 (si algo ya lo ocupa, el servidor lo detiene) y abre el navegador.
up: deps datos
	uv run python3 scripts/ecobici_mapa/server.py --port $(PORT)

deps:
	@command -v uv >/dev/null 2>&1 || { echo "Falta uv. Instálalo con:  curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	uv sync

# Copia a data/ lo que falte de datos-app/; si data/ ya trae sus datos (o es un enlace a ellos), no toca nada.
datos:
	@mkdir -p $(DST)
	@[ -f $(DST)/replay/index.json ] || { cp -R $(APP)/replay $(DST)/ && echo "Replay: copiado de datos-app/."; }
	@[ -f $(DST)/produccion/manifiesto.json ] || { mkdir -p $(DST)/produccion && cp -R $(APP)/produccion/. $(DST)/produccion/ && echo "Modelos de producción: copiados de datos-app/."; }
	@echo "Datos de la app: listos."

test: deps datos
	uv run pytest -q
