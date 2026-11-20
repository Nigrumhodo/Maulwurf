"""RFC 2822 MIME + escaped HTML templates for M8 Gmail notifications (J4.1).

Pure formatting core: no database I/O, no provider HTTP calls, no secrets. All dynamic
strings interpolated into HTML are escaped via ``html.escape`` and header values reject
control / CR / LF characters so header injection fails closed with ``template_invalid``.
Never includes raw class transcripts or audio metadata.
"""

import base64
import html
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Final

from app.models.notification import DIGEST_TYPES, REMINDER_TYPES
from app.services.notifications import NotificationDeliveryRejected

TEMPLATE_INVALID: Final = "template_invalid"

_REMINDER_WINDOW_LABELS: Final[dict[str, str]] = {
    "reminder_t48": "48 horas antes",
    "reminder_t24": "24 horas antes",
    "reminder_t2": "2 horas antes",
    "reminder_t3d": "3 días antes (09:00)",
    "reminder_t1d": "1 día antes (09:00)",
}


@dataclass(frozen=True)
class DigestItemView:
    """Redacted summary item shown in a daily/weekly digest email."""

    titulo: str
    tipo: str
    due_label: str


@dataclass(frozen=True)
class RenderedEmail:
    """Rendered notification email ready for Gmail ``users.messages.send``."""

    subject: str
    text_body: str
    html_body: str
    raw_b64url: str


def _require_safe_header_text(value: str, *, field: str, allow_empty: bool = False) -> str:
    if "\r" in value or "\n" in value or "\x00" in value:
        raise NotificationDeliveryRejected(TEMPLATE_INVALID)
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise NotificationDeliveryRejected(TEMPLATE_INVALID)
    return cleaned


def _format_due(due_at: datetime | None, due_date: date | None) -> str:
    if due_at is not None:
        return due_at.strftime("%Y-%m-%d %H:%M %Z").strip()
    if due_date is not None:
        return due_date.isoformat()
    return "sin fecha"


def build_mime_message(
    *, recipient: str, subject: str, text_body: str, html_body: str
) -> RenderedEmail:
    """Encode an RFC 2822 multipart/alternative message as base64url for Gmail."""
    safe_recipient = _require_safe_header_text(recipient, field="recipient")
    _, parsed_addr = parseaddr(safe_recipient)
    if not parsed_addr or parsed_addr != safe_recipient or "@" not in parsed_addr:
        raise NotificationDeliveryRejected(TEMPLATE_INVALID)
    local_part, _, domain_part = parsed_addr.partition("@")
    if not local_part or not domain_part or "." not in domain_part:
        raise NotificationDeliveryRejected(TEMPLATE_INVALID)
    safe_subject = _require_safe_header_text(subject, field="subject")
    if not text_body.strip() or not html_body.strip():
        raise NotificationDeliveryRejected(TEMPLATE_INVALID)

    msg = EmailMessage()
    msg["To"] = safe_recipient
    msg["Subject"] = safe_subject
    msg.set_content(text_body, subtype="plain", charset="utf-8")
    msg.add_alternative(html_body, subtype="html", charset="utf-8")
    raw_b64url = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
    return RenderedEmail(
        subject=safe_subject,
        text_body=text_body,
        html_body=html_body,
        raw_b64url=raw_b64url,
    )


def render_notification_email(
    *,
    recipient: str,
    notification_type: str,
    occurrence: date | None = None,
    task_title: str | None = None,
    task_tipo: str | None = None,
    due_at: datetime | None = None,
    due_date: date | None = None,
    digest_items: tuple[DigestItemView, ...] = (),
) -> RenderedEmail:
    """Render one notification type into plain-text + escaped HTML + base64url MIME."""
    if notification_type == "test":
        subject = "[Maulwurf] Prueba de notificaciones Gmail"
        text_body = (
            "Maulwurf — Prueba de notificaciones\n\n"
            "Tu opt-in de Gmail y el permiso gmail.send están activos.\n"
            "Gmail no garantiza entrega exactamente una vez; los envíos inciertos\n"
            "quedan auditados en Ajustes sin reintentos automáticos."
        )
        html_body = (
            "<html><body>"
            "<h2>Maulwurf — Prueba de notificaciones</h2>"
            "<p>Tu opt-in de Gmail y el permiso <code>gmail.send</code> están activos.</p>"
            "<p>Gmail no garantiza entrega exactamente una vez; los envíos inciertos "
            "quedan auditados en Ajustes sin reintentos automáticos.</p>"
            "</body></html>"
        )
        return build_mime_message(
            recipient=recipient, subject=subject, text_body=text_body, html_body=html_body
        )

    if notification_type in REMINDER_TYPES:
        raw_title = _require_safe_header_text(task_title or "", field="task_title")
        safe_title_inline = " ".join(raw_title.split())
        raw_tipo = _require_safe_header_text(task_tipo or "tarea", field="task_tipo")
        window_label = _REMINDER_WINDOW_LABELS.get(notification_type, "recordatorio")
        due_label = _format_due(due_at, due_date)
        subject = f"[Maulwurf] Recordatorio ({raw_tipo}): {safe_title_inline}"[:200]
        text_body = (
            f"Maulwurf — Recordatorio ({window_label})\n\n"
            f"Tipo: {raw_tipo}\n"
            f"Título: {raw_title}\n"
            f"Fecha límite: {due_label}\n"
        )
        html_body = (
            "<html><body>"
            f"<h2>Recordatorio ({html.escape(window_label, quote=True)})</h2>"
            f"<p><strong>Tipo:</strong> {html.escape(raw_tipo, quote=True)}</p>"
            f"<p><strong>Título:</strong> {html.escape(raw_title, quote=True)}</p>"
            f"<p><strong>Fecha límite:</strong> {html.escape(due_label, quote=True)}</p>"
            "</body></html>"
        )
        return build_mime_message(
            recipient=recipient, subject=subject, text_body=text_body, html_body=html_body
        )

    if notification_type in DIGEST_TYPES:
        period_label = "diario" if notification_type == "digest_daily" else "semanal"
        date_str = occurrence.isoformat() if occurrence is not None else "hoy"
        subject = f"[Maulwurf] Resumen {period_label} · {date_str}"
        if digest_items:
            text_lines = [
                f"- [{item.tipo}] {item.titulo} ({item.due_label})" for item in digest_items
            ]
            html_lines = [
                "<li>"
                f"[{html.escape(item.tipo, quote=True)}] "
                f"{html.escape(item.titulo, quote=True)} "
                f"({html.escape(item.due_label, quote=True)})"
                "</li>"
                for item in digest_items
            ]
            text_list = "\n".join(text_lines)
            html_list = f"<ul>{''.join(html_lines)}</ul>"
        else:
            text_list = "No tienes entregas confirmadas próximas en este periodo."
            html_list = "<p>No tienes entregas confirmadas próximas en este periodo.</p>"

        text_body = f"Maulwurf — Resumen {period_label} ({date_str})\n\n{text_list}\n"
        escaped_heading = (
            f"{html.escape(period_label, quote=True)} ({html.escape(date_str, quote=True)})"
        )
        html_body = (
            "<html><body>"
            f"<h2>Resumen {escaped_heading}</h2>"
            f"{html_list}"
            "</body></html>"
        )
        return build_mime_message(
            recipient=recipient, subject=subject, text_body=text_body, html_body=html_body
        )

    raise NotificationDeliveryRejected(TEMPLATE_INVALID)
