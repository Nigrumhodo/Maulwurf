# Sprints semanales — Maulwurf

Este directorio contiene las **fichas personales de sprint** del equipo. El plan
maestro es [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md); el catálogo de pruebas
que evidencia cada tarea es [../PLAN_TESTS.md](../PLAN_TESTS.md); la fuente de contratos es
[../ESPECIFICACION.md](../ESPECIFICACION.md) y las puertas/evidencias en
[../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md).

## Fichas del equipo

| Persona | Rol | Ficha |
|---|---|---|
| José Leonardo Pinilla Zamora | Líder técnico · RAG | [jose-leonardo.md](jose-leonardo.md) |
| Andres Felipe | Backend · Base de datos | [andres.md](andres.md) |
| Santiago Montealegre | Audio · Transcripción | [santiago.md](santiago.md) |
| Jefferson Figueroa | Integraciones · Infra · Calidad | [jefferson.md](jefferson.md) |
| Daniel Samaca | Frontend · UX | [daniel.md](daniel.md) |

## Reglas de coordinación

1. **Propiedad de carpetas (anti-choques):** cada persona es dueña de sus carpetas
   y nadie más hace merge sin su revisión:
   - José Leonardo: `apps/api/prompts/`, datasets de evaluación, decisiones (ADRs).
   - Andres: `apps/api` excepto `prompts/` y `workers/` compartidos con Jefferson.
   - Santiago: servicio `ingest`, cliente Riva, ffmpeg, tmpfs/supervisor.
   - Jefferson: `infra/`, CI, `apps/api/workers/`, integraciones Google.
   - Daniel: `apps/web`.

2. **Interfaces congeladas:** el lunes de cada sprint se congela el contrato de
   las interfaces compartidas (ver §5 del plan maestro). Cambiarlas dentro de la
   semana requiere aprobación del líder y actualizar consumidores el mismo día.

3. **PRs y reviews:** nada se mergea sin 1 review. El código que cruza fronteras
   de propiedad lo revisa el dueño de la carpeta destino.

4. **Evidencia sobre afirmaciones:** cada checklist marcada exige evidencia
   (comando real, fecha, entorno, resultado). Un mock no valida proveedor; un
   test unitario no valida infraestructura. Nada está "hecho" por estar escrito
   en la documentación.

5. **Evidencia y pruebas con proveedores cloud:** el spike de Riva y las pruebas
   de Google usan audio sintético generado en RAM, credenciales de prueba
   protegidas y presupuesto acordado. Nunca clases reales hasta que D3 esté
   resuelto y la puerta G1 pase.

6. **Regla de prioridad de documentos:** si este plan choca con
   `ESPECIFICACION.md` o `PLAN_IMPLEMENTACION.md`, ganan ellos. Actualizar
   spec/plan en la misma revisión si un cambio de contrato es inevitable.

7. **Backlog:** cualquier idea fuera del alcance de la semana va a backlog
   (F4 del plan de implementación); el líder la prioriza en la retro del viernes.

## Flujo de trabajo semanal

```
Lunes: plan + congelación de interfaces + reparto de tickets (45 min, todo el equipo)
Diario: daily de 15 min (hecho/bloqueos; el líder desbloquea)
Miércoles: integración cruzada (cada uno prueba el trabajo de otro en su entorno)
Viernes: demo + revisión de la puerta de salida + retro (45 min)
```

Regla anti-bloqueo del miércoles: quien sigue esperando código real de otro a mitad de semana
sigue sobre su mock del contrato congelado y reporta en la daily; el líder decide entre
aceptar el mock para la demo del viernes o reasignar tareas.

## Plantilla de checklist semanal

Cada ficha personal sigue esta estructura; copiarla para cada semana S1–S4.

```markdown
## Semana N (Sx) — <nombre de la semana>

### Objetivo
<una frase; debe coincidir con el plan maestro>

### Tareas (reparto concreto)
- [ ] T1 — <tarea> (día objetivo: lunes–martes)
- [ ] T2 — <tarea> (día objetivo: martes–miércoles)
...

### Entregables verificables
- <artefacto> — <cómo se comprueba>

### Alcance
**Dentro del alcance:**
- <qué sí haces esta semana>

**Fuera del alcance (otra persona / otra semana):**
- <qué NO haces y quién lo cubre o cuándo llega>

### Criterios de aceptación (cierre de la semana)
- [ ] <condición observable, binaria, medible>

### Pruebas requeridas
- <nivel> — <qué debe cubrir> — <quién las corre>

### Riesgos y plan B
- <riesgo> → <señal temprana> → <plan B>

### Interfaces que consume
- <contrato congelado> — dueño: <persona>

### Interfaces que provee
- <contrato congelado> — consumidores: <quién lo espera>

### Dependencias y bloqueos (anti-ciclo)

**Regla (§5a del plan maestro):** no existe dependencia circular dentro de una semana. Quien
**provee** una interfaz la congela el lunes y entrega el código a más tardar el **día 3**;
quien **consume** construye contra el contrato congelado (mock si hace falta) y conmuta a
código real el miércoles/jueves. Nadie queda bloqueado más de un día.

| Semana | No espero a (quién/código) | Consumo desde el lunes (contrato congelado) | Código real disponible |
|---|---|---|---|
| S1 | <persona> | <contrato> | <cuando/día> |
| S2 | <persona> | <contrato> | <cuando/día> |
| S3 | <persona> | <contrato> | <cuando/día> |
| S4 | <persona> | <contrato> | <cuando/día> |

- Necesito de <persona>: <qué> (día objetivo)
- Bloqueos: <ninguno / descripción>
```

## Enlaces

- [../PLAN_SPRINTS.md](../PLAN_SPRINTS.md) — plan maestro de 4 semanas (asignaciones, congelaciones, riesgos).
- [../PLAN_TESTS.md](../PLAN_TESTS.md) — catálogo de pruebas por sprint: unitarias, integración, provider-contract, E2E y carga.
- [../ESPECIFICACION.md](../ESPECIFICACION.md) — contratos M1–M9 y modelo de datos.
- [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) — fases F0–F3, puertas G1–G8, decisiones D1–D8.
- [../NVIDIA_RIVA.md](../NVIDIA_RIVA.md) — parámetros auditados para el spike S1 de Santiago.