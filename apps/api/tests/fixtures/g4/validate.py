"""Carga y validación del dataset G4 (L1.5, U-S1-JL-03).

Uso: `uv run python -m tests.fixtures.g4.validate <archivo.jsonl> [--languages es,en,fr]
[--min-per-language 50] [--min-unanswerable 15]`. Sale con 0 si el dataset cumple, 1 si
tiene problemas y 2 si no puede determinar los idiomas habilitados.

Cada idioma aprueba por separado: un idioma que sobra no compensa a otro que falta. Los
mensajes citan línea, idioma o `id`, nunca el texto de la pregunta.
"""
import argparse
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from tests.fixtures.g4.schema import G4Item


class DatasetFormatError(ValueError):
    """El JSONL no se puede cargar; `problems` trae un mensaje por error."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


def _describe(exc: ValidationError) -> list[str]:
    # include_input=False: el mensaje no debe repetir el contenido de la pregunta.
    problems = []
    for error in exc.errors(include_input=False, include_url=False):
        field = ".".join(str(part) for part in error["loc"]) or "(ítem)"
        problems.append(f"{field}: {error['msg']}")
    return problems


def load(path: Path) -> list[G4Item]:
    """Lee un JSONL de ítems G4; las líneas en blanco se ignoran."""
    items: list[G4Item] = []
    problems: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                items.append(G4Item.model_validate_json(line))
            except ValidationError as exc:
                problems.extend(f"línea {number}: {p}" for p in _describe(exc))
    if problems:
        raise DatasetFormatError(problems)
    return items


def _normalized(question: str) -> str:
    return " ".join(question.split()).casefold()


def validate_dataset(
    items: Sequence[G4Item],
    *,
    languages: Sequence[str],
    min_per_language: int = 50,
    min_unanswerable: int = 15,
) -> list[str]:
    """Devuelve los problemas del dataset; lista vacía significa que cumple."""
    problems: list[str] = []
    enabled = set(languages)
    by_language: dict[str, list[G4Item]] = defaultdict(list)
    for item in items:
        by_language[item.language].append(item)

    for language in sorted(set(by_language) - enabled):
        problems.append(f"idioma no habilitado: {language}")

    for id_, count in sorted(Counter(item.id for item in items).items()):
        if count > 1:
            problems.append(f"id duplicado: {id_} ({count} veces)")

    for language in languages:
        group = by_language.get(language, [])
        if not group:
            problems.append(f"{language}: idioma habilitado sin ítems")
            continue
        if len(group) < min_per_language:
            problems.append(f"{language}: {len(group)} ítems, mínimo {min_per_language}")
        unanswerable = sum(1 for item in group if not item.answerable)
        if unanswerable < min_unanswerable:
            problems.append(
                f"{language}: {unanswerable} no respondibles, mínimo {min_unanswerable}"
            )
        for partition in ("development", "test"):
            if not any(item.partition == partition for item in group):
                problems.append(f"{language}: sin ítems en la partición {partition}")
        seen: dict[str, str] = {}
        for item in group:
            key = _normalized(item.question)
            if key in seen:
                problems.append(f"{language}: pregunta duplicada en {seen[key]} y {item.id}")
            else:
                seen[key] = item.id
    return problems


def _default_languages() -> tuple[str, ...]:
    # Import diferido: `app.core.config` construye Settings al importarse y exige los
    # secretos; con `--languages` el CLI funciona sin configuración.
    from app.core.config import settings

    return settings.ingest_languages


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada del CLI; devuelve el código de salida."""
    parser = argparse.ArgumentParser(description="Valida un dataset G4 en JSONL.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--languages", help="lista separada por comas; por defecto, la de ingesta")
    parser.add_argument("--min-per-language", type=int, default=50)
    parser.add_argument("--min-unanswerable", type=int, default=15)
    args = parser.parse_args(argv)

    if args.languages:
        languages = tuple(lang.strip() for lang in args.languages.split(",") if lang.strip())
    else:
        try:
            languages = _default_languages()
        except ValidationError:
            print(
                "no se pudo leer settings.ingest_languages (configuración incompleta); "
                "pasa --languages",
                file=sys.stderr,
            )
            return 2

    try:
        items = load(args.path)
    except DatasetFormatError as exc:
        problems = exc.problems
    else:
        problems = validate_dataset(
            items,
            languages=languages,
            min_per_language=args.min_per_language,
            min_unanswerable=args.min_unanswerable,
        )

    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"OK: {len(items)} ítems, idiomas {', '.join(languages)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
