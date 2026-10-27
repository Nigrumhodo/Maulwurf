#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Build the Workshop 2 deliverables: PlantUML sources to SVG, and every section into one PDF.

Usage:
    uv run Workshop-2/build.py            # diagrams + PDF
    uv run Workshop-2/build.py diagrams   # only the SVGs
    uv run Workshop-2/build.py pdf        # only the PDF (uses the committed SVGs)

Requirements:
    PLANTUML_JAR  path to plantuml-mit-1.2026.8.jar (Java 17+ on PATH)
    CHROME_PATH   path to Chrome or Chromium (headless printing)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_URL = "https://github.com/Nigrumhodo/Maulwurf/blob/develop"
PLANTUML_SHA256 = "3629c9cd017c7f73e6450396eea0040216c7e1eef8473ce33cc1aad469dab2f9"
SECTIONS = (
    "README.md",
    "01-system-domain.md",
    "02-crc-cards.md",
    "03-class-diagram.md",
    "04-relational-model.md",
    "05-mockups.md",
    "06-business-processes.md",
    "07-architecture.md",
)
PDF_NAME = "Workshop-2.pdf"

CSS = """
@page { size: A4; margin: 16mm 14mm 18mm 14mm;
        @bottom-center { content: counter(page); font: 9pt "Noto Sans", sans-serif; } }
@page landscape { size: A4 landscape; margin: 12mm; }
body { font: 10.5pt/1.45 "Noto Sans", sans-serif; color: #1a1a1a; }
section { break-before: page; }
section:first-of-type { break-before: auto; }
h1 { font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: 4px; }
h2 { font-size: 14pt; margin-top: 18px; break-after: avoid; }
h3 { font-size: 12pt; break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9.5pt; }
th, td { border: 1px solid #999; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #eee; }
tr { break-inside: avoid; }
code { font-family: "Noto Sans Mono", monospace; font-size: 9pt; background: #f3f3f3;
       padding: 0 2px; }
pre { background: #f3f3f3; padding: 6px; white-space: pre-wrap; font-size: 8.5pt; }
img { max-width: 100%; max-height: 235mm; display: block; margin: 6px auto; }
p.figure { break-inside: avoid; text-align: center; }
p.landscape { page: landscape; break-before: page; break-after: page; }
p.landscape img { max-height: 180mm; }
blockquote { border-left: 3px solid #999; margin: 8px 0; padding: 2px 10px; color: #444; }
.crc table { width: 100%; }
"""


def render_diagrams() -> None:
    jar = os.environ.get("PLANTUML_JAR")
    if not jar or not Path(jar).is_file():
        sys.exit("set PLANTUML_JAR to plantuml-mit-1.2026.8.jar")
    digest = hashlib.sha256(Path(jar).read_bytes()).hexdigest()
    if digest != PLANTUML_SHA256:
        sys.exit(f"unexpected PlantUML jar (sha256 {digest}); use plantuml-mit-1.2026.8.jar")
    sources = sorted([*HERE.glob("diagrams/*.puml"), *HERE.glob("mockups/*.puml")])
    subprocess.run(
        ["java", "-Djava.awt.headless=true", "-jar", jar, "-tsvg", "-failfast2",
         *map(str, sources)],
        check=True,
    )
    for source in sources:
        print(source.with_suffix(".svg").relative_to(HERE))


def _is_wide(svg: Path) -> bool:
    match = re.search(r'viewBox="[\d.\s-]*?([\d.]+)\s+([\d.]+)"', svg.read_text(encoding="utf-8"))
    return bool(match) and float(match.group(1)) > 1.35 * float(match.group(2))


def _inline(value: str) -> str:
    """Convert the small Markdown inline subset used by the workshop documents."""
    value = html.escape(value, quote=False)
    value = re.sub(r'!\[([^]]*)\]\(([^)]+)\)', r'<img alt="\1" src="\2">', value)
    value = re.sub(r'\[([^]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', value)
    value = re.sub(r'`([^`]+)`', r'<code>\1</code>', value)
    value = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', value)
    value = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', value)
    return value


