"""Google Calendar service (J3.2): blocking client, bounded retries, `invalid_grant`.

Design decisions:

- **Blocking client isolated from the event loop.** Google's Calendar client is synchronous.
  The service keeps a sync `httpx.Client` and runs the whole call, including its retry
  sleeps, inside `asyncio.to_thread`, so a slow Calendar response never blocks the loop.
- **Bounded budget.** At most `max_retries + 1` attempts. Only transient failures are
  retried: 408, 429, 5xx, network errors and the 403 quota reasons Google uses. A 401, a
  plain 403, 404, 409 or any other 4xx is terminal.
- **Writes are not retried at transport level.** `POST /calendars` is not idempotent: a
  retry after a lost response could create a second calendar. `create_calendar` opts out of
  the transport retry and lets its caller reconcile by identity before trying again.
- **`Retry-After` wins.** When the header is present (numeric seconds or an HTTP date) it
  replaces the exponential backoff and is capped by `max_retry_after_seconds`; otherwise a
  bounded exponential backoff with multiplicative jitter is used.
- **`invalid_grant` disconnects.** A response whose structured error is `invalid_grant`
  (token endpoint or Calendar body) invokes `on_invalid_grant` once and is never retried.
  A generic 401/403 is not treated as revocation. `disconnect_google_credential` is the
  ready-made callback that flips `google_credentials.status` to `disconnected`.
- **No secrets or payloads in logs.** Only method, status, attempt and delay are logged.
"""

import asyncio
import logging
import secrets
import time
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GoogleCredential

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_API_URL = "https://www.googleapis.com/calendar/v3"
_BACKOFF_BASE_SECONDS = 0.5
# Bounded calendar-list pagination: the Calendar API returns at most 250 entries per page
# and a user has no legitimate reason to exceed a few pages. The bound keeps a corrupt or
# hostile `nextPageToken` from turning one connect into an unbounded loop (guardrail 10).
CALENDAR_LIST_PAGE_SIZE = 250
CALENDAR_LIST_MAX_PAGES = 10
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
# Google reports some transient quota errors as 403. Only these reasons are retryable; a
# permission/forbidden 403 is a configuration or consent problem, not a transient failure.
_TRANSIENT_FORBIDDEN_REASONS = frozenset(
    {
        "backendError",
        "dailyLimitExceeded",
        "quotaExceeded",
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "RESOURCE_EXHAUSTED",
    }
)


class GoogleCalendarError(Exception):
    """Typed Calendar error. `transient` drives retry/blocked decisions in callers."""

    code = "google_calendar_error"
    transient = False
    disconnects_integration = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class GoogleCalendarTransientError(GoogleCalendarError):
    code = "google_calendar_transient_error"
    transient = True


class GoogleCalendarRateLimited(GoogleCalendarTransientError):
    code = "google_calendar_rate_limited"


class GoogleCalendarInvalidGrant(GoogleCalendarError):
    code = "google_calendar_invalid_grant"
    disconnects_integration = True


class GoogleCalendarUnauthorized(GoogleCalendarError):
    code = "google_calendar_unauthorized"


class GoogleCalendarForbidden(GoogleCalendarError):
    code = "google_calendar_forbidden"


class GoogleCalendarConflict(GoogleCalendarError):
    code = "google_calendar_conflict"


class GoogleCalendarNotFound(GoogleCalendarError):
    code = "google_calendar_not_found"


class GoogleCalendarInvalidRequest(GoogleCalendarError):
    code = "google_calendar_invalid_request"


class GoogleCalendarResponseError(GoogleCalendarError):
    code = "google_calendar_response_error"


def _unit_interval() -> float:
    return secrets.randbelow(1_000_000) / 1_000_000


def _backoff(attempt: int, jitter: Callable[[], float]) -> float:
    base = _BACKOFF_BASE_SECONDS * float(1 << (attempt - 1))
    drawn = min(max(jitter(), 0.0), 1.0)
    return base + (drawn * base)


def _retry_after_seconds(response: httpx.Response, *, cap: float) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None  # Malformed header: fall back to our own backoff.
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
        seconds = max(0.0, seconds)
    if seconds < 0:
        return None
    return min(seconds, cap)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_reason(response: httpx.Response) -> str | None:
    payload = _json_object(response)
    error = payload.get("error")
    if isinstance(error, str):
        return error
    if not isinstance(error, dict):
        return None
    reason = error.get("reason")
    if isinstance(reason, str):
        return reason
    for key in ("errors", "details"):
        items = error.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                item_reason = item.get("reason")
                if isinstance(item_reason, str):
                    return item_reason
    status = error.get("status")
    return status if isinstance(status, str) else None


