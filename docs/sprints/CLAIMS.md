# Registro canónico de claims — Maulwurf

> Estado: **S1 abierto**. Revisión: 2026-09-23.
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
| claim | S1.A5 | satnovaOG | hecho | 2026-09-24 | pendiente | — / líder | Tres llamadas: espera local `DEADLINE_EXCEEDED`, cancelación `CANCELLED`, piso 30 s `OK`. 200 MiB y 3 h no aprobados. Cloud sin revisor. |

## 4. Historial de cambios

| Fecha UTC | Decisión | Referencia |
|---|---|---|
| 2026-09-19 | Se adopta registro canónico; los listados personales prellenados pasan a ser candidatos históricos. | Revisión documental |
| 2026-09-23 | Se abre S1; se reclaman L1.1, L1.2, L1.3 y S1.A1 (hecho). | Este PR |
| 2026-09-24 | Se reclama S1.A2. La llamada real queda observada (gRPC `OK`); estado `hecho`. Elegibilidad cloud sigue `pendiente` (sin revisor). | Este cambio |
| 2026-09-24 | Se reclaman S1.A3 y S1.A4. Corridas observadas; estado `hecho`. Elegibilidad cloud sigue `pendiente`. | Este cambio |
| 2026-09-24 | Se reclama S1.A5. La serie de tres llamadas queda observada; estado `hecho`. 200 MiB y 3 h no se aprueban. Elegibilidad cloud `pendiente`. | Este cambio |
