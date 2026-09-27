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
| S1.A5 | S | hecho | 2026-09-24 UTC, WSL. `cd apps/ingest && .venv/bin/pytest tests/test_riva_limits_report.py tests/test_riva_spike_report.py`: 13 passed. `cd apps/ingest && .venv/bin/python ../../scripts/provider/riva_limits.py` (P-S1-SG-07), tres llamadas en serie. Espera local 0.05 s: `DEADLINE_EXCEEDED` (`client_wait_timeout`); deadline del servidor `NO VERIFICADO`. Cancelación inmediata: `CANCELLED`. Piso 16 kHz, 30.0 s, 960044 bytes: gRPC `OK`, hipótesis 46 caracteres; es mínimo observado, no máximo. `approved_200_mib` y `approved_3h` en false. Cuota y concurrencia `NO VERIFICADO`. `INVALID_ARGUMENT` de S1.A3 citado, no repetido. Un intento previo abortó en `FutureCancelledError` antes del piso; la serie registrada es la completa. `P-S1-SG-08` (latencia y memoria) queda para S1.A7. |
| S1.A6 | S | hecho | 2026-09-27T04:46:03Z, WSL. `cd apps/ingest && .venv/bin/pytest tests/test_timestamps.py tests/test_riva_offsets_report.py tests/test_riva_spike_report.py tests/test_riva_limits_report.py tests/test_riva_perf_report.py`: 26 passed (U-S1-SG-03 incluida). `cd apps/ingest && .venv/bin/python ../../scripts/provider/riva_offsets.py` (P-S1-SG-07), dos llamadas en serie con `enable_word_time_offsets`. Cliente: milisegundos documentados, escala 0.001. Ambas gRPC `OK`, 0 palabras y 0 segmentos. Frase 3.948 s, 174140 bytes, 22050 Hz, hipótesis 46 caracteres. Silencio en RAM 5.948 s (hablado 3.948 s), 262340 bytes. `d6_proposal=none`, `d6_status=pending`. No cierra G2-T ni G2-X. |
| S1.A7 | S | hecho | 2026-09-27T04:46:03Z, WSL. `cd apps/ingest && .venv/bin/python ../../scripts/provider/riva_perf.py` (P-S1-SG-08), tres llamadas en serie. `es` 3.948 s: latencia 1.037 s, gRPC `OK`, 174140 bytes, 22050 Hz, hipótesis 46. Piso `es` 30.0 s, 16 kHz, 960044 bytes: latencia 1.692 s, gRPC `OK`. `en` 3.231 s, 142510 bytes, 22050 Hz: latencia 1.054 s, gRPC `OK`, hipótesis 39. `en` 30 s y `fr` `NO VERIFICADO`. `VmRSS` SDK: 20742144 antes de importar `riva.client`, 36171776 después. PCM 3 h 16 kHz mono s16 = 345600000 bytes (329.59 MiB), no subido. RSS antes 36175872, pico 381779968, delta 345604096. Coste/min y /h `NO VERIFICADO`. `approved_200_mib` y `approved_3h` en false. Audio observado 37.178 s. |
| S1.A8 | S | hecho | 2026-09-27 UTC. Informe en `docs/spike/F0.1-informe-riva.md`, formato L1.3, sin llamada nueva a NVIDIA. Huecos en `NO VERIFICADO`. Retención del audio de este `function-id` no consta en la página pública de términos NVCF. |
| S1.A9 | S | hecho | 2026-09-27 UTC. Acta en `docs/spike/F0.1-acta-decisiones.md`. D2 `pending`, D3-Audio `blocked`, D4 `blocked`, D6 `pending` con propuesta `none`. Ninguna `approved`. Spec, API y UI sin cambio. |
| S1.B2 | S | hecho | 2026-09-27 UTC, WSL. `cd apps/ingest && .venv/bin/pytest tests/test_capacity.py -q --noconftest`: 3 passed. `cd apps/ingest && .venv/bin/python ../../scripts/provider/capacity_bench.py`: un slot provisional 209747200 bytes cabe en el tmpfs de 268435456; dos slots (419494400) no. RSS de una conversión de 2 s: antes 16633856, pico 16769024; directorio pico 128122; audio borrado. 200 MiB y 3 h no aprobados. PCM de 3 h no entra en el slot. |
| S1.B4 | S | hecho | 2026-09-27 UTC. Postgres en `127.0.0.1:55432`. En el host WSL, 7 failed: `unreadable_processes` 35, uid 0, descriptores al audio 0, tmpfs sí. En contenedor Ubuntu con `/proc` propio: `cd apps/ingest && .venv/bin/python -m pytest -m integration tests/test_failure_matrix.py -q` → 10 passed en 9.23 s. `unreadable` 0. |
| S1.B5 | S | hecho | 2026-09-27 UTC, WSL. Postgres y Redis en `127.0.0.1`. `cd apps/ingest && uv run pytest tests/test_no_audio_persistence.py tests/test_cleanup_admission.py -q` → 9 passed en 7.48 s. I-S1-SG-06: mounts, Redis, logs, trazas y cachés en cero, sin dumps. En este host el éxito del supervisor no queda `verified` por `unreadable_processes` (igual que S1.B4); el directorio del intento ya no está. |
| S1.B6 | S | hecho | 2026-09-27 UTC, WSL. Los 9 passed de ingest cubren I-S1-SG-05: lease vencido deja `pending` y la admisión sigue abierta; cleanup `failed` la cierra y emite `cleanup_failed` sin rutas. `cd apps/api && uv run pytest tests/integration/test_cleanup_gate.py -q` → 1 passed en 1.97 s. `index_requested` sigue `pending`. |

## Bloqueos y dependencias

- (el lunes de cada semana: anotar de quién esperas contrato congelado §6 o código real, y qué día)

## Notas de semana

- 2026-09-23: registro retroactivo; trabajo en #6 y #7; claim canónico en #8.