def _is_invalid_grant(response: httpx.Response) -> bool:
    payload = _json_object(response)
    error = payload.get("error")
    if isinstance(error, str):
        return error == "invalid_grant"
    if not isinstance(error, dict):
        return False
    if error.get("reason") == "invalid_grant" or error.get("status") == "invalid_grant":
        return True
    for key in ("errors", "details"):
        items = error.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("reason") == "invalid_grant":
                return True
    return False


def _is_transient_forbidden(response: httpx.Response) -> bool:
    return (
        response.status_code == httpx.codes.FORBIDDEN
        and _error_reason(response) in _TRANSIENT_FORBIDDEN_REASONS
    )


async def disconnect_google_credential(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Mark the user's Google integration `disconnected` after `invalid_grant`.

    Idempotent and tenant-scoped by the primary key (`user_id`). The caller owns the
    transaction; this helper only flushes so a later failure can still roll it back.
    """
    credential = await db.get(GoogleCredential, user_id)
    if credential is None:
        return False
    credential.status = "disconnected"
    await db.flush()
    return True


class GoogleCalendarService:
    """Minimal blocking Calendar client consumed by J3.3–J3.7."""

    def __init__(
        self,
        *,
        access_token: str,
        base_url: str = GOOGLE_CALENDAR_API_URL,
        timeout_seconds: float = 15.0,
        max_retries: int = 3,
        max_retry_after_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = _unit_interval,
        on_invalid_grant: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("access_token must not be empty")
        self._access_token = access_token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_attempts = max_retries + 1
        self._max_retry_after = max_retry_after_seconds
        self._transport = transport
        self._sleep = sleeper
        self._jitter = jitter
        self._on_invalid_grant = on_invalid_grant

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        retry_transient: bool = True,
    ) -> dict[str, Any]:
        """Run one Calendar request in a worker thread and return its JSON object.

        `retry_transient=False` makes the call single-attempt. It is required for
        non-idempotent writes such as `POST /calendars`: a retry after a lost response can
        create a second remote object, so only the caller may reconcile and decide.
        """
        try:
            return await asyncio.to_thread(
                self._request, method, path, params, json, retry_transient
            )
        except GoogleCalendarInvalidGrant:
            if self._on_invalid_grant is not None:
                await self._on_invalid_grant()
            raise

    def update_access_token(self, access_token: str) -> None:
        """Rotate the bearer token after a job refreshed it (call between requests)."""
        if not access_token:
            raise ValueError("access_token must not be empty")
        self._access_token = access_token

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
        json_body: Mapping[str, Any] | None,
        retry_transient: bool = True,
    ) -> dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        attempt = 0
        max_attempts = self._max_attempts if retry_transient else 1
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            while True:
                attempt += 1
                try:
                    response = client.request(
                        method, url, params=params, json=json_body, headers=headers
                    )
                except httpx.HTTPError as exc:
                    if attempt >= max_attempts:
                        raise GoogleCalendarTransientError(
                            "Google Calendar no respondió.",
                            details={"attempts": attempt, "error": type(exc).__name__},
                        ) from exc
                    self._pause(attempt, None)
                    continue
                if response.status_code in _RETRYABLE_STATUS or _is_transient_forbidden(
                    response
                ):
                    if attempt >= max_attempts:
                        raise self._exhausted(response, attempt)
                    retry_after = _retry_after_seconds(
                        response, cap=self._max_retry_after
                    )
                    self._pause(attempt, retry_after)
                    continue
                if response.status_code >= httpx.codes.BAD_REQUEST:
                    raise self._client_error(response, attempt)
                return self._parse(response)

    async def list_calendars(self) -> list[dict[str, Any]]:
        """Every entry of the user's calendar list, following pagination up to the cap."""
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        for _ in range(CALENDAR_LIST_MAX_PAGES):
            params: dict[str, Any] = {
                "maxResults": CALENDAR_LIST_PAGE_SIZE,
                "showDeleted": "false",
            }
            if page_token is not None:
                params["pageToken"] = page_token
            payload = await self.request("GET", "users/me/calendarList", params=params)
            page_items = payload.get("items")
            if isinstance(page_items, list):
                items.extend(item for item in page_items if isinstance(item, dict))
            next_token = payload.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return items
            page_token = next_token
        logger.warning("google_calendar.list_truncated pages=%d", CALENDAR_LIST_MAX_PAGES)
        # Why: a silently partial list could hide the existing "Maulwurf" calendar and make
        # reconciliation create a duplicate. Fail closed so the caller surfaces a retryable
        # error instead of breaking the single-calendar invariant.
        raise GoogleCalendarTransientError(
            "Google Calendar devolvió más páginas de calendarios de las permitidas.",
            details={"pages": CALENDAR_LIST_MAX_PAGES},
        )

    async def get_calendar(self, calendar_id: str) -> dict[str, Any]:
        """One calendar by ID; a deleted or unknown ID raises `GoogleCalendarNotFound`."""
        path = f"calendars/{urllib.parse.quote(calendar_id, safe='')}"
        return await self.request("GET", path)

    async def create_calendar(
        self, *, summary: str, description: str, timezone: str
    ) -> dict[str, Any]:
        """Create one secondary calendar owned by the authenticated user."""
        payload = await self.request(
            "POST",
            "calendars",
            json={"summary": summary, "description": description, "timeZone": timezone},
            # Why: Calendar does not make writes idempotent. A transport-level retry after a
            # lost response would create a second calendar; `binding_from_google` re-lists
            # by identity instead and only creates again if the first attempt really failed.
            retry_transient=False,
        )
        calendar_id = payload.get("id")
        if not isinstance(calendar_id, str) or not calendar_id:
            raise GoogleCalendarResponseError(
                "Google Calendar creó un calendario sin identificador.",
                details={"reason": "missing_calendar_id"},
            )
        return payload

    def _pause(self, attempt: int, retry_after: float | None) -> None:
        delay = retry_after if retry_after is not None else _backoff(attempt, self._jitter)
        logger.warning(
            "google_calendar.retry attempt=%d delay=%.2f", attempt, delay
        )
        self._sleep(delay)

    def _exhausted(self, response: httpx.Response, attempt: int) -> GoogleCalendarError:
        details = self._details(response, attempt)
        retry_after = _retry_after_seconds(response, cap=self._max_retry_after)
        if retry_after is not None:
            details["retry_after"] = retry_after
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS or _is_transient_forbidden(
            response
        ):
            return GoogleCalendarRateLimited(
                "Google Calendar rechazó la petición por cuota.", details=details
            )
        return GoogleCalendarTransientError(
            "Google Calendar devolvió un error temporal.", details=details
        )

    def _client_error(self, response: httpx.Response, attempt: int) -> GoogleCalendarError:
        details = self._details(response, attempt)
        status = response.status_code
        if _is_invalid_grant(response):
            return GoogleCalendarInvalidGrant(
                "Google revocó el acceso (invalid_grant); la integración queda desconectada.",
                details=details,
            )
        if status == httpx.codes.UNAUTHORIZED:
            return GoogleCalendarUnauthorized(
                "Google Calendar rechazó las credenciales.", details=details
            )
        if status == httpx.codes.FORBIDDEN:
            return GoogleCalendarForbidden(
                "Google Calendar rechazó la operación por permisos.", details=details
            )
        if status == httpx.codes.NOT_FOUND:
            return GoogleCalendarNotFound(
                "El recurso de Google Calendar no existe.", details=details
            )
        if status == httpx.codes.CONFLICT:
            return GoogleCalendarConflict(
                "Google Calendar reportó un conflicto de estado.", details=details
            )
        return GoogleCalendarInvalidRequest(
            "Google Calendar rechazó la petición.", details=details
        )

    @staticmethod
    def _details(response: httpx.Response, attempt: int) -> dict[str, Any]:
        details: dict[str, Any] = {
            "status": response.status_code,
            "attempts": attempt,
        }
        reason = _error_reason(response)
        if reason is not None:
            details["reason"] = reason
        return details

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        if response.status_code == httpx.codes.NO_CONTENT:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise GoogleCalendarResponseError(
                "Google Calendar devolvió una respuesta no JSON.",
                details={"status": response.status_code},
            ) from exc
        if not isinstance(payload, dict):
            raise GoogleCalendarResponseError(
                "Google Calendar devolvió una respuesta con forma inesperada.",
                details={"status": response.status_code},
            )
        return payload
