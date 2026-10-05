"""Contratos Pydantic de datos derivados del LLM y del índice (no ORM; ver `app.models`).

La serialización de sus errores de validación no hace eco del valor recibido: usar
siempre `app.schemas.errors.validation_errors_redacted`; `str(ValidationError)` y
`errors()` por defecto sí incluyen `input_value`, y el transcript es dato no confiable.
"""
