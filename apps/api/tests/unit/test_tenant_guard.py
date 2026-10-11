"""Guardia estática de ADR-0006 sobre `app/routers` (A1.9, sin BD).

Los routers no construyen ni ejecutan consultas: toda lectura de una tabla de propiedad pasa
por `app.services.tenant` con el `user_id` de la sesión. Las únicas lecturas por PK
permitidas son `User` y `GoogleCredential`, cuya PK es el propio `user_id` de la sesión.
Excepción acotada: la sonda de readiness puede ejecutar `text("SELECT 1")`, que no toca
ninguna tabla.
"""
import ast
from pathlib import Path

import pytest

import app.routers

ROUTERS_DIR = Path(app.routers.__file__).parent
# Constructores de consultas de SQLAlchemy que un router no debe importar.
QUERY_CONSTRUCTORS = frozenset(
    {
        "select", "insert", "update", "delete", "union", "union_all", "exists",
        "Select", "Insert", "Update", "Delete", "TextClause", "literal_column",
    }
)
EXECUTION_METHODS = frozenset(
    {"execute", "scalars", "scalar", "stream", "stream_scalars", "exec_driver_sql"}
)
PK_READABLE_MODELS = frozenset({"User", "GoogleCredential"})
READINESS_PROBE_SQL = "SELECT 1"


def _is_readiness_probe(node: ast.expr) -> bool:
    """`text("SELECT 1")`: la única SQL literal que un router puede ejecutar."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "text"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == READINESS_PROBE_SQL
    )


def _model_names(tree: ast.Module) -> set[str]:
    """Nombres locales con los que el archivo importa modelos de `app.models`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.models"):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def find_violations(source: str, filename: str = "<router>") -> list[str]:
    """Infracciones de ADR-0006 en el código de un router, como `archivo:línea: motivo`."""
    tree = ast.parse(source, filename=filename)
    models = _model_names(tree)
    found: list[str] = []

    def report(node: ast.AST, reason: str) -> None:
        found.append(f"{filename}:{getattr(node, 'lineno', 0)}: {reason}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "sqlalchemy":
                    report(node, f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlalchemy"):
            for alias in node.names:
                if alias.name in QUERY_CONSTRUCTORS or alias.name == "*":
                    report(node, f"importa {alias.name} de {node.module}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "text" and not _is_readiness_probe(node):
                report(node, "SQL literal con text(...)")
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr in EXECUTION_METHODS and not (
                len(node.args) == 1 and _is_readiness_probe(node.args[0])
            ):
                report(node, f"llamada directa a .{func.attr}(...)")
            if (
                func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in models
                and node.args[0].id not in PK_READABLE_MODELS
            ):
                report(node, f".get({node.args[0].id}, ...) sin filtro de tenant")
    return found


def _router_files() -> list[Path]:
    return sorted(ROUTERS_DIR.glob("*.py"))


def test_router_directory_is_scanned() -> None:
    names = {path.name for path in _router_files()}
    assert {"audios.py", "me.py", "integrations.py"} <= names


@pytest.mark.parametrize("path", _router_files(), ids=lambda p: p.name)
def test_routers_respect_adr_0006(path: Path) -> None:
    assert find_violations(path.read_text(encoding="utf-8"), path.name) == []


@pytest.mark.parametrize(
    "source",
    [
        "from sqlalchemy import select\n",
        "from sqlalchemy.sql import update\n",
        "import sqlalchemy\n",
        "async def f(db):\n    await db.execute(q)\n",
        "async def f(db):\n    await db.scalars(q)\n",
        "async def f(db):\n    await db.scalar(q)\n",
        "async def f(db):\n    await db.execute(text('SELECT * FROM subjects'))\n",
        "from app.models import Subject\nasync def f(db, i):\n    await db.get(Subject, i)\n",
        "from app.models import Audio as A\nasync def f(db, i):\n    await db.get(A, i)\n",
    ],
)
def test_guard_flags_direct_queries(source: str) -> None:
    # Control negativo permanente: la guardia sí detecta cada forma prohibida.
    assert find_violations(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "from sqlalchemy import text\nasync def f(c):\n    await c.execute(text('SELECT 1'))\n",
        "from app.models import User\nasync def f(db, s):\n    await db.get(User, s.user_id)\n",
        "from app.models import GoogleCredential\n"
        "async def f(db, s):\n    await db.get(GoogleCredential, s.user_id)\n",
        "async def f(request):\n    request.cookies.get('mw_session')\n",
        "from app.services.tenant import get_owned\n"
        "async def f(db, m, u, i):\n    await get_owned(db, m, u, i)\n",
    ],
)
def test_guard_allows_sanctioned_access(source: str) -> None:
    assert find_violations(source) == []
