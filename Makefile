# App desde un clon limpio:  make up  →  http://127.0.0.1:8000
# Necesita uv (https://docs.astral.sh/uv/) e internet la primera vez: baja Python y dependencias, y los datos de la
# app (replay precalculado y modelos de producción, ~180 MB) del Release del repo. Después ya no baja nada.
PORT ?= 8000
DATOS_TAG ?= datos-app-2026-10-15
DATOS_URL ?= https://github.com/Jero110/movilidad-cdmx/releases/download/$(DATOS_TAG)/datos-app.tar.gz

.PHONY: up deps datos test

# Arranca en el 8000 (si algo ya lo ocupa, el servidor lo detiene) y abre el navegador.
up: deps datos
	uv run python3 scripts/ecobici_mapa/server.py --port $(PORT)

deps:
	@command -v uv >/dev/null 2>&1 || { echo "Falta uv. Instálalo con:  curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	uv sync

# Solo baja si faltan; con datos propios en data/ (o un enlace a ellos) no toca nada.
datos:
	@if [ -f data/derived/ecosim/produccion/manifiesto.json ] && [ -f data/derived/ecosim/replay/index.json ]; then \
	  echo "Datos de la app: listos."; \
	else \
	  echo "Bajando los datos de la app (~180 MB)..."; \
	  mkdir -p data && curl -fL --progress-bar -o data/.datos-app.tar.gz "$(DATOS_URL)" && \
	  tar -xzf data/.datos-app.tar.gz -C data && rm -f data/.datos-app.tar.gz && echo "Datos de la app: listos."; \
	fi

test: deps datos
	uv run pytest -q
