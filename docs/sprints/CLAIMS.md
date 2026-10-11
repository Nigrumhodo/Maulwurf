# Canonical claims registry — Maulwurf

> Status: **S2 open**. Review: 2026-10-09.
>
> This file is the single source of truth for ticket ownership. Personal logbooks keep
> evidence, blockers, and notes, but a row there does not assign a ticket by itself. Rows
> pre-filled before this review are historical candidates and **are not valid claims**
> until they appear here.

## 1. Usage rules

1. Only the active sprint's section opens after verifying the previous gate, the measured
   capacity, and the team's availability. S2–S4 are not claimed in advance.
2. A claim is accepted through a single planning PR that adds one complete row. The
   leader's or the team-designated person's review resolves editing conflicts.
3. `Owner` is the only person responsible for the ticket. `Contributors` and `Reviewers`
   may collaborate, but they do not share ownership or closure.
4. If the ticket has a `P-*` test or a real call to Riva, LLM/embeddings, Google, or
   Gmail, `Cloud eligibility` must state `verificada` with date and reviewer, no secrets.
5. When transferring a ticket, the historical row is kept and another one is added with
   `action = transfer`. When splitting it, first update the backlog with approved child
   IDs; parts are not claimed with informal IDs.
6. A `continuo` ticket uses the operating status `en curso` during the sprint and must close
   as `hecho`, `bloqueado`, or `transferido` with weekly evidence. `continuo` is not a state.

## 2. Allowed states and actions

| Field | Values |
|---|---|
| `Acción` | `claim`, `transfer`, `unclaim`, `split` |
| `Estado` | `pendiente`, `en curso`, `bloqueado`, `hecho`, `transferido`, `cancelado` |
| `Elegibilidad cloud` | `no aplica`, `pendiente`, `verificada` |

An `unclaim` is only valid before work starts or if it records the evidence handover.
A `split` references the PR that created the child IDs. No action removes history.

These six values are **all** the status vocabulary: there are no `reclamado`, `en_curso`,
`en_revision`, or `continuo` statuses. `continuo` describes the bounded nature of a ticket
(see rule 6) and the table's actions are recorded as dated events.

## 3. Active sprint

**S2 is open.** In S1 only A1.6 remains `en curso`: #49 claims the CRUD and the code is
not in `develop`. A1.3 and L1.7 are not implemented. S3 and S4 remain closed. D2 and D6
remain `pending`; D3-Audio and D4 remain `blocked`.

### S1 — Foundation + F0 spike

> Logbooks keep the "Claimed tickets" section as history. Only this table's rows are
> valid claims; when accepting a claim, the PR links the logbook evidence but does not
> replace it.

