"""Modelos ORM. Importar este paquete registra todas las tablas en `Base.metadata`."""

from app.models.base import Base
from app.models.chat import Conversation, Message, MessageSource
from app.models.chunk import Chunk, ChunkSegment, Embedding, IndexGeneration
from app.models.core import GoogleCredential, Session, Subject, User
from app.models.ingestion import Audio, IngestionAttempt, OutboxEvent, VariantConfirmationToken
from app.models.transcript import ProcessingRun, Segment, Transcript

__all__ = [
    "Audio",
    "Base",
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
    "User",
    "Transcript",
    "VariantConfirmationToken",
]
