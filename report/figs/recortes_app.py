"""JPG de las capturas de la app para reporte-caso, desde las PNG de captura_app.cjs.

    node report/figs/captura_app.cjs <dir>               # PNG + cajas.json en <dir>
    uv run python3 report/figs/recortes_app.py <dir>     # escribe report/figs/app-caso/*.jpg
    uv run python3 report/figs/recortes_app.py <dir> --check

Por cada captura: el JPG completo (`<nombre>.jpg`) y, salvo para sin
rebalanceo contra LightGBM directo, la versión de columnas (`<nombre>-col.jpg`):
de la captura sin controles (`-limpio.png`) se toma cada mapa entre la estación
más al oeste y la más al este, ±25 px, a toda su altura, y si son dos se pegan
con 16 px en blanco.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent / 'app-caso'
MARGEN, SEPARACION, CALIDAD = 25, 16, 90


def jpg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=CALIDAD)
    return buf.getvalue()


def generate(src: Path) -> dict[Path, bytes]:
    files = {}
    for c in json.loads((src / 'cajas.json').read_text()):
        name = c['name']
        files[OUT / name.replace('.png', '.jpg')] = jpg(Image.open(src / name).convert('RGB'))
        if name.startswith('sin-lgbm'):
            continue
        clean = Image.open(src / name.replace('.png', '-limpio.png')).convert('RGB')
        parts = [clean.crop((int(b['left'] + b['x0'] - MARGEN), int(b['top']),
                             int(b['left'] + b['x1'] + MARGEN), int(b['top'] + b['height'])))
                 for b in c['box']]
        col = Image.new('RGB', (sum(p.width for p in parts) + SEPARACION * (len(parts) - 1), parts[0].height), 'white')
        x = 0
        for part in parts:
            col.paste(part, (x, 0))
            x += part.width + SEPARACION
        files[OUT / name.replace('.png', '-col.jpg')] = jpg(col)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src', type=Path)
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()
    files = generate(args.src)
    if args.check:
        bad = [p.name for p, data in files.items() if not p.exists() or p.read_bytes() != data]
        if bad:
            print('cambió:', *bad)
            sys.exit(1)
        print('ok:', len(files), 'JPG iguales')
        return
    OUT.mkdir(exist_ok=True)
    for p, data in files.items():
        p.write_bytes(data)
        print('escrito', p.name)


if __name__ == '__main__':
    main()
