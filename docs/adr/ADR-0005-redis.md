# ADR-0005 — Política de Redis

- Fecha UTC: 2026-09-25
- Estado: Propuesto (pasa a Aceptado al fusionar el PR de J1.6 con review)
- Ticket: J1.6
- Relacionado: [../ESPECIFICACION.md](../ESPECIFICACION.md) §3.1 y §8,
  [../PLAN_IMPLEMENTACION.md](../PLAN_IMPLEMENTACION.md) (G1, G5-F1),
  [../plan/S1.md](../plan/S1.md) §1.3 y §5 (J1.6)

## Contexto

ARQ usa Redis como cola y almacén de estado de trabajo. El outbox durable vive
en PostgreSQL: los trabajos se pueden regenerar desde la BD si Redis se pierde
(J2.3). El riesgo es convertir Redis en un almacén no auditado de audio,
transcripts, prompts, tokens o PII, y en una segunda fuente de verdad con
persistencia duradera no revisada.

## Decisión

| Tema | Decisión | Razón |
|---|---|---|
| Fuente de verdad | PostgreSQL (`outbox_events`) | Redis es prescindible; los jobs se reconstruyen |
| Persistencia de Redis | Deshabilitada: `--save "" --appendonly no`, sin volumen | Evita copias durables no auditadas |
| Evicción | `maxmemory-policy noeviction` | Fallar visible antes que evacuar jobs en silencio |
| `maxmemory` | `${REDIS_MAXMEMORY:-256mb}` (provisional) | Acota el contenedor; se revisa en S2/S4 |
| Umbral de valor | **4 KiB por valor** | IDs, estados, contadores y resultados cortos de ARQ; nada mayor pertenece a Redis |
| Catálogo de claves | `arq:*`, `arq:health-check:*`, `mw:*` solo IDs/estado | Toda clave nueva se documenta aquí |
| TTL | Resultados ARQ `keep_result=60` s; claves de salud 30–60 s | Sin estado de larga vida |
| Prohibido | Audio, transcripts, textos, prompts, tokens, credenciales, PII | G1 y reglas no negociables |
| Exposición | Sin puerto al host; en local solo `127.0.0.1:6379` para el test de barrido | Reduce superficie sin bloquear I-S1-JF-03 |

Cambiar cualquiera de estas filas exige actualizar este ADR y el compose en el
mismo PR.

## Consecuencias

- Perder Redis (reinicio o `FLUSHALL`) pierde trabajos encolados, no datos:
  el outbox de PostgreSQL los regenera (J2.3). El test I-S2-JF-03 lo verificará.
- Con memoria llena, ARQ falla al encolar en vez de evacuar; J2.5 debe alertar.
- El test I-S1-JF-03 barre claves y tamaños contra el umbral de 4 KiB, y
  comprueba `aof_enabled=0`, `save` vacío y `noeviction`.
- Este ADR no decide políticas de proveedor, embeddings ni dimensiones de índice.
