<!--
Formato según ADR-0004 (docs/adr/ADR-0004-commits-pr.md).
Base del PR: develop. Un PR = un ticket. Mínimo 1 review.
Sin secretos, .env, audio, transcripts privados ni payloads de proveedor.
Un PR de claim toca solo su fila en docs/sprints/CLAIMS.md.
-->

## Resumen

<!-- 1–3 puntos: qué cambia y por qué. -->

-
-

## Ticket y claim

- Ticket: <!-- p. ej. J1.1 -->
- Claim vigente: <!-- fila de docs/sprints/CLAIMS.md o PR de claim -->

## Test plan / verificación

<!-- Comandos exactos y resultado esperado. Evidencia: comando, fecha UTC, entorno y resultado. -->

- [ ]
- [ ]

## Evidencia

<!-- Enlace a la bitácora y salidas redactadas; sin datos sensibles. -->

## Checklist

- [ ] 1 review mínimo; si el diff cruza área, revisa quien tenga claim activo en esa carpeta o el líder
- [ ] CI de calidad en verde (J1.4); `P-*` y `load` no se ejecutan en MR
- [ ] Sin secretos, `.env`, audio, transcripts privados ni payloads de proveedor en el diff
- [ ] `docs/ESPECIFICACION.md` y `docs/PLAN_IMPLEMENTACION.md` actualizados si cambió un contrato
- [ ] Fila de `docs/sprints/CLAIMS.md` actualizada con la evidencia (o PR de claim separado)
