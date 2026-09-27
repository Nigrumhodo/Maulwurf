# Registro canónico de claims — Maulwurf

> Estado: **S1 abierto**. Revisión: 2026-09-27.
>
> Este archivo es la única fuente de verdad para la propiedad de tickets. Las bitácoras
> personales conservan evidencia, bloqueos y notas, pero una fila allí no asigna un ticket
> por sí sola. Las filas prellenadas antes de esta revisión son candidatos históricos y **no
> son claims vigentes** hasta que aparezcan aquí.

## 1. Reglas de uso

1. Solo se abre la sección del sprint activo después de verificar la puerta anterior, la
   capacidad medida y la disponibilidad del equipo. S2–S4 no se reclaman por adelantado.
2. Un claim se acepta mediante un único PR de planificación que añade una fila completa. La
   revisión del líder o de la persona designada por el equipo resuelve conflictos de edición.
3. `Owner` es la única persona responsable del ticket. `Contributors` y `Reviewers` pueden
   colaborar, pero no comparten la propiedad ni el cierre.
4. Si el ticket tiene una prueba `P-*` o una llamada real a Riva, LLM/embeddings, Google o
   Gmail, `Elegibilidad cloud` debe indicar `verificada` con fecha y revisor, sin secretos.
5. Al transferir un ticket, se conserva la fila histórica y se agrega otra con `acción =
   transfer`. Al dividirlo, primero se actualiza el backlog con IDs hijos aprobados; no se
   reclaman partes con IDs informales.
6. Un ticket `continuo` usa estado operativo `en curso` durante el sprint y debe cerrar como
   `hecho`, `bloqueado` o `transferido` con evidencia semanal. `continuo` no es un estado.

## 2. Estados y acciones permitidos

| Campo | Valores |
|---|---|
| `Acción` | `claim`, `transfer`, `unclaim`, `split` |
| `Estado` | `pendiente`, `en curso`, `bloqueado`, `hecho`, `transferido`, `cancelado` |
| `Elegibilidad cloud` | `no aplica`, `pendiente`, `verificada` |

Un `unclaim` solo es válido antes de iniciar trabajo o si registra el traspaso de evidencia.
Un `split` referencia el PR que creó los IDs hijos. Ninguna acción elimina el historial.

Estos seis valores son **todo** el vocabulario de estado: no existen `reclamado`, `en_curso`,
`en_revision` ni `continuo` como estados. `continuo` describe la naturaleza acotada de un
ticket (ver regla 6) y las acciones de la tabla se registran como eventos con fecha.

## 3. Sprint activo

**S1 está abierto.** S2–S4 siguen cerrados. La apertura queda registrada en el historial
de este PR de planificación.

### S1 — Fundación + spike F0

> Las bitácoras conservan la sección «Tickets reclamados» como historial. Solo las filas de
> esta tabla son claims vigentes; al aceptar un claim, el PR enlaza la evidencia de la
> bitácora pero no la sustituye.

| Acción | Ticket | Owner | Estado | Claimed at (UTC) | Elegibilidad cloud | Contributors / reviewers | Motivo, evidencia o PR |
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
| claim | J1.5 | jpinillaz | en curso | 2026-09-27 | no aplica | — / área A | Parcial mergeado en #25 (worker/scheduler ARQ, `/readyz` real); falta el dispatcher con gate (U-S1-JF-04), bloqueado por A1.8 |
| claim | L1.6 | jpinillaz | en curso | 2026-09-27 | no aplica | — / arquitectura | Congela §1.1–§1.5 de `docs/plan/S1.md`; bloquea A1.8 |
| claim | A1.1 | jpinillaz | hecho | 2026-09-27 | no aplica | — / área A | Router de salud y base de tests; mergeado en #21 |
| claim | A1.2 | jpinillaz | en curso | 2026-09-27 | no aplica | — / área A | Base mergeada vía #24; PR propio con la 2.ª/3.ª ronda de QA en revisión |
| claim | A1.5 | jpinillaz | hecho | 2026-09-27 | no aplica | — / área A, seguridad | Esquema núcleo, shape S1 de ingesta/outbox, AES-GCM, ADR-0006; mergeado en #24 |
| claim | A1.4 | jpinillaz | en curso | 2026-09-27 | no aplica | — / área A, seguridad | Sesión opaca, CSRF y `GET /me`; PR en revisión, desbloquea A1.8 y el cliente CSRF de Web |
| claim | A1.7 | jpinillaz | en curso | 2026-09-27 | no aplica | — / área A | Completa la sección de migraciones pendiente en J1.7 |
| claim | A1.8 | jpinillaz | en curso | 2026-09-27 | no aplica | — / área A, Web | Depende de A1.4 y L1.6; desbloquea J1.5 |
| claim | S1.B1 | jpinillaz | en curso | 2026-09-27 | no aplica | — / área S | Desbloquea S1.B2 y S1.B3 |
| claim | S1.B2 | satnovaOG | hecho | 2026-09-27 | no aplica | — / líder | Tabla `docs/spike/F0.2-capacidad-slot.md`. Dos slots no caben en el tmpfs de 256 MiB. 200 MiB y 3 h no aprobados. |
| claim | S1.B3 | jpinillaz | en curso | 2026-09-27 | no aplica | — / área S | Desbloquea S1.B4, S1.B5, S1.B6 |
| claim | S1.B4 | satnovaOG | hecho | 2026-09-27 | no aplica | — / líder | Matriz I-S1-SG-04: 10 passed en contenedor Ubuntu (PID propio). En el WSL del host, `unreadable_processes` de root impide `verified`. |

## 4. Historial de cambios

| Fecha UTC | Decisión | Referencia |
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
