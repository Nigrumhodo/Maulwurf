"""Capabilities de ingesta publicadas para el formulario web (A2.2)."""
from fastapi import APIRouter

from app.core.config import settings
from app.schemas.ingestion import IngestionCapabilitiesOut

router = APIRouter(tags=["ingestion"])


@router.get("/ingestion/capabilities")
async def ingestion_capabilities() -> IngestionCapabilitiesOut:
    """Devuelve exclusivamente la configuración publicada y versionada.

    S4.4 publica los TTLs efectivos (10/30/60 min) con su benchmark; los límites de
    tamaño/duración siguen nulos mientras D4 esté bloqueada. No lee los valores
    provisionales de la reserva S1 ni consulta proveedores externos. La versión del aviso
    de privacidad se inyecta aquí (M2/D2.1): es la vigente, no un límite publicado, para
    que el formulario la muestre ANTES de aceptar bytes.
    """
    return IngestionCapabilitiesOut.model_validate(
        settings.ingestion_capabilities.model_dump()
        | {"privacy_notice_version": settings.privacy_notice_version}
    )
