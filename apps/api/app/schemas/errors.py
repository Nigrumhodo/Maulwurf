"""Serialización de errores de validación de los contratos de `app.schemas`."""
from pydantic import ValidationError
from pydantic_core import ErrorDetails


def validation_errors_redacted(exc: ValidationError) -> list[ErrorDetails]:
    """Serializa errores de estos contratos sin el valor recibido.

    ¿Por qué: `str(exc)` y `errors()` por defecto incluyen `input_value`; si un
    worker loguea la excepción directa, el transcript (dato no confiable) llega a
    logs/traces. Este helper es el único punto de serialización permitido.
    """
    return exc.errors(include_input=False, include_url=False)
