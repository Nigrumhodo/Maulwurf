"""Modelos ORM. Importar este paquete registra todas las tablas en `Base.metadata`."""

from app.models.base import Base
from app.models.calendar_event import CalendarEvent
from app.models.chat import Conversation, Message, MessageSource
from app.models.chunk import Chunk, ChunkSegment, Embedding, IndexGeneration
from app.models.core import GoogleCredential, Session, Subject, User
from app.models.ingestion import Audio, IngestionAttempt, OutboxEvent, VariantConfirmationToken
from app.models.processing_run import ProcessingRun
from app.models.settings import CalendarSettings
from app.models.task import Task, TaskRevision, TaskSource
from app.models.transcript import Segment, Transcript

__all__ = [
    "Audio",
    "Base",
    "CalendarEvent",
    "CalendarSettings",
    "Chunk",
    "ChunkSegment",
    "Conversation",
    "Embedding",
    "GoogleCredential",
    "IngestionAttempt",
    "IndexGeneration",
    "Message",
    "MessageSource",
    "OutboxEvent",
    "ProcessingRun",
    "Segment",
    "Session",
    "Subject",
    "Task",
    "TaskRevision",
    "TaskSource",
    "Transcript",
    "User",
    "VariantConfirmationToken",
]
