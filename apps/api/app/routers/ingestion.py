"""Capabilities de ingesta publicadas para el formulario web (A2.2)."""
from fastapi import APIRouter

from app.core.config import settings
from app.schemas.ingestion import IngestionCapabilities

router = APIRouter(tags=["ingestion"])


@router.get("/ingestion/capabilities")
async def ingestion_capabilities() -> IngestionCapabilities:
    """Devuelve exclusivamente la configuración publicada por el spike.

    No lee los valores provisionales de la reserva S1 ni consulta proveedores externos.
    """
    return settings.ingestion_capabilities
