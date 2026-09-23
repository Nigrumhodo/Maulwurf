# ADR-0004 — Formato de commit/PR y reviews

- Fecha UTC: 2026-09-23
- Estado: Aceptado
- Ticket: L1.2
- Relacionado: [docs/sprints/README.md](../sprints/README.md)

## Contexto

No había plantilla de commit ni hook. AGENTS.md pedía definir el formato
antes de imponerlo. Las reglas de PR ya existen en el modelo de sprints.

## Decisión

### Commits

- Asunto imperativo (inglés o español), cerca de 72 caracteres.
- Cuerpo: el **por qué**, no el listado de archivos, si no es obvio.
- Incluir el ID de ticket (`L1.2`, `S1.A1`, …) cuando el cambio cierra o
  avanza un ticket.
- No `--no-verify` ni saltar hooks salvo petición explícita del autor.
- No hay commit-lint ni husky en este ticket: la norma es documental.

### Pull requests

- Base: `develop`.
- Cuerpo: resumen (1–3 puntos) y test plan o checklist de verificación.
- Mínimo **una** review antes de merge.
- Si el diff cruza área, revisa quien tenga claim activo en esa carpeta, o
  el líder si no hay claim.
- Un PR de claim toca **solo** su fila en `docs/sprints/CLAIMS.md`.
- No mergear secretos, `.env`, audio, transcripts privados ni payloads de
  proveedor.

## Consecuencias

J1.4 puede añadir checks de CI después, sin cambiar esta norma. Reviews de
privacidad/proveedor/despliegue de AGENTS.md siguen siendo puertas aparte.
