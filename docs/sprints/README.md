# Bitácoras de sprint — Maulwurf

Este directorio contiene las **bitácoras personales** del equipo. El plan maestro con el
backlog por semana es [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md); el catálogo de pruebas que
evidencia cada tarea es [../PLAN_TESTS.md](../PLAN_TESTS.md); la fuente de contratos es
[../ESPECIFICACION.md](../ESPECIFICACION.md) y las puertas/evidencias en
[../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md).

## Cómo funciona el claiming

1. **Las tareas no están preasignadas.** Cada semana publica su backlog en el plan maestro
   (§2–§5), agrupado por área (`L-` RAG/liderazgo, `A-` API/BD, `S-` Ingesta/ASR,
   `J-` Infra/Integraciones, `D-` Web/UX) con dependencias y tests.
2. **El lunes** se abre únicamente el sprint activo tras revisar la puerta anterior,
   capacidad y disponibilidad. Cada persona elige tickets y los reclama mediante un PR en
   el [registro canónico](CLAIMS.md). **Un ticket = un responsable.** La bitácora personal
   conserva evidencia y bloqueos, pero no asigna un ticket por sí sola.
3. Las filas prellenadas en bitácoras antes de la revisión 2026-09-19 son candidatos
   históricos, no claims vigentes. S2–S4 no se reclaman hasta que se abran formalmente.
4. Lo que nadie reclama lo resuelve el líder: reasignar, dividir o bajar el alcance del
   DoD de forma explícita. Una división crea IDs hijos en el backlog antes del claim; una
   transferencia conserva owner anterior, fecha, motivo y evidencia.
5. Restricciones: un ticket que incluya prueba `P-*` o una llamada real a Riva,
   LLM/embeddings, Google o Gmail requiere elegibilidad verificada para la cuenta protegida
   (sin registrar secretos); un ticket que toca carpetas de otra área necesita review de
   quien tenga un claim activo allí o del líder.
6. **Evidencia sobre afirmaciones:** un ticket se cierra con comando real, fecha, entorno y
   resultado anotados en la bitácora. Un mock no valida proveedor; un test unitario no
   valida infraestructura; nada está «hecho» por estar escrito.

## Bitácoras del equipo

| Persona | Perfil sugerido (no ownership) | Bitácora |
|---|---|---|
| José Leonardo Pinilla Zamora | Liderazgo técnico · RAG | [jose-leonardo.md](jose-leonardo.md) |
| Andres Felipe | API · Base de datos | [andres.md](andres.md) |
| Santiago Montealegre | Ingesta · Audio · ASR | [santiago.md](santiago.md) |
| Jefferson Figueroa | Infra · Workers · Integraciones | [jefferson.md](jefferson.md) |
| Daniel Samaca | Web · UX | [daniel.md](daniel.md) |

El ownership vigente se consulta exclusivamente en [CLAIMS.md](CLAIMS.md).

El perfil es una sugerencia de especialización, no una restricción: cualquiera puede
reclamar de cualquier área respetando las reglas de arriba.

## Reglas de coordinación

1. **Interfaces congeladas:** el lunes se congela el contrato de las interfaces compartidas
   (§6 del plan maestro). Cambiarlas dentro de la semana requiere aprobación del líder y
   actualizar consumidores el mismo día.
2. **Anti-ciclo (§6a del plan maestro):** quien provee una interfaz congela el contrato y
   un doble/mock ejecutable el lunes; entrega la primera integración consumible a más tardar
   el día 3 y cierra el comportamiento/E2E durante días 4–5. Quien consume construye contra
   el contrato congelado y conmuta al código real al estar disponible. Nadie queda bloqueado
   más de un día sin una decisión visible.
3. **PRs y reviews:** nada se mergea sin 1 review; el código que cruza fronteras de área lo
   revisa una persona con claim activo en esa carpeta o el líder si no hay claim.
4. **Prioridad de documentos:** si este plan choca con `ESPECIFICACION.md` o
   `PLAN_IMPLEMENTACION.md`, ganan ellos; actualizar spec/plan en la misma revisión si un
   cambio de contrato es inevitable.
5. **Backlog:** ideas fuera del alcance de la semana van al backlog F4; el líder prioriza
   en la retro del viernes.
6. **Pruebas con proveedores cloud:** audio sintético generado en RAM, credenciales de
   prueba protegidas y presupuesto acordado; nunca clases reales hasta que D3 esté
   resuelto y G1 pase.

## Flujo de trabajo semanal

```
Lunes: verificar puerta/capacidad + abrir sprint + congelar interfaces + claims en CLAIMS.md
Diario: 15 min (estado, bloqueos, transferencias visibles)
Día 3: primera integración consumible; consumidores cambian mocks cuando proceda
Días 4–5: E2E, cierre técnico y evidencia
Viernes: demo + verificación del DoD + retro + cierre de bitácoras
```

## Plantilla de bitácora

Cada bitácora personal mantiene esta estructura mínima:

```markdown
# <Nombre> — bitácora de sprint

> Perfil sugerido: <área> · Plan maestro: [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) ·
> Pruebas: [../PLAN_TESTS.md](../PLAN_TESTS.md)

## Tickets reclamados

### Semana 1 (S1) — Fundación + spike

> Sección histórica: las filas anteriores a la revisión 2026-09-19 son candidatos, no
> claims. Los tickets vigentes se registran primero en `CLAIMS.md`.

| Ticket | Área | Estado (`pendiente/en curso/bloqueado/hecho/transferido`) | Evidencia (comando, fecha, entorno, resultado) |
|---|---|---|---|
| <id> | <área> | pendiente | Claim aceptado en `CLAIMS.md`: <PR/enlace> |

## Bloqueos y dependencias

- Espero de <quién>: <qué> (contrato congelado §6 / código real día <n>)

## Notas de semana

- <decisiones, hallazgos, desvíos de alcance>
```

Reglas de la plantilla: un ticket por fila y solo después de aparecer en `CLAIMS.md`; el
estado cambia en la daily, no el viernes; un ticket «hecho» sin evidencia vuelve a «en curso».
Cada semana S2–S4 añade su propia sección solo cuando el sprint sea abierto.

## Enlaces

- [CLAIMS.md](CLAIMS.md) — registro canónico de claims, transferencias y elegibilidad cloud.
- [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) — plan maestro: backlog por semana, dependencias, DoD.
- [../PLAN_TESTS.md](../PLAN_TESTS.md) — catálogo de pruebas por sprint: unitarias, integración, provider-contract, E2E y carga.
- [../DISENO_BD_API_SPRINTS.md](../DISENO_BD_API_SPRINTS.md) — diseño de BD/API y secuencia sugerida por área.
- [../ESPECIFICACION.md](../ESPECIFICACION.md) — contratos M1–M9 y modelo de datos.
- [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) — fases F0–F3, puertas G1–G8, decisiones D1–D8.
- [../NVIDIA_RIVA.md](../NVIDIA_RIVA.md) — parámetros auditados para el spike S1.
