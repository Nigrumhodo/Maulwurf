"""Modelos ORM. Importar este paquete registra todas las tablas en `Base.metadata`."""
from app.models.base import Base
from app.models.core import GoogleCredential, Session, Subject, User
from app.models.ingestion import Audio, IngestionAttempt, OutboxEvent

__all__ = [
    "Audio",
    "Base",
    "GoogleCredential",
    "IngestionAttempt",
    "OutboxEvent",
    "Session",
    "Subject",
    "User",
]