| Action | Ticket | Owner | Status | Claimed at (UTC) | Cloud eligibility | Contributors / reviewers | Reason, evidence, or PR |
|---|---|---|---|---|---|---|---|
| claim | L1.1 | satnovaOG | hecho | 2026-09-23 | no aplica | — / líder | Pins y lockfiles; mergeado en #7 |
| claim | L1.2 | satnovaOG | hecho | 2026-09-23 | no aplica | — / líder | ADRs 0001–0004; mergeado en #7 |
| claim | L1.3 | satnovaOG | hecho | 2026-09-23 | no aplica | — / líder | `docs/spike/F0.1-protocolo.md`; mergeado en #7 |
| claim | S1.A1 | satnovaOG | hecho | 2026-09-23 | no aplica | — / líder | Generador sintético en RAM; mergeado en #6 |
| claim | S1.A2 | satnovaOG | hecho | 2026-09-24 | pendiente | — / líder | Llamada observada gRPC `OK`; servidor `NO VERIFICADO`. Cloud sin revisor. |
| claim | S1.A3 | satnovaOG | hecho | 2026-09-24 | pendiente | — / líder | Allowlist propuesta `es`, `en`, `fr`. Cloud sin revisor. |
| claim | S1.A4 | satnovaOG | hecho | 2026-09-24 | pendiente | — / líder | WAV `accept`; mp3/ogg/opus/flac/webm `normalize`. m4a `NO VERIFICADO`. 16 kHz `OK`, no aprobado. Cloud sin revisor. |
| claim | S1.A5 | satnovaOG | hecho | 2026-09-24 | pendiente | — / líder | Tres llamadas: espera local `DEADLINE_EXCEEDED`, cancelación `CANCELLED`, piso 30 s `OK`. 200 MiB y 3 h no aprobados. Cuota, concurrencia y máximos `NO VERIFICADO`. Cloud sin revisor. |
| claim | S1.A6 | satnovaOG | hecho | 2026-09-27 | pendiente | — / líder | Offsets: gRPC `OK` sin palabras ni segmentos. D6 propuesta `none`, estado `pending`. Cloud sin revisor. |
| claim | S1.A7 | satnovaOG | hecho | 2026-09-27 | pendiente | — / líder | Latencia `es`/`en` y pico PCM ~330 MiB. Coste `NO VERIFICADO`. 200 MiB y 3 h no aprobados. Cloud sin revisor. |
| claim | S1.A8 | satnovaOG | hecho | 2026-09-27 | pendiente | — / líder | Informe `docs/spike/F0.1-informe-riva.md`. Retención del audio `NO VERIFICADO`. Cloud sin revisor. |
| claim | S1.A9 | satnovaOG | hecho | 2026-09-27 | pendiente | — / líder | Acta: D2 y D6 `pending`; D3-Audio y D4 `blocked`. Nada `approved`. Cloud sin revisor. |
| claim | J1.8 | jpinillaz | hecho | 2026-09-25 | no aplica | — / líder | Target `base` con ffmpeg fijado y paridad 7.1.5 vs 8.0.1 verificada; mergeado en #12 |
| claim | J1.1 | jpinillaz | hecho | 2026-09-25 | no aplica | — / líder | Compose de 8 servicios; evidencia en bitácora; mergeado en #13 |
| claim | J1.2 | jpinillaz | hecho | 2026-09-25 | no aplica | — / líder | Ingest endurecido; evidencia en bitácora; host pendiente de operación; mergeado en #14 |
| claim | J1.3 | jpinillaz | hecho | 2026-09-25 | no aplica | — / líder | Caddy sin buffering; SSE pendiente de A1.8/S2; mergeado en #15 |
| claim | J1.4 | jpinillaz | hecho | 2026-09-25 | no aplica | — / líder | CI + plantilla de PR (ADR-0004); pipeline verde; mergeado en #16 |
| claim | J1.6 | jpinillaz | hecho | 2026-09-25 | no aplica | — / líder | ADR-0005 y política Redis; mergeado en #17 |
| claim | J1.7 | jpinillaz | hecho | 2026-09-25 | no aplica | — / líder | Runbook de desarrollo; verificado desde clon limpio; mergeado en #18 |
| claim | J1.5 | jpinillaz | hecho | 2026-09-27 | no aplica | — / área A | Worker/scheduler ARQ en #25; dispatcher con gate de cleanup (U-S1-JF-04) en #30. Ack/reintentos pasan a J2.1 |
| claim | L1.6 | jpinillaz | hecho | 2026-09-27 | no aplica | — / arquitectura | Congelación `S1-v1` de §1.1–§1.5 de `docs/plan/S1.md`; mergeado en #38 |
| claim | A1.1 | jpinillaz | hecho | 2026-09-27 | no aplica | — / área A | Router de salud y base de tests; mergeado en #21 |
| claim | A1.2 | jpinillaz | hecho | 2026-09-27 | no aplica | — / área A | Settings fail-fast; mergeado en #23 y #26 |
| claim | A1.5 | jpinillaz | hecho | 2026-09-27 | no aplica | — / área A, seguridad | Esquema núcleo, shape S1 de ingesta/outbox, AES-GCM, ADR-0006; mergeado en #24 |
| claim | A1.4 | jpinillaz | hecho | 2026-09-27 | no aplica | — / área A, seguridad | Sesión opaca, CSRF y `GET /me`; mergeado en #22 |
| claim | A1.7 | jpinillaz | hecho | 2026-09-27 | no aplica | — / área A | Migraciones en compose y sección del runbook; mergeado en #28 |
| claim | A1.8 | jpinillaz | hecho | 2026-09-27 | no aplica | — / área A, Web | Contrato §1.2 (`PUT` responde 503 en S1); mergeado en #29. El 429 por usuario espera D4 |
| claim | S1.B1 | jpinillaz | hecho | 2026-09-27 | no aplica | — / área S | Autochequeo de endurecimiento y I-S1-SG-06 (parte B1); mergeado en #33. Host de desarrollo sin modificar (excepción) |
| claim | S1.B2 | satnovaOG | hecho | 2026-09-27 | no aplica | — / líder | Tabla `docs/spike/F0.2-capacidad-slot.md`. Dos slots no caben en el tmpfs de 256 MiB. 200 MiB y 3 h no aprobados. |
| claim | S1.B3 | jpinillaz | hecho | 2026-09-27 | no aplica | — / área S | Lease, fencing y cleanup verificado con ffmpeg real; mergeado en #34. `verified` solo en contenedor (L1.6, excepción 5) |
| claim | S1.B4 | satnovaOG | hecho | 2026-09-27 | no aplica | — / líder | Matriz I-S1-SG-04: 10 passed en contenedor Ubuntu (PID propio). En el WSL del host, `unreadable_processes` de root impide `verified`. |
| claim | S1.B5 | satnovaOG | hecho | 2026-09-27 | no aplica | — / área S | I-S1-SG-06: 9 passed en WSL. Barrido en cero. En este host el éxito no queda `verified` por `unreadable_processes` (igual que S1.B4). |
| claim | S1.B6 | satnovaOG | hecho | 2026-09-27 | no aplica | — / área S | I-S1-SG-05: lease `pending` no cierra admisión; cleanup `failed` alerta y la cierra. Outbox: 1 passed. |
| claim | A1.9 | jpinillaz | hecho | 2026-10-04 | no aplica | — / área A | Suite I-S1-AN-05 contra Postgres; mergeado en #44. Las rutas de A1.6 y A1.3 quedan fuera de la matriz. |
| claim | L1.4 | jpinillaz | hecho | 2026-10-04 | no aplica | — / líder | Borradores Pydantic §M4/§M6 (U-S1-JL-01/02); mergeado en #43. Sin llamadas a LLM. |
| claim | L1.5 | jpinillaz | hecho | 2026-10-04 | no aplica | — / líder | Formato y validador del dataset G4 (U-S1-JL-03); mergeado en #45. Sin contenido real. |
| claim | A1.6 | <danielsam171> | en curso | 2026-10-06| no aplica | — / área A | CRUD de materias (I-S1-AN-07); force diferido a S2 |

