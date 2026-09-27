# ADR-0006 — Aislamiento por tenant en S1

- Fecha UTC: 2026-09-27
- Estado: Propuesto (pasa a Aceptado al fusionar el PR de A1.5 con review)
- Ticket: A1.5 (lo verifica A1.9)
- Relacionado: [../ESPECIFICACION.md](../ESPECIFICACION.md) §6,
  [../plan/S1.md](../plan/S1.md) §3 (A1.5, A1.9),
  [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G3)

## Contexto

A1.5 pide «RLS base o filtrado obligatorio por `user_id` en todos los queries (decisión
documentada en ADR)». Las dos vías impiden que el usuario B lea o mute recursos de A:

- **Filtrado en la aplicación:** cada query de una tabla de propiedad lleva
  `WHERE user_id = :usuario_de_la_sesión`.
- **RLS de PostgreSQL:** políticas por tabla que ocultan filas ajenas aunque el query no
  filtre, con el usuario fijado por conexión (`SET LOCAL app.user_id`).

RLS exige un rol de BD sin propiedad de las tablas, fijar el usuario por transacción para
que el pool no lo arrastre entre peticiones y roles separados para migraciones y workers.
En S1 solo existen `users`, `sessions`, `google_credentials`, `subjects` y el shape S1 de
ingesta/outbox.

## Decisión

| Tema | S1 | S2 |
|---|---|---|
| Mecanismo | Filtrado obligatorio en la aplicación | Añadir RLS encima del filtrado |
| Punto único | Capa de repositorio que recibe el `user_id` de la sesión autenticada; los routers no construyen queries | Igual, más `SET LOCAL app.user_id` por transacción |
| Integridad | FKs compuestas `(user_id, id)` en toda fila de propiedad | Igual |
| Origen del `user_id` | Solo la sesión del backend; nunca payload, ruta ni salida de LLM | Igual |
| Recurso ajeno | `404` (no revelar existencia) | Igual |
| Workers | Reciben IDs y releen el `user_id` de la fila en BD | Rol de BD propio con política explícita |

## Consecuencias

- Una query escrita fuera del repositorio puede saltarse el filtro: la review lo rechaza y
  A1.9 (I-S1-AN-05) lo detecta adivinando IDs de A desde B.
- Las FKs compuestas impiden enlazar recursos de dos usuarios aunque falle un filtro.
- Pasar a RLS en S2 no cambia la API ni los tests de A1.9; añade una segunda barrera.
- Cambiar esta decisión exige actualizar este ADR y `docs/plan/S1.md` en el mismo PR.
