"""`GET /search` (A2.11, S2.md §1.3 and DISENO §3.9).

The router only validates the HTTP shape, calls the reusable `SearchService` and maps
typed domain errors to the API envelope. SQL, RRF and hydration live in `services.search`
(ADR-0006). The response is always `Cache-Control: no-store`: it carries tenant-owned
transcript snippets.
"""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.core.deps import CurrentSession, DbSession
from app.core.errors import ApiError, validation_failed
from app.core.providers import EmbeddingProviderDep
from app.core.redis import RedisDep
from app.schemas.search import SearchResponseOut, SearchResultOut, SearchSubjectOut
from app.services.embeddings import (
    EmbeddingDimensionMismatch,
    EmbeddingInputTooLong,
    EmbeddingNotConfigured,
    EmbeddingProviderError,
    EmbeddingRateLimited,
)
from app.services.search import (
    _CURSOR_INVALID,
    CURSOR_MAX_LENGTH,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    QUERY_MAX_CHARS,
    SearchHnswUnsupported,
    SearchInvalidCursor,
    SearchInvalidQuery,
    SearchInvalidRange,
    SearchNotConfigured,
    SearchProviderMismatch,
    SearchService,
    SearchVectorRequired,
)
from app.services.search_quota import enforce_search_quota

router = APIRouter(tags=["search"])


def _fields(exc: Any, fallback: str, *, field: str = "q") -> dict[str, str]:
    details = getattr(exc, "details", {})
    fields = details.get("fields") if isinstance(details, dict) else None
    if not isinstance(fields, dict) or not fields:
        return {field: fallback}
    return {str(key): str(value) for key, value in fields.items()}


async def _search_quota(session: CurrentSession, redis: RedisDep) -> None:
    await enforce_search_quota(redis, session.user_id)


SearchQuota = Annotated[None, Depends(_search_quota)]


def _provider_unavailable() -> ApiError:
    return ApiError(
        503,
        "provider_unavailable",
        "El proveedor de embeddings no está disponible.",
        {"provider": "embeddings"},
    )


@router.get(
    "/search",
    responses={
        200: {"description": "Resultados híbridos paginados con snippet y tiempos honestos"},
        401: {"description": "Sin sesión (auth_required)"},
        422: {"description": "validation_failed: q, limit, fechas o cursor inválidos"},
        429: {
            "description": (
                "rate_limited: cuota por usuario o del proveedor de embeddings, "
                "con Retry-After"
            )
        },
        500: {"description": "internal_error | search_hnsw_unsupported"},
        503: {
            "description": (
                "provider_unavailable: embeddings sin configurar o caídos | "
                "capacity_unavailable: limitador de búsquedas no disponible"
            )
        },
    },
)
async def search(
    q: Annotated[str, Query(min_length=1, max_length=QUERY_MAX_CHARS)],
    response: Response,
    session: CurrentSession,
    db: DbSession,
    provider: EmbeddingProviderDep,
    _quota: SearchQuota,
    subject_id: Annotated[uuid.UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)] = None,
) -> SearchResponseOut:
    """Hybrid search over the session user's active index generation.

    The per-user Redis quota runs as a dependency before this handler, so an over-quota
    request never reaches the embeddings provider. A foreign or unknown `subject_id` is an
    empty result, never a `404` (ADR-0006). Without an active generation the answer is `200`
    with no results and the provider is not called.
    """
    response.headers["Cache-Control"] = "no-store"
    service = SearchService(db, provider=provider)
    try:
        page = await service.page(
            user_id=session.user_id,
            query=q,
            subject_id=subject_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            cursor=cursor,
        )
    except SearchInvalidQuery as exc:
        raise validation_failed(_fields(exc, "consulta inválida")) from exc
    except SearchInvalidRange as exc:
        raise validation_failed(_fields(exc, "rango de fechas inválido")) from exc
    except SearchInvalidCursor as exc:
        raise validation_failed(_fields(exc, _CURSOR_INVALID, field="cursor")) from exc
    except EmbeddingInputTooLong as exc:
        raise validation_failed({"q": "consulta demasiado larga"}) from exc
    except EmbeddingNotConfigured as exc:
        raise ApiError(
            503,
            "provider_unavailable",
            "El proveedor de embeddings no está configurado.",
            {"provider": "embeddings", "reason": "not_configured"},
        ) from exc
    except EmbeddingRateLimited as exc:
        headers: dict[str, str] = {}
        retry_after = exc.details.get("retry_after")
        if isinstance(retry_after, (int, float)):
            headers["Retry-After"] = str(max(0, int(retry_after)))
        raise ApiError(
            429,
            "rate_limited",
            "El proveedor de embeddings rechazó la petición por cuota.",
            {"provider": "embeddings"},
            headers=headers,
        ) from exc
    except EmbeddingProviderError as exc:
        raise _provider_unavailable() from exc
    except (EmbeddingDimensionMismatch, SearchProviderMismatch) as exc:
        # Configuration drift: never expose expected/observed dimensions or model names.
        raise ApiError(
            500,
            "internal_error",
            "El índice activo no es compatible con el proveedor configurado.",
        ) from exc
    except SearchHnswUnsupported as exc:
        raise ApiError(
            500,
            "search_hnsw_unsupported",
            "La versión de pgvector no soporta la búsqueda filtrada requerida.",
            {"reason": "iterative_scan_unavailable"},
        ) from exc
    except SearchVectorRequired as exc:
        raise ApiError(
            500, "internal_error", "No se pudo construir el vector de consulta."
        ) from exc
    except SearchNotConfigured:
        # A generation disappeared between resolution and retrieval: same honest empty page
        # as a user without an active index.
        return SearchResponseOut(results=[], took_ms=0, next_cursor=None)
    return SearchResponseOut(
        results=[
            SearchResultOut(
                chunk_id=str(result.chunk_id),
                audio_id=str(result.audio_id),
                subject=SearchSubjectOut(
                    id=str(result.subject_id),
                    name=result.subject_name,
                    color=result.subject_color,
                ),
                class_date=result.class_date,
                snippet=result.snippet,
                segment_id=str(result.segment_id) if result.segment_id is not None else None,
                t_start=result.t_start,
                t_end=result.t_end,
                score_rrf=result.score_rrf,
            )
            for result in page.results
        ],
        took_ms=page.took_ms,
        next_cursor=page.next_cursor,
    )
