"""Materias del usuario (A1.6, M2a): CRUD acotado al tenant de la sesión.

El dueño de cada materia sale siempre de `session.user_id` (estado del servidor); el cuerpo
de la petición nunca lleva `user_id` (`extra="forbid"` lo rechaza con 422).
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.deps import CurrentSession, DbSession, MutationSession
from app.core.errors import ApiError, not_found
from app.models import Subject
from app.services import subjects as subject_service

router = APIRouter(tags=["subjects"])

DEFAULT_COLOR = "#6366f1"  # mismo valor que el server_default de `subjects.color`
COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


class SubjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    color: str = Field(default=DEFAULT_COLOR, pattern=COLOR_PATTERN)
    teacher: str | None = Field(default=None, max_length=200)


class SubjectUpdate(BaseModel):
    """Cambio parcial: solo se modifican los campos enviados; `teacher: null` lo borra."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    teacher: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _requires_a_real_change(self) -> "SubjectUpdate":
        if not self.model_fields_set:
            raise ValueError("envía al menos un campo para cambiar")
        for required in ("name", "color"):  # estas columnas no admiten NULL
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} no puede ser null")
        return self


class SubjectDelete(BaseModel):
    """Cuerpo opcional del `DELETE`. `force` se acepta por contrato pero no tiene efecto en S1."""

    model_config = ConfigDict(extra="forbid")

    force: bool = False


class SubjectOut(BaseModel):
    id: str
    name: str
    color: str
    teacher: str | None
    class_count: int


class SubjectList(BaseModel):
    items: list[SubjectOut]


def _to_out(subject: Subject, class_count: int) -> SubjectOut:
    return SubjectOut(
        id=str(subject.id),
        name=subject.name,
        color=subject.color,
        teacher=subject.teacher,
        class_count=class_count,
    )


@router.get("/subjects")
async def list_subjects(session: CurrentSession, db: DbSession) -> SubjectList:
    rows = await subject_service.list_active_with_class_count(db, session.user_id)
    return SubjectList(items=[_to_out(subject, count) for subject, count in rows])


@router.patch("/subjects/{subject_id}")
async def update_subject(
    subject_id: uuid.UUID, body: SubjectUpdate, session: MutationSession, db: DbSession
) -> SubjectOut:
    subject = await subject_service.get_active(db, session.user_id, subject_id)
    if subject is None:  # inexistente, ajena o borrada: misma respuesta (ADR-0006)
        raise not_found()
    changes = {field: getattr(body, field) for field in body.model_fields_set}
    await subject_service.apply_changes(db, subject, changes)
    count = await subject_service.count_active_classes(db, session.user_id, subject_id)
    out = _to_out(subject, count)
    await db.commit()
    return out


@router.delete("/subjects/{subject_id}", status_code=204)
async def delete_subject(
    subject_id: uuid.UUID,
    session: MutationSession,
    db: DbSession,
    body: SubjectDelete | None = None,  # se valida el cuerpo; `force` aún no cambia nada
) -> Response:
    subject = await subject_service.get_active(db, session.user_id, subject_id)
    if subject is None:
        raise not_found()
    class_count = await subject_service.count_active_classes(db, session.user_id, subject_id)
    if class_count > 0:
        # Nunca se borra en cascada en silencio (S1.md A1.6). `force` queda diferido a S2:
        # borrar clases exige el tombstone completo de `DELETE /audios/{id}`.
        raise ApiError(
            409,
            "subject_has_active_audios",
            "La materia tiene clases activas. Elimina las clases primero; "
            "borrarlas junto con la materia estará disponible más adelante.",
            {"class_count": class_count},
        )
    subject.deleted_at = datetime.now(UTC)  # soft delete: libera el nombre y conserva la fila
    await db.commit()
    return Response(status_code=204)


@router.post("/subjects", status_code=201)
async def create_subject(
    body: SubjectCreate, session: MutationSession, db: DbSession
) -> SubjectOut:
    subject = await subject_service.create(
        db, session.user_id, name=body.name, color=body.color, teacher=body.teacher
    )
    out = _to_out(subject, class_count=0)  # una materia recién creada no tiene clases
    await db.commit()
    return out
