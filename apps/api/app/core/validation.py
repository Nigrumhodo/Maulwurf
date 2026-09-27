"""Validadores de dominio compartidos entre routers."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def is_iana_timezone(value: str) -> bool:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True