### S2 — Knowledge

| Action | Ticket | Owner | Status | Claimed at (UTC) | Cloud eligibility | Contributors / reviewers | Reason, evidence, or PR |
|---|---|---|---|---|---|---|---|
| claim | A2.1 | angalindog | hecho | 2026-10-05 | no aplica | — / área A | Migración `0002_s2_schema`; mergeado en #48 |
| claim | A2.2 | angalindog | hecho | 2026-10-05 | no aplica | — / área A | `GET /ingestion/capabilities`; mergeado en #47 |
| claim | A2.3 | angalindog | hecho | 2026-10-07 | no aplica | — / área A | `POST /audios` con consentimiento y slot; mergeado en #51 |
| claim | A2.4 | angalindog | hecho | 2026-10-07 | no aplica | — / área A | `PUT` streaming, SHA-256 y dedupe; mergeado en #52. El sink por defecto sigue en 503 (C2). |
| claim | S2.1 | satnovaOG | hecho | 2026-10-09 | no aplica | — / líder | Recepción y ffprobe; mergeado en #54 |
| claim | S2.2 | satnovaOG | hecho | 2026-10-09 | no aplica | — / líder | ffmpeg acotado y fragmentación a 30 s; mergeado en #54 |
| claim | S2.3 | satnovaOG | en curso | 2026-10-09 | no aplica | — / líder | Backpressure y reconciliación de fronteras (U-S2-SG-04) |
| claim | S2.4 | satnovaOG | en curso | 2026-10-09 | pendiente | — / líder | Cliente Riva aislado. `P-S2-SG-12` no se ejecuta. Cloud sin revisor. |

## 4. Change history

