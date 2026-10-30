"""Capabilities de ingesta publicadas para el formulario web (A2.2)."""
from fastapi import APIRouter

from app.core.config import settings
from app.schemas.ingestion import IngestionCapabilities

router = APIRouter(tags=["ingestion"])


@router.get("/ingestion/capabilities")
async def ingestion_capabilities() -> IngestionCapabilities:
    """Devuelve exclusivamente la configuración publicada por el spike.

    No lee los valores provisionales de la reserva S1 ni consulta proveedores externos.
    La versión del aviso de privacidad se inyecta aquí (M2/D2.1): es la vigente, no un
    límite publicado, para que el formulario la muestre ANTES de aceptar bytes.
    """
    return settings.ingestion_capabilities.model_copy(
        update={"privacy_notice_version": settings.privacy_notice_version}
    )
