#!/usr/bin/env python3
"""Exporta a SVG los diagramas Mermaid de docs/ARCH.md y docs/PROCESOS.md.

Cada bloque exportable va precedido de `<!-- svg: nombre -->` y se escribe en
`docs/diagrams/<nombre>.svg`. Requiere Node 22 y Chrome o Chromium (`CHROME_PATH`).

Uso: python3 scripts/docs/export_diagrams.py [nombre ...]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (ROOT / "docs" / "ARCH.md", ROOT / "docs" / "PROCESOS.md")
OUT_DIR = ROOT / "docs" / "diagrams"
CONFIG = Path(__file__).with_name("mermaid.config.json")
MMDC_PACKAGE = "@mermaid-js/mermaid-cli@12.0.0"
BLOCK = re.compile(r"<!-- svg: ([a-z0-9-]+) -->\n```mermaid\n(.*?)\n```", re.DOTALL)
TAG = re.compile(r"<[^>]+>")
LONG_FLOAT = re.compile(r"(\d+\.\d{2})\d+")


def shrink(svg: str) -> str:
    """Recorta a 2 decimales las coordenadas de las etiquetas; el texto no se toca."""
    return TAG.sub(lambda m: LONG_FLOAT.sub(r"\1", m.group(0)), svg)


def collect() -> dict[str, str]:
    found: dict[str, str] = {}
    for source in SOURCES:
        for name, code in BLOCK.findall(source.read_text(encoding="utf-8")):
            if name in found:
                raise SystemExit(f"nombre de diagrama duplicado: {name}")
            found[name] = code
    return found


def main(argv: list[str]) -> int:
    diagrams = collect()
    wanted = argv or sorted(diagrams)
    unknown = [name for name in wanted if name not in diagrams]
    if unknown:
        print(f"diagramas desconocidos: {', '.join(unknown)}", file=sys.stderr)
        return 2
    npx = shutil.which("npx")
    if npx is None:
        print("falta Node 22 (npx)", file=sys.stderr)
        return 2

    puppeteer: dict[str, object] = {}
    env = dict(os.environ)
    chrome = os.environ.get("CHROME_PATH") or os.environ.get("PUPPETEER_EXECUTABLE_PATH")
    if chrome:
        puppeteer["executablePath"] = chrome
        env["PUPPETEER_SKIP_DOWNLOAD"] = "true"
    if getattr(os, "geteuid", lambda: 1)() == 0:
        # Chrome no arranca como root con sandbox; típico dentro de un contenedor.
        puppeteer["args"] = ["--no-sandbox", "--disable-dev-shm-usage"]

    OUT_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        puppeteer_config = Path(tmp) / "puppeteer.json"
        puppeteer_config.write_text(json.dumps(puppeteer), encoding="utf-8")
        for name in wanted:
            source = Path(tmp) / f"{name}.mmd"
            source.write_text(diagrams[name], encoding="utf-8")
            target = OUT_DIR / f"{name}.svg"
            # argv fijo: solo intervienen rutas del repo y la versión fijada de mmdc.
            subprocess.run(  # noqa: S603
                [
                    npx, "--yes", f"--package={MMDC_PACKAGE}", "mmdc",
                    "-p", str(puppeteer_config), "-c", str(CONFIG),
                    "-i", str(source), "-o", str(target), "-b", "white",
                ],
                check=True,
                env=env,
            )
            target.write_text(shrink(target.read_text(encoding="utf-8")), encoding="utf-8")
            print(target.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
