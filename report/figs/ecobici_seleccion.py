"""Replay de Ecobici (principal: rebal, t0, sin ±1, dañadas dinámicas) y baseline en los
15 días de SELECCIÓN, para comparar la curva de λ (que se eligió en esos días) contra
Ecobici a igual número de movimientos. El run 2 solo corrió a Ecobici en evaluación.

Usa el código congelado del run 2 (`ecosim/` de la rama `ecosim2-integracion`), exportado
con `git archive` a un directorio temporal, sin tocar `ecosim/` de este worktree. Cómputo
pesado: correr con el semáforo.

python3 .../.ecosim2-run/heavy.py uv run python report/figs/ecobici_seleccion.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
tmp = Path(tempfile.mkdtemp(prefix="ecosim2_code_"))
subprocess.run(f"git -C {REPO} archive ecosim2-integracion ecosim | tar -x -C {tmp}", shell=True, check=True)
os.environ["ECOSIM_DATA_ROOT"] = str(REPO / "data")
sys.path.insert(0, str(tmp))

import pandas as pd  # noqa: E402
from ecosim import run  # noqa: E402

days = json.loads((REPO / "ecosim" / "days.json").read_text())
rows = []
for d in [x["day"] for x in days["seleccion"]]:
    for arm in ("baseline", "ecobici"):
        row, _ = run.run_one(run._fixed(d, "seleccion", "reporte", arm))
        rows.append(row)
        print(d, arm, row["EF"], row["moves"], flush=True)
out = pd.DataFrame(rows)
out.to_csv(Path(__file__).with_name("ecobici_seleccion.csv"), index=False)
print(out.groupby("arm")[["EF", "E", "F", "moves", "bikes_moved"]].mean())
