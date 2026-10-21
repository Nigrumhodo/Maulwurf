"""Provider dependencies shared by the API routers (L2.4/L2.5).

The embeddings provider is optional: without `MAULWURF_EMBEDDING_BASE_URL` and
`MAULWURF_EMBEDDING_API_KEY` the dependency returns `None` instead of failing at import
time. The caller decides whether that is an empty search (no active index) or a typed
`503 provider_unavailable` (an index exists but cannot be queried).
"""

from typing import Annotated

from fastapi import Depends

from app.services.embeddings import (
    EmbeddingNotConfigured,
    EmbeddingProvider,
    HttpEmbeddingProvider,
)


def get_embedding_provider() -> EmbeddingProvider | None:
    """Return the configured provider or `None`; never a fake/default provider."""
    try:
        return HttpEmbeddingProvider.from_settings()
    except EmbeddingNotConfigured:
        return None


EmbeddingProviderDep = Annotated[EmbeddingProvider | None, Depends(get_embedding_provider)]
