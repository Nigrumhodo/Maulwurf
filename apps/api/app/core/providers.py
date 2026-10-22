"""Provider dependencies shared by the API routers (L2.4/L2.5).

The embeddings and LLM providers are optional: without their `MAULWURF_*` base URL and API
key the dependency returns `None` instead of failing at import time. The caller decides
whether that is an empty search (no active index) or a typed `503 provider_unavailable` (an
index exists but cannot be queried) / a terminal SSE `provider_unavailable` event.
"""

from typing import Annotated

from fastapi import Depends

from app.services.embeddings import (
    EmbeddingNotConfigured,
    EmbeddingProvider,
    HttpEmbeddingProvider,
)
from app.services.llm import HttpChatProvider, LLMNotConfigured, LLMProvider


def get_embedding_provider() -> EmbeddingProvider | None:
    """Return the configured provider or `None`; never a fake/default provider."""
    try:
        return HttpEmbeddingProvider.from_settings()
    except EmbeddingNotConfigured:
        return None


def get_llm_provider() -> LLMProvider | None:
    """Return the configured streaming LLM or `None`; never a fake completion."""
    try:
        return HttpChatProvider.from_settings()
    except LLMNotConfigured:
        return None


EmbeddingProviderDep = Annotated[EmbeddingProvider | None, Depends(get_embedding_provider)]
LLMProviderDep = Annotated[LLMProvider | None, Depends(get_llm_provider)]
