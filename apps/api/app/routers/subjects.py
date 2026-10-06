"""Materias del usuario (A1.6, M2a): CRUD acotado al tenant de la sesión.

El dueño de cada materia sale siempre de `session.user_id` (estado del servidor); el cuerpo
de la petición nunca lleva `user_id` (`extra="forbid"` lo rechaza con 422).
"""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.core.deps import DbSession, MutationSession
from app.models import Subject

router = APIRouter(tags=["subjects"])

DEFAULT_COLOR = "#6366f1"  # mismo valor que el server_default de `subjects.color`
COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


class SubjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    color: str = Field(default=DEFAULT_COLOR, pattern=COLOR_PATTERN)
    teacher: str | None = Field(default=None, max_length=200)


class SubjectOut(BaseModel):
    id: str
    name: str
    color: str
    teacher: str | None
    class_count: int


def _to_out(subject: Subject, class_count: int) -> SubjectOut:
    return SubjectOut(
        id=str(subject.id),
        name=subject.name,
        color=subject.color,
        teacher=subject.teacher,
        class_count=class_count,
    )


@router.post("/subjects", status_code=201)
async def create_subject(
    body: SubjectCreate, session: MutationSession, db: DbSession
) -> SubjectOut:
    subject = Subject(
        user_id=session.user_id, name=body.name, color=body.color, teacher=body.teacher
    )
    db.add(subject)
    await db.flush()  # la BD asigna el id; aún no es definitivo hasta el commit
    out = _to_out(subject, class_count=0)  # una materia recién creada no tiene clases
    await db.commit()
    return out