| Date (UTC) | Decision | Reference |
|---|---|---|
| 2026-09-19 | Se adopta registro canónico; los listados personales prellenados pasan a ser candidatos históricos. | Revisión documental |
| 2026-09-23 | Se abre S1; se reclaman L1.1, L1.2, L1.3 y S1.A1 (hecho). | Este PR |
| 2026-09-24 | Se reclama S1.A2. La llamada real queda observada (gRPC `OK`); estado `hecho`. Elegibilidad cloud sigue `pendiente` (sin revisor). | Este cambio |
| 2026-09-24 | Se reclaman S1.A3 y S1.A4. Corridas observadas; estado `hecho`. Elegibilidad cloud sigue `pendiente`. | Este cambio |
| 2026-09-24 | Se reclama S1.A5. La serie de tres llamadas queda observada; estado `hecho`. 200 MiB y 3 h no se aprueban. Elegibilidad cloud `pendiente`. | Este cambio |
| 2026-09-25 | Se reclaman J1.1–J1.4 y J1.6–J1.8 (J1.5 queda pendiente en S1). Trabajo y evidencia registrados antes del merge; estado `en curso` hasta review. | Este PR |
| 2026-09-26 | Se cierran J1.1–J1.4 y J1.6–J1.8 como `hecho` tras fusionar #12–#18 en `develop` (pipeline de calidad verde en `4aa8ced`). J1.5 sigue pendiente en S1. | Este PR |
| 2026-09-27 | Se reclaman J1.5, L1.6, A1.1, A1.2, A1.4, A1.5, A1.7, A1.8, S1.B1 y S1.B3 (camino crítico y tickets con más dependientes). Quedan libres A1.3, A1.6, A1.9, L1.4, L1.5, L1.7 y S1.B2, S1.B4–B6; S1.A6–A9 siguen con su owner en Plane. | Este PR |
| 2026-09-27 | A1.1 y A1.5 pasan a `hecho` (#21, #24). J1.5 sigue `en curso` con el parcial de #25. A1.2 y A1.4 en revisión. | Este PR |
| 2026-09-27 | Se reclaman S1.A6 y S1.A7. Corridas observadas; D6 queda `pending` con propuesta `none`. Coste `NO VERIFICADO`. Elegibilidad cloud `pendiente`. | Este cambio |
| 2026-09-27 | Se reclaman S1.A8 y S1.A9. Informe y acta publicados. D2 y D6 `pending`; D3-Audio y D4 `blocked`. Elegibilidad cloud `pendiente`. | Este cambio |
| 2026-09-27 | La elegibilidad cloud de S1.A2–A7 sigue `pendiente` hasta que haya revisor. S1.A8 y S1.A9 no llaman a NVIDIA. | Este cambio |
| 2026-09-27 | Se reclaman S1.B2 (hecho) y S1.B4. La matriz pasa en contenedor (10 passed). En el host WSL no, por procesos root ilegibles. S1.B1 y S1.B3 siguen `en curso` de jpinillaz. | Este cambio |
| 2026-09-27 | Se reclaman S1.B5 y S1.B6 (`en curso`). | Este cambio |
| 2026-09-27 | S1.B5 y S1.B6 pasan a `hecho`. I-S1-SG-06: 9 passed en ingest. I-S1-SG-05: el gate de outbox, 1 passed. En el WSL el éxito no queda `verified` por `unreadable_processes`. | Este cambio |
| 2026-09-28 | A1.2, A1.4, A1.7, A1.8, J1.5, S1.B1 y S1.B3 pasan a `hecho` tras fusionar #22, #23, #26, #28–#30, #33 y #34 en `develop`. L1.6 sigue `en curso` hasta que se fusione su PR. | Este PR |
| 2026-09-28 | L1.6 pasa a `hecho`: el PR `s1/l1.6-freeze` (#38) se fusionó en `develop` (35e4d70) tras #39/#40. La congelación `S1-v1` está publicada en `docs/plan/S1.md`. | Este PR |
| 2026-10-04 | Se reclaman A1.9, L1.4 y L1.5 (libres desde 2026-09-27). | Este PR |
| 2026-10-05 | Se reclama A1.6 (libre desde 2026-09-27). | Este PR |
| 2026-10-09 | Se abre S2. Se reclaman S2.1 y S2.2 (`hecho`, #54) y S2.3 y S2.4 (`en curso`). D2 y D6 siguen `pending`; D3-Audio y D4 siguen `blocked`. S2.4 queda con cloud `pendiente`: `P-S2-SG-12` no se corre. A1.6, A1.9, L1.4 y L1.5 siguen `en curso` en S1. | Este PR |
| 2026-10-09 | A1.9 (#44), L1.4 (#43) y L1.5 (#45) pasan a `hecho`. A1.6 sigue `en curso`: #49 solo reclama y el CRUD no está en `develop`. A1.3 y L1.7 no están implementados. Se reclaman A2.1 (#48), A2.2 (#47), A2.3 (#51) y A2.4 (#52) como `hecho` (`angalindog`). A2.5 sigue abierto. | Este cambio |