def _markdown_html(text: str) -> str:
    """Render headings, tables, lists, code blocks, links and images without third-party packages."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    in_list = False
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            language = line[3:].strip()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            out.append(f'<pre><code class="language-{html.escape(language)}">{html.escape(chr(10).join(code))}</code></pre>')
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            if in_list:
                out.append("</ul>"); in_list = False
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2).rstrip('#').strip())}</h{level}>")
            i += 1
            continue
        if re.match(r"^\s*[-*_]{3,}\s*$", line):
            out.append("<hr>"); i += 1; continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$", lines[i + 1]):
            rows: list[list[str]] = []
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append(header); i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")]); i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in rows[0]) + "</tr></thead><tbody>")
            out.extend("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in rows[1:])
            out.append("</tbody></table>")
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet:
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline(bullet.group(1))}</li>"); i += 1; continue
        if in_list:
            out.append("</ul>"); in_list = False
        if line.startswith("> "):
            quote: list[str] = []
            while i < len(lines) and lines[i].startswith("> "):
                quote.append(lines[i][2:]); i += 1
            out.append(f"<blockquote>{_inline(' '.join(quote))}</blockquote>"); continue
        if not line.strip():
            i += 1; continue
        paragraph = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "```", "> ", "| ", "- ", "* ")):
            paragraph.append(lines[i]); i += 1
        out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _section_html(name: str) -> str:
    text = (HERE / name).read_text(encoding="utf-8")
    body = _markdown_html(text)

    def link(match: re.Match[str]) -> str:
        target = match.group(1)
        if re.match(r"[a-z]+:|#", target):
            return match.group(0)
        path, _, fragment = target.partition("#")
        if path in SECTIONS:
            return f'href="#{Path(path).stem}"'
        resolved = (HERE / path).resolve()
        try:
            relative = resolved.relative_to(HERE.parent).as_posix()
        except ValueError:
            return match.group(0)
        suffix = f"#{fragment}" if fragment else ""
        return f'href="{REPO_URL}/{relative}{suffix}"'

    body = re.sub(r'href="([^"]+)"', link, body)

    def figure(match: re.Match[str]) -> str:
        src = match.group(1)
        css = "figure landscape" if src.endswith(".svg") and _is_wide(HERE / src) else "figure"
        return f'<p class="{css}">{match.group(0)[3:-4]}</p>'

    body = re.sub(r'<p><img [^>]*src="([^"]+)"[^>]*/?></p>', figure, body)
    return f'<section id="{Path(name).stem}">{body}</section>'


def build_pdf() -> None:
    chrome = os.environ.get("CHROME_PATH")
    if not chrome or not Path(chrome).is_file():
        sys.exit("set CHROME_PATH to a Chrome or Chromium binary")
    sections = "\n".join(_section_html(name) for name in SECTIONS)
    try:
        commit = subprocess.run(
            ["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    stamp = f"Built {dt.date.today().isoformat()} from commit {html.escape(commit)}."
    document = (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<base href="{HERE.as_uri()}/"><title>Maulwurf — Workshop 2</title>'
        f"<style>{CSS}</style></head><body>{sections}"
        f'<p style="font-size:8pt;color:#666">{stamp}</p></body></html>'
    )
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "workshop-2.html"
        page.write_text(document, encoding="utf-8")
        argv = [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                f"--print-to-pdf={HERE / PDF_NAME}", page.as_uri()]
        if getattr(os, "geteuid", lambda: 1)() == 0:
            # Chrome refuses to start as root with its sandbox (typical inside containers).
            argv.insert(1, "--no-sandbox")
        subprocess.run(argv, check=True, capture_output=True)
    print(PDF_NAME)


def main(argv: list[str]) -> int:
    steps = argv or ["diagrams", "pdf"]
    unknown = set(steps) - {"diagrams", "pdf"}
    if unknown:
        sys.exit(f"unknown step(s): {', '.join(sorted(unknown))}")
    if "diagrams" in steps:
        render_diagrams()
    if "pdf" in steps:
        build_pdf()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
