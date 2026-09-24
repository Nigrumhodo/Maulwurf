# Santiago Montealegre — bitácora de sprint

> Perfil sugerido: Ingesta · Audio · ASR (§0.2 del plan) ·
> Plan maestro: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Pruebas: [../PLAN_TESTS.md](../PLAN_TESTS.md) ·
> Contratos: [../ESPECIFICACION.md](../ESPECIFICACION.md) (§M2, §M3) ·
> Documento técnico: [../NVIDIA_RIVA.md](../NVIDIA_RIVA.md) ·
> Puertas: [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (F0, G1)

## Tickets reclamados

### Semana 1 (S1) — Fundación + spike (F0, bloqueante)

> Sección histórica: las filas anteriores a la revisión 2026-09-19 son candidatos, no
> claims. Ownership vigente: [CLAIMS.md](CLAIMS.md). Esta tabla es evidencia.

| Ticket | Área | Estado | Evidencia (comando, fecha, entorno, resultado) |
|---|---|---|---|
| L1.1 | L | hecho | 2026-09-23 UTC. `uv sync --extra dev` en `apps/api` y `apps/ingest`; `npm install` en `apps/web`. Pins `.python-version` 3.12, `.nvmrc` 22, `docs/VERSIONES.md` y lockfiles. Merge #7. |
| L1.2 | L | hecho | 2026-09-23 UTC. ADR-0001–0004 en `docs/adr/` (monorepo, versiones, lockfiles, commit/PR). Merge #7. |
| L1.3 | L | hecho | 2026-09-23 UTC. Protocolo en `docs/spike/F0.1-protocolo.md`. No es el informe S1.A8 ni llamadas Riva. Merge #7. |
| S1.A1 | S | hecho | 2026-09-23 UTC, WSL. `cd apps/ingest && uv run pytest tests/test_synthetic_audio.py` (U-S1-SG-01): 9 passed. WAV sintético en RAM. Merge #6. |
| S1.A2 | S | hecho | 2026-09-24 UTC, WSL. `cd apps/ingest && .venv/bin/pytest tests/test_riva_spike_report.py`: 8 passed. `cd apps/ingest && .venv/bin/python ../../scripts/provider/riva_spike.py`: gRPC `OK`, cliente `nvidia-riva-client==2.27.0`, `grpcio==1.84.0`, `protobuf==6.33.5`. Metadata inicial/final sin versión (`date`, `nvcf-reqid`, `set-cookie`; sin valores). Servidor `NO VERIFICADO`. Hipótesis 46 caracteres; la frase esperada no coincide tal cual. |
| S1.A3 | S | hecho | 2026-09-24 UTC, WSL. `cd apps/ingest && .venv/bin/python ../../scripts/provider/riva_languages.py`. gRPC `OK` en `es`, `en` y `fr` (hipótesis no vacía; la frase esperada no coincide). `zz` y código ausente: `INVALID_ARGUMENT`. Audio `es` declarado `en`: `OK`. Allowlist propuesta: `es`, `en`, `fr`. Sin `multi` ni `task:translate`. Pytest local de la regla: 2 passed. |
| S1.A4 | S | hecho | 2026-09-24 UTC, WSL. `pytest tests/test_format_validate.py`: 3 passed (U-S1-SG-02). Tabla de contenedores reejecutada con ffmpeg/ffprobe 8.0.1, sin nueva llamada Riva. WAV PCM s16 mono `accept`. `normalize` → WAV PCM s16 mono: mp3 (`mp3`), ogg (`vorbis`), opus (`opus`), flac (`flac`), webm (`opus`), todos mono. m4a por pipe: `NO VERIFICADO`. 16 kHz gRPC `OK` (corrida anterior), frase esperada no coincide; no es límite aprobado. 22050 Hz se cita de S1.A2 (`OK`). |
| S1.A5 | S | hecho | 2026-09-24 UTC, WSL. `cd apps/ingest && .venv/bin/pytest tests/test_riva_limits_report.py tests/test_riva_spike_report.py`: 12 passed. `cd apps/ingest && .venv/bin/python ../../scripts/provider/riva_limits.py` (P-S1-SG-07), tres llamadas en serie. Espera local 0.05 s: `DEADLINE_EXCEEDED` (`client_wait_timeout`); deadline del servidor `NO VERIFICADO`. Cancelación inmediata: `CANCELLED`. Piso 16 kHz, 30.0 s, 960044 bytes: gRPC `OK`, hipótesis 46 caracteres; es mínimo observado, no máximo. `approved_200_mib` y `approved_3h` en false. Cuota y concurrencia `NO VERIFICADO`. `INVALID_ARGUMENT` de S1.A3 citado, no repetido. Un intento previo abortó en `FutureCancelledError` antes del piso; la serie registrada es la completa. `P-S1-SG-08` (latencia y memoria) queda para S1.A7. |

## Bloqueos y dependencias

- (el lunes de cada semana: anotar de quién esperas contrato congelado §6 o código real, y qué día)

## Notas de semana

- 2026-09-23: registro retroactivo; trabajo en #6 y #7; claim canónico en #8.
