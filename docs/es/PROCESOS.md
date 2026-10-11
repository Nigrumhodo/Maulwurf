# Diagramas de procesos — Maulwurf

> **Estado:** procesos derivados de [ESPECIFICACION.md](ESPECIFICACION.md) (módulos M1–M9 y §5),
> de [DISENO_BD_API_SPRINTS.md](DISENO_BD_API_SPRINTS.md) (§2.10 y §3) y de los planes
> [S1](plan/S1.md)–[S4](plan/S4.md), y contrastados con el código de `develop` en `2298e1b`
> (2026-10-11). No crean contratos: ante cualquier diferencia mandan la especificación y el
> [plan de implementación](PLAN_IMPLEMENTACION.md). La arquitectura está en [ARCH.md](ARCH.md).

## 1. Qué es un diagrama de procesos

Un **diagrama de procesos** describe un flujo de trabajo de principio a fin: quién actúa, en qué
orden, qué decisiones abren ramas y cómo termina cada una (éxito, error o cancelación). Es el
«cómo ocurre» de un caso de uso. No sustituye a los otros diagramas del proyecto:

| Diagrama | Responde | Dónde está |
|---|---|---|
| Arquitectura | Qué piezas existen y cómo se conectan | [ARCH.md](ARCH.md) |
| **Proceso** | Qué pasos, decisiones y ramas de error tiene un flujo | Este documento |
| Secuencia | Qué mensajes se intercambian los participantes y en qué orden | [ESPECIFICACION §5](ESPECIFICACION.md#5-pipeline-end-to-end) |
| Estados | Qué estados tiene una entidad y qué transiciones son válidas | [DISENO §2.10](DISENO_BD_API_SPRINTS.md#210-máquinas-de-estado-contratos-congelados) |
| Datos | Qué tablas y relaciones existen | [ESPECIFICACION §6.1](ESPECIFICACION.md#61-diagrama-er) |

## 2. Cómo leerlos

| Estilo | Significado |
|---|---|
| Relleno verde | Implementado y en uso |
| Relleno ámbar | Existe en el código, pero el proceso no funciona de extremo a extremo |
| Relleno gris, borde discontinuo | Solo especificado o planificado |
| Relleno rojo | Terminal de error o rechazo |
| Relleno azul | Actor o sistema externo |
| Rombo | Decisión; las ramas llevan su condición |
| Terminal redondeado | Resultado, con su código HTTP o estado |

| Proceso | Fase y sprint | Estado a `2298e1b` | SVG |
|---|---|---|---|
| P0 · Mapa de procesos | Todas | — | [proc-00-mapa.svg](diagrams/proc-00-mapa.svg) |
| P1 · Subida: reserva, recepción y dedupe | F1 · S2 | Parcial | [proc-01-subida.svg](diagrams/proc-01-subida.svg) |
| P2 · Transcripción, commit y limpieza | F1 · S2 | Parcial (biblioteca) | [proc-02-transcripcion.svg](diagrams/proc-02-transcripcion.svg) |
| P3 · Indexación y búsqueda híbrida | F1 · S2 | Planificado | [proc-03-indexacion-busqueda.svg](diagrams/proc-03-indexacion-busqueda.svg) |
| P4 · Chat con citas | F1 · S2 | Planificado | [proc-04-chat.svg](diagrams/proc-04-chat.svg) |
| P5 · Extracción, revisión y Calendar | F2 · S3 | Planificado | [proc-05-extraccion-calendar.svg](diagrams/proc-05-extraccion-calendar.svg) |
| P6 · Recordatorios y digests | F3 · S4 | Planificado | [proc-06-recordatorios.svg](diagrams/proc-06-recordatorios.svg) |
| P7 · Borrado de clase y de cuenta | S2 y S4 | Planificado | [proc-07-borrado.svg](diagrams/proc-07-borrado.svg) |
| P8 · Proceso del equipo | Todas | En vigor | [proc-08-equipo.svg](diagrams/proc-08-equipo.svg) |

## 3. P0 · Mapa de procesos

<!-- svg: proc-00-mapa -->
```mermaid
---
title: "P0 · Mapa de procesos de Maulwurf"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart TB
    subgraph F1["F1 · Conocimiento (S2)"]
        direction LR
        p1["P1 · Subir una clase<br/>reserva, recepción y dedupe"]:::parcial
        p2["P2 · Transcribir y borrar el<br/>audio<br/>commit y limpieza verificada"]:::parcial
        p3["P3 · Indexar y buscar<br/>índice híbrido"]:::plan
        p4["P4 · Chatear con citas<br/>respuesta en streaming"]:::plan
        p1 --> p2
        p2 --> p3
        p3 --> p4
        p2 -.->|"sin transcript:<br/>volver a subir"| p1
    end
    subgraph F2["F2 · Acción (S3)"]
        p5["P5 · Extraer, revisar y<br/>sincronizar<br/>con Google Calendar"]:::plan
    end
    subgraph F3["F3 · Recordatorios y cierre (S4)"]
        p6["P6 · Recordatorios y digests<br/>por Gmail"]:::plan
    end
    subgraph TR["Transversales"]
        direction LR
        p7["P7 · Borrar una clase o la<br/>cuenta<br/>S2 y S4"]:::plan
        p8["P8 · Proceso del equipo<br/>sprint, claim, PR y hecho"]:::impl
    end
    F1 -->|"transcript con<br/>limpieza verificada"| F2
    F2 -->|"tarea confirmada"| F3

    classDef impl fill:#e6f4ea,stroke:#2e7d32,color:#111
    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    style F1 fill:#fafafa,stroke:#9aa0a6,color:#111
    style F2 fill:#fafafa,stroke:#9aa0a6,color:#111
    style F3 fill:#fafafa,stroke:#9aa0a6,color:#111
    style TR fill:#fafafa,stroke:#9aa0a6,color:#111
```

El índice (P3) y el análisis (P5) cuelgan del mismo transcript y avanzan con estados
independientes: F1 no depende de la extracción de F2.

## 4. P1 · Subida de una clase: reserva, recepción y dedupe

<!-- svg: proc-01-subida -->
```mermaid
---
title: "P1 · Subida de una clase: reserva, recepción y deduplicación"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph F1["Fase 1 · Reserva<br/>POST /audios"]
        direction TB
        u0(["Estudiante elige materia,<br/>fecha, zona IANA e idioma y<br/>acepta el consentimiento"]):::ext
        a1["API valida sesión, CSRF,<br/>materia propia, zona, idioma<br/>de la allowlist y<br/>consentimiento versionado"]:::impl
        d1{"¿Datos válidos?"}:::impl
        e1(["422 consent_required,<br/>language_not_allowed o<br/>validation_failed"]):::err
        d2{"¿Cuota y slot?"}:::impl
        e2(["429 rate_limited o 503<br/>capacity_unavailable"]):::err
        a2["Crea clase e intento<br/>awaiting_upload. 201:<br/>audio_id, attempt_id, URL<br/>relativa y expiración"]:::impl
        u0 --> a1
        a1 --> d1
        d1 -->|"no"| e1
        d1 -->|"sí"| d2
        d2 -->|"no"| e2
        d2 -->|"sí"| a2
    end
    subgraph F2["Fase 2 · Recepción<br/>PUT /audios/{id}/content"]
        direction TB
        b1["API valida sesión, CSRF y<br/>que el intento esté vigente"]:::impl
        d3{"¿Intento válido?"}:::impl
        e3(["409 attempt_not_active o 410<br/>upload_expired"]):::err
        b2["Abre el sink de recepción<br/>(puerto C2)"]:::parcial
        d4{"¿Sink disponible?"}:::parcial
        e4(["503 capacity_unavailable<br/>antes de leer un byte. HOY:<br/>siempre"]):::hoy
        b3["Recibe en streaming sin<br/>spool, calcula el SHA-256 y<br/>corta al superar el límite"]:::parcial
        d5{"¿Tamaño y formato?"}:::parcial
        e5(["413 payload_too_large o 415<br/>unsupported_format"]):::err
        x1(["Desconexión: intento<br/>cancelled y limpieza"]):::err
        b1 --> d3
        d3 -->|"no"| e3
        d3 -->|"sí"| b2
        b2 --> d4
        d4 -->|"no"| e4
        d4 -->|"sí"| b3
        b3 --> d5
        d5 -->|"no"| e5
        b3 -.-> x1
    end
    subgraph F3["Fase 3 · Dedupe<br/>SHA-256 + idioma + materia + fecha + zona"]
        direction TB
        d6{"¿SHA-256 ya conocido para el<br/>tenant?"}:::parcial
        b4["Descarta el temporal y<br/>verifica la limpieza antes<br/>de responder"]:::parcial
        d7{"¿Identidad idéntica?"}:::parcial
        r1(["200 duplicate_exact: clase<br/>canónica y reserva liberada"]):::parcial
        r2(["409 duplicate_variant:<br/>existing_audio_id y token de<br/>un solo uso"]):::parcial
        c1["El estudiante confirma: un<br/>segundo POST consume el<br/>token y repite el PUT"]:::ext
        d8{"¿Mismo SHA-256?"}:::parcial
        e6(["422 variant_hash_mismatch:<br/>rejected, sin ASR"]):::err
        ok(["202 admitido por el<br/>supervisor: receiving →<br/>transcribing (P2)"]):::parcial
        d6 -->|"no"| ok
        d6 -->|"sí"| b4
        b4 --> d7
        d7 -->|"sí"| r1
        d7 -->|"no"| r2
        r2 --> c1
        c1 --> d8
        d8 -->|"no"| e6
        d8 -->|"sí"| ok
    end
    F1 -->|"201: sube el archivo"| F2
    F2 -->|"tamaño y formato OK"| F3

    classDef impl fill:#e6f4ea,stroke:#2e7d32,color:#111
    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    classDef hoy fill:#fff4d6,stroke:#b26a00,stroke-width:4px,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style F1 fill:#fafafa,stroke:#9aa0a6,color:#111
    style F2 fill:#fafafa,stroke:#9aa0a6,color:#111
    style F3 fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Estado: parcial.** `POST /audios` (A2.3) y las validaciones del `PUT` (A2.4) están
implementadas. Como el sink de recepción no tiene implementación real, el `PUT` responde `503`
antes de leer el cuerpo. El resto de la fase 2 y toda la fase 3 existen y están probados con un
sink inyectado (`test_put_content_dedupe.py`), pero no son alcanzables hoy.

| Regla | Detalle |
|---|---|
| Identidad | SHA-256, idioma, materia, fecha y zona horaria; el título y el profesor no participan |
| Orden en duplicados | Primero se descarta el temporal y se verifica, después se responde `200` o `409` |
| Cuota y slot | `429` y `503` son mecanismos condicionales (A2.3, D4): sin configuración no limitan |
| Token de variante | Opaco, de un solo uso y con TTL; en la BD solo vive su hash, ligado a tenant, hash y metadatos |
| Variante confirmada | No muta la evidencia ni las tareas de la clase existente |
| Cierre de la pestaña | Antes del `202` cancela; después **no** cancela el ASR (ver P2) |

## 5. P2 · Transcripción, commit transaccional y limpieza verificada

<!-- svg: proc-02-transcripcion -->
```mermaid
---
title: "P2 · Transcripción, commit transaccional y limpieza verificada"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph A["Preparación<br/>supervisor con lease y fencing"]
        direction TB
        in0(["Intento admitido, con el<br/>audio en el tmpfs de la<br/>ingesta (viene de P1)"]):::parcial
        i1["Toma el intento: lease con<br/>token de fencing y admisión<br/>por capacidad"]:::parcial
        i2["ffprobe valida contenedor,<br/>duración y streams. ffmpeg<br/>acotado normaliza a WAV mono"]:::parcial
        d1{"¿Formato válido?"}:::parcial
        r1(["rejected: formato inválido"]):::err
        i3["Fragmenta en trozos de hasta<br/>30 s con overlap de 0,25 s,<br/>un fragmento activo a la vez"]:::parcial
        in0 --> i1
        i1 --> i2
        i2 --> d1
        d1 -->|"no"| r1
        d1 -->|"sí"| i3
    end
    subgraph B["Transcripción<br/>por fragmentos"]
        direction TB
        i4["Envía el fragmento a Riva<br/>por gRPC TLS con el idioma<br/>elegido, nunca translate"]:::parcial
        d2{"¿Respuesta?"}:::parcial
        d3{"¿Transitorio, con intentos y<br/>deadline?"}:::parcial
        r2(["requires_reupload: sin audio<br/>no hay retranscripción"]):::err
        d4{"¿Quedan fragmentos?"}:::parcial
        i6["Reconcilia fronteras y<br/>overlap: offsets globales y<br/>timestamp_precision"]:::parcial
        d5{"¿Texto utilizable?"}:::parcial
        r3(["rejected: no_usable_text<br/>(silencio o vacío)"]):::err
        cx(["cancelled: cancelación antes<br/>del commit"]):::err
        i4 --> d2
        d2 -->|"error"| d3
        d2 -->|"transcrito"| d4
        d3 -->|"sí: reintenta, máx.<br/>3"| i4
        d3 -->|"no"| r2
        d4 -->|"sí"| i4
        d4 -->|"no"| i6
        i6 --> d5
        d5 -->|"no"| r3
        i4 -.->|"POST …/cancel"| cx
    end
    subgraph C["Commit y cierre<br/>el cierre corre siempre, también tras rejected, requires_reupload o cancelled"]
        direction TB
        c1["Commit atómico: transcript,<br/>segmentos, idioma, modelo,<br/>versión y precisión, más<br/>eventos index y analyze<br/>deshabilitados con<br/>cleanup_pending. Intento:<br/>transcript_committed_cleanup_pending.<br/>Hoy solo cambia el estado y<br/>guarda conteos"]:::parcial
        k1["Cierra buffers y clientes,<br/>mata el grupo de procesos y<br/>borra original, convertidos<br/>y fragmentos"]:::parcial
        k2["Verifica en /proc<br/>descriptores, procesos y<br/>mounts, y que la ruta ya no<br/>existe: cleanup_status =<br/>verified"]:::parcial
        d6{"¿Limpieza verificada?"}:::parcial
        ok(["succeeded: el supervisor ya<br/>lo escribe. Habilitar la<br/>outbox (index y, desde F2,<br/>analyze) está pendiente<br/>(S2.5 y S2.6)"]):::parcial
        pend(["Sigue<br/>transcript_committed_cleanup_pending,<br/>outbox bloqueada"]):::parcial
        rec["Reconciliador (S2.7):<br/>verifica la limpieza y<br/>habilita la outbox sin<br/>retranscribir. Si se pierde<br/>el host, exige evidencia de<br/>destrucción del tmpfs"]:::plan
        c1 --> k1
        k1 --> k2
        k2 --> d6
        d6 -->|"sí"| ok
        d6 -->|"no, o el proceso<br/>muere"| pend
        pend --> rec
        rec --> ok
    end
    A -->|"formato válido"| B
    B -->|"hay texto"| C

    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    style A fill:#fafafa,stroke:#9aa0a6,color:#111
    style B fill:#fafafa,stroke:#9aa0a6,color:#111
    style C fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Estado: parcial.** El supervisor, el lease con fencing, ffprobe y ffmpeg, la fragmentación, la
reconciliación, el cliente de Riva y la verificación de limpieza existen como biblioteca con
tests, pero `main` no los importa y `asr_job` es un stub que no llama a Riva. El supervisor ya
escribe `transcript_committed_cleanup_pending` y `succeeded`, pero sin persistir el
transcript ni habilitar la outbox (S2.5, A2.10 y S2.6); el reconciliador no existe (S2.7).

**Reintentar o volver a subir** (A2.8). Ambos endpoints están especificados y ninguno existe aún:

| Situación | Acción | Respuesta |
|---|---|---|
| Hay transcript utilizable | `POST /audios/{id}/retry` con `{stages, transcript_version}` (`index` o `analyze`): runs idempotentes sobre el texto. El botón dice «reintentar análisis/índice» | `202`; `409 transcript_unavailable` o `409 invalid_transition` |
| No hay transcript utilizable | `POST /audios/{id}/reupload`: nueva sesión efímera con la misma identidad; el estudiante vuelve a subir. El botón dice «volver a subir» | `201` con URL nueva; `409` si ya hay transcript |

**Cancelación** (S2.8, A2.7):

| Momento | Resultado |
|---|---|
| Desconexión durante la recepción | Cancela y limpia |
| Tras el `202` | Continúa bajo lease; cerrar la pestaña o el SSE **no** cancela |
| `POST /audios/{id}/attempts/{attempt_id}/cancel` antes del commit | Invalida el fencing; el supervisor cancela el subproceso y el gRPC, y limpia. `202` |
| Cancelar tras el commit | `409` con el estado actual; el transcript no se destruye |

**Otras reglas.** Si la evidencia de limpieza falla, la instancia cierra la admisión de uploads
nuevos y dispara una alerta (S1.B6); un lease vencido que deja `pending` solo bloquea la outbox. Si
el contenedor muere (`SIGKILL`), el tmpfs desaparece con él y el intento queda con lease vencido y
`cleanup_status = pending`. Un TTL no equivale a borrado. El estado final de un error **no
transitorio** del proveedor no está especificado.

## 6. P3 · Indexación del texto y búsqueda híbrida

<!-- svg: proc-03-indexacion-busqueda -->
```mermaid
---
title: "P3 · Indexación del texto y búsqueda híbrida"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph IDX["Indexación<br/>job index(audio_id) desde el texto persistido"]
        direction TB
        o1["El dispatcher del scheduler<br/>toma los eventos habilitados<br/>y encola solo IDs y versión<br/>en Redis"]:::parcial
        w1["El worker revalida tenant,<br/>tombstone y versión del<br/>transcript"]:::plan
        d1{"¿Sigue vigente?"}:::plan
        x1(["Descarta: no publica nada"]):::err
        w2["Chunking de unos 800 tokens<br/>con overlap de unos 100,<br/>offsets Unicode y vínculos<br/>chunk_segments"]:::plan
        w3["Embeddings por lotes con<br/>modelo, dimensión y versión<br/>registrados"]:::plan
        w4["Construye la generación de<br/>índice completa (building) y<br/>la activa de forma atómica:<br/>la anterior pasa a retired"]:::plan
        d2{"¿Falló la etapa?"}:::plan
        w5["Fallo transitorio: hasta 3<br/>reintentos con backoff y<br/>jitter"]:::plan
        f1(["Etapa index = failed.<br/>Reintento desde el texto:<br/>POST /audios/{id}/retry"]):::err
        ok1(["Etapa index = succeeded"]):::plan
        o1 --> w1
        w1 --> d1
        d1 -->|"no"| x1
        d1 -->|"sí"| w2
        w2 --> w3
        w3 --> w4
        w4 --> d2
        d2 -->|"no"| ok1
        d2 -->|"sí"| w5
        w5 -->|"reintento"| w2
        w5 -->|"se agotan"| f1
    end
    subgraph QRY["Búsqueda<br/>GET /search?q=&subject_id=&from=&to=&limit=&cursor="]
        direction TB
        q1["La API valida la sesión y<br/>los filtros"]:::plan
        q2["Rama léxica: tsvector por<br/>idioma"]:::plan
        q3["Rama vectorial: pgvector,<br/>distancia coseno, HNSW"]:::plan
        q4["Cada rama filtra user_id,<br/>recurso activo, versión,<br/>tombstones y filtros pedidos<br/>antes del top-k: unos 30<br/>candidatos por rama"]:::plan
        q5["Fusión RRF determinista,<br/>deduplicación y diversidad.<br/>Orden: score_rrf descendente<br/>y chunk_id"]:::plan
        q6(["Resultados paginados con<br/>cita Materia · Clase · mm:ss<br/>o «segmento N» si no hay<br/>tiempos fiables"]):::plan
        q1 --> q2
        q1 --> q3
        q2 --> q4
        q3 --> q4
        q4 --> q5
        q5 --> q6
    end
    IDX -.->|"el índice activo<br/>alimenta la búsqueda"| QRY

    classDef parcial fill:#fff4d6,stroke:#b26a00,color:#111
    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    style IDX fill:#fafafa,stroke:#9aa0a6,color:#111
    style QRY fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Estado: planificado** (L2.1–L2.4, J2.1–J2.2, A2.11). Hoy existen los modelos de chunks y
embeddings, el dispatcher y el gate de limpieza; `index` y `analyze` son no-op y no hay ruta
`GET /search`. Reglas del diseño:

- Un evento `index_requested` solo se despacha si el último intento tiene `cleanup_status` en
  `verified`.
- Los embeddings tienen modelo y dimensión fijos por generación de índice; cambiarlos exige una
  generación nueva y reindexar, nunca mezclar vectores.
- Sin D3-Texto cerrada para el proveedor, solo se indexa texto sintético o redactado.
- El valor de `k` de RRF y el código HTTP de `GET /search` no están especificados.

## 7. P4 · Chat con citas verificadas

<!-- svg: proc-04-chat -->
```mermaid
---
title: "P4 · Chat con citas verificadas (SSE)"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph A["Entrada"]
        direction TB
        u0(["Estudiante escribe una<br/>pregunta. Ámbito: todas las<br/>clases, una materia o una<br/>clase"]):::ext
        a0["Si no hay conversación: POST<br/>/chat/conversations. 201 o<br/>422 chat_mode_invalid"]:::plan
        a1["POST<br/>/chat/conversations/{id}/messages<br/>con client_message_id. 200<br/>text/event-stream, consumido<br/>con fetch"]:::plan
        d0{"¿client_message_id ya usado?"}:::plan
        r0(["Devuelve el mensaje<br/>guardado, sin volver a<br/>llamar al LLM"]):::plan
        a2["Guarda el mensaje del<br/>usuario y emite snapshot"]:::plan
        u0 --> a0
        a0 --> a1
        a1 --> d0
        d0 -->|"sí"| r0
        d0 -->|"no"| a2
    end
    subgraph B["Recuperación y contexto"]
        direction TB
        a3["Embedding de la pregunta y<br/>búsqueda híbrida (P3) con<br/>filtros de tenant, versión y<br/>tombstones"]:::plan
        d1{"¿Hay evidencia suficiente?"}:::plan
        e1(["error insufficient_evidence:<br/>abstención, pide más<br/>contexto y no inventa<br/>fuentes ni fechas"]):::err
        a4["Arma el contexto: hasta unos<br/>8 chunks bajo ventana del<br/>modelo menos reserva de<br/>salida y margen. Recorta el<br/>historial por turnos<br/>completos"]:::plan
        d2{"¿Cabe el mínimo?"}:::plan
        e2(["error context_exceeded"]):::err
        a3 --> d1
        d1 -->|"no"| e1
        d1 -->|"sí"| a4
        a4 --> d2
        d2 -->|"no"| e2
    end
    subgraph C["Respuesta y citas"]
        direction TB
        a5["El LLM responde con prompt<br/>de tutor: transcript y<br/>resultados de tools son<br/>datos no confiables. La API<br/>emite delta"]:::plan
        a6["Los marcadores de cita<br/>esperan en buffer hasta<br/>validar en la BD que ID,<br/>chunk y segmento existen y<br/>son del tenant y la versión<br/>activa"]:::plan
        d3{"¿Cita válida?"}:::plan
        x3["No se renderiza como fuente"]:::plan
        a7["Emite citation: Materia ·<br/>Clase · mm:ss, con enlace a<br/>/biblioteca/{audio_id}?segment=…<br/>o «segmento N»"]:::plan
        a8["Persiste messages y<br/>message_sources con TODAS<br/>las fuentes entregadas al<br/>modelo"]:::plan
        ok(["Evento done, único terminal:<br/>el mensaje queda completed"]):::plan
        can(["Cancelación: POST<br/>…/messages/{message_id}/cancel,<br/>202. El parcial queda<br/>cancelled"]):::plan
        a5 --> a6
        a6 --> d3
        d3 -->|"sí"| a7
        d3 -->|"no"| x3
        a7 --> a8
        x3 --> a8
        a8 --> ok
        a5 -.->|"el estudiante<br/>cancela"| can
    end
    A -->|"mensaje nuevo"| B
    B -->|"contexto listo"| C

    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style A fill:#fafafa,stroke:#9aa0a6,color:#111
    style B fill:#fafafa,stroke:#9aa0a6,color:#111
    style C fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Estado: planificado** (L2.5–L2.6, A2.12, D2.5–D2.7). Hay modelos de conversaciones y mensajes,
pero ninguna ruta de chat.

| Aspecto | Contrato |
|---|---|
| Eventos SSE | `snapshot` inicial, luego `delta` y `citation`, y exactamente un terminal `done` o `error`; todos con ID monótono |
| Reconexión | Latidos `: ping` entre deltas; `Last-Event-ID` permite omitir lo ya enviado |
| Estados del mensaje | `streaming`, `completed`, `failed` y `cancelled` |
| Idempotencia | Repetir `client_message_id` devuelve el mensaje guardado |
| Sin tiempos fiables | La cita muestra «segmento N», nunca un minuto inventado (D6) |
| Fuentes | Se registran todas las que recibió el modelo, también las heredadas del historial |

## 8. P5 · Extracción de actividades, revisión humana y Google Calendar

<!-- svg: proc-05-extraccion-calendar -->
```mermaid
---
title: "P5 · Extracción de actividades, revisión humana y Google Calendar"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph ANA["Análisis<br/>job analyze(audio_id)"]
        direction TB
        o1["Outbox analyze_requested,<br/>habilitada tras la limpieza<br/>verificada. El worker toma<br/>el job"]:::plan
        w1["Ventanas con overlap y<br/>deduplicación por misma<br/>evidencia. Contexto: fecha y<br/>zona de la clase, no la de<br/>subida"]:::plan
        w2["LLM con prompt versionado y<br/>salida Pydantic:<br/>actividades, fechas, spans,<br/>cita literal, resumen y<br/>temas"]:::plan
        v1["Validación determinista:<br/>enums y rangos, segmento de<br/>la clase, tenant y versión,<br/>offsets Unicode y cita<br/>literal en el texto<br/>reconstruido desde la BD"]:::plan
        d1{"¿Propuesta válida?"}:::plan
        x1(["Se descarta: sin resultados<br/>parciales ni se pisan tareas<br/>editadas o confirmadas"]):::err
        d2{"date_status"}:::plan
        n1["resolved: tarea pending"]:::plan
        n2["ambiguous o missing:<br/>needs_review. Nunca se<br/>inventa la medianoche"]:::plan
        o1 --> w1
        w1 --> w2
        w2 --> v1
        v1 --> d1
        d1 -->|"no"| x1
        d1 -->|"sí"| d2
        d2 -->|"resolved"| n1
        d2 -->|"ambiguous o missing"| n2
    end
    subgraph REV["Revisión humana<br/>bandeja"]
        direction TB
        rv["El estudiante revisa<br/>evidencia, fecha, confianza<br/>y advertencia ASR. Edita,<br/>descarta (dismissed) o<br/>confirma"]:::plan
        d3{"¿Versión, fecha, bloque y<br/>evidencia válidos?"}:::plan
        e3(["409 version_conflict o 422<br/>fecha o evidencia inválida"]):::err
        cf["Transacción atómica:<br/>confirmed y versión + 1,<br/>outbox por task_id y<br/>due_version. 202 con<br/>calendar_sync_status =<br/>pending"]:::plan
        rv --> d3
        d3 -->|"no"| e3
        d3 -->|"sí"| cf
    end
    subgraph CAL["Sincronización con Google Calendar"]
        direction TB
        d4{"¿Google conectado?"}:::plan
        bl(["calendar_sync_status =<br/>blocked"]):::plan
        sy["Job sync_task_event en el<br/>calendario secundario<br/>«Maulwurf»: ID de evento<br/>determinista y ETag para no<br/>pisar cambios externos.<br/>Timeout: consulta por ID.<br/>409: reconcilia. Versión<br/>obsoleta: descarta"]:::plan
        d5{"¿Resultado?"}:::plan
        s1(["synced"]):::plan
        f1(["failed: retry explícito de<br/>la versión vigente, vuelve a<br/>pending"]):::err
        ig["invalid_grant: la<br/>integración pasa a<br/>disconnected y las tareas<br/>pendientes a blocked"]:::plan
        d4 -->|"no"| bl
        d4 -->|"sí"| sy
        sy --> d5
        d5 -->|"ok"| s1
        d5 -->|"error"| f1
        d5 -->|"invalid_grant"| ig
        ig --> bl
        bl -.->|"reconnect: reactiva<br/>las de la versión<br/>vigente"| sy
    end
    ANA -->|"propuesta pending o<br/>needs_review"| REV
    REV -->|"confirmada"| CAL
    CAL -.->|"editar una tarea<br/>confirmada: no<br/>escribe hasta<br/>reconfirmar"| REV

    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    style ANA fill:#fafafa,stroke:#9aa0a6,color:#111
    style REV fill:#fafafa,stroke:#9aa0a6,color:#111
    style CAL fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Estado: planificado** (S3). No hay rutas, jobs ni servicios de análisis, tareas o Calendar; el
sync de Calendar figura en `services/outbox` sin consumidor y existe un esquema Pydantic de
análisis en borrador.

| Aspecto | Contrato |
|---|---|
| Estados de tarea | `pending`, `needs_review`, `confirmed`, `dismissed` y `completed`; `scheduled` no es un estado |
| `calendar_sync_status` | `not_requested`, `pending`, `synced`, `failed` y `blocked` |
| Escritura en Calendar | Solo tras la confirmación humana; los tools del chat nunca escriben en Calendar |
| Edición posterior | Editar una tarea confirmada crea una revisión y no escribe en Google hasta reconfirmar |
| Reintento | `failed` solo vuelve a `pending` con un retry explícito de la versión vigente |

## 9. P6 · Recordatorios y digests por Gmail

<!-- svg: proc-06-recordatorios -->
```mermaid
---
title: "P6 · Recordatorios y digests por Gmail"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph A["Programación"]
        direction TB
        c0(["Estudiante confirma una<br/>tarea (P5) y activa Gmail<br/>con PATCH /me/notifications.<br/>El opt-in está desactivado<br/>por defecto"]):::ext
        d0{"¿La tarea tiene fecha?"}:::plan
        n0(["Sin recordatorios escalados"]):::plan
        s1["Programa ocurrencias con<br/>dedupe_key: user_id, tipo,<br/>task_id, due_version y<br/>occurrence. Con hora: T-48<br/>h, T-24 h y T-2 h. Solo<br/>fecha: T-3 d y T-1 d a la<br/>hora local"]:::plan
        s2["Cambiar el vencimiento o las<br/>preferencias cancela las<br/>ocurrencias futuras<br/>obsoletas. Nunca envía de<br/>golpe los umbrales ya<br/>vencidos"]:::plan
        c0 --> d0
        d0 -->|"no"| n0
        d0 -->|"sí"| s1
        s1 -.-> s2
    end
    subgraph B["Envío"]
        direction TB
        sc["Scheduler ARQ con un único<br/>rol activo por lease en la<br/>BD. Cada minuto calcula<br/>vencimientos locales y<br/>reclama filas de forma<br/>atómica"]:::plan
        dg["Digest diario (07:00<br/>configurable) y semanal<br/>(domingo por la noche): una<br/>ejecución por usuario y<br/>fecha local, solo con opt-in"]:::plan
        snd["Estado sending: Gmail API<br/>con gmail.send, solo al<br/>email verificado del usuario"]:::plan
        d1{"¿Resultado?"}:::plan
        sent(["sent"]):::plan
        fail(["failed"]):::err
        unk(["delivery_unknown: respuesta<br/>perdida o lease de sending<br/>vencido. NO se reintenta<br/>solo"]):::err
        sc --> snd
        sc -->|"también evalúa los<br/>digests"| dg
        dg --> snd
        snd --> d1
        d1 -->|"Gmail acepta"| sent
        d1 -->|"error"| fail
        d1 -->|"sin respuesta"| unk
    end
    subgraph C["Entrega incierta y reenvío"]
        direction TB
        pan["El panel GET /notifications<br/>explica delivery_unknown"]:::plan
        rs["Reenvío manual: POST<br/>/notifications/{id}/resend<br/>con<br/>acknowledge_duplicate_risk =<br/>true"]:::plan
        nw["Crea una ocurrencia auditada<br/>nueva. La original nunca<br/>vuelve a pending"]:::plan
        pan --> rs
        rs --> nw
    end
    A -->|"la ocurrencia vence"| B
    B -->|"delivery_unknown"| C

    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style A fill:#fafafa,stroke:#9aa0a6,color:#111
    style B fill:#fafafa,stroke:#9aa0a6,color:#111
    style C fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Estado: planificado** (S4: J4.1–J4.6, A4.1). `me.py` declara que las preferencias de Gmail y de
los digests llegan en S4; no hay jobs de notificación, solo los tipos de evento `notify` y
`send_digest` catalogados en la outbox.

| Aspecto | Contrato |
|---|---|
| Estados | `pending`, `sending`, `sent`, `failed`, `delivery_unknown` y `cancelled` |
| Entrega | Gmail no ofrece exactly-once con `gmail.send`; un `Message-ID` estable no garantiza dedupe remota |
| Objetivo | Envío dentro de 15 minutos, no exactitud al segundo; varios workers protegidos por unicidad y leases |
| Fechas sin hora | T-3 d y T-1 d a la hora local; una hora inexistente por DST pasa al siguiente instante válido y una repetida se ejecuta una sola vez |
| Correo | Plantillas HTML y texto plano con contenido escapado; «Posponer» abre una UI autenticada y ningún GET de correo muta estado |
| Sin especificar | Cómo se recupera una entrega `failed` |

## 10. P7 · Borrado de una clase y de la cuenta

<!-- svg: proc-07-borrado -->
```mermaid
---
title: "P7 · Borrado de una clase y de la cuenta"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph CLA["Borrar una clase<br/>DELETE /audios/{id}, 202 (S2)"]
        direction TB
        c1["Tombstone: cancela los<br/>intentos y jobs de esa clase"]:::plan
        c2["Borra transcript, chunks,<br/>embeddings, tareas<br/>derivadas, resumen y citas.<br/>Los mensajes que las citan<br/>se purgan o pasan a<br/>tombstone sin contenido"]:::plan
        c3["Los eventos de Google ya<br/>creados se conservan por<br/>defecto, con aviso.<br/>Borrarlos exige una opción<br/>explícita vía<br/>google_remote_operations"]:::plan
        c1 --> c2
        c2 --> c3
    end
    subgraph CT1["Borrar la cuenta · inicio<br/>DELETE /me, 202 (S4)"]
        direction TB
        u0(["Estudiante pide borrar su<br/>cuenta y decide si se borran<br/>los eventos de Maulwurf:<br/>delete_maulwurf_events"]):::ext
        a1["Crea account_deletions y<br/>responde 202 con<br/>deletion_operation_id"]:::plan
        a2["Aplica tombstone y bloquea<br/>sesiones y jobs. La sesión<br/>iniciadora queda restringida<br/>a GET /me/deletion/{id} y<br/>POST /auth/logout. Las demás<br/>se revocan"]:::plan
        st(["Mientras dura: GET<br/>/me/deletion/{id} devuelve<br/>pending"]):::plan
        u0 --> a1
        a1 --> a2
        a2 -.-> st
    end
    subgraph CT2["Borrar la cuenta · ejecución"]
        direction TB
        d1{"¿Borrar eventos en Google?"}:::plan
        g1["Ejecuta o encola los<br/>borrados remotos ANTES de<br/>revocar los tokens:<br/>delete_event y<br/>delete_calendar, con estados<br/>pending, running, succeeded<br/>o failed"]:::plan
        p1["Purga los datos y derivados<br/>del usuario"]:::plan
        p2["Elimina los tokens"]:::plan
        d2{"¿Falló algún borrado remoto?"}:::plan
        r1(["GET /me/deletion/{id}<br/>devuelve completed"]):::plan
        r2(["completed_with_remote_failures"]):::err
        rs["Queda un registro residual<br/>no sensible para consultar<br/>el resultado, sin retener<br/>perfil ni texto"]:::plan
        bk["Backups: solo texto y<br/>metadatos cifrados, 30 días<br/>como máximo propuestos. Al<br/>restaurar se reaplican los<br/>tombstones"]:::plan
        d1 -->|"sí: autorizado y con<br/>credenciales"| g1
        d1 -->|"no"| p1
        g1 --> p1
        p1 --> p2
        p2 --> d2
        d2 -->|"no"| r1
        d2 -->|"sí"| r2
        r1 --> rs
        r2 --> rs
        p1 -.->|"los backups caducan"| bk
    end
    CT1 -->|"operación creada"| CT2

    classDef plan fill:#f3f3f3,stroke:#777,stroke-dasharray:5 4,color:#111
    classDef err fill:#fde8e8,stroke:#c0392b,color:#111
    classDef ext fill:#e8eefc,stroke:#3b5bdb,color:#111
    style CLA fill:#fafafa,stroke:#9aa0a6,color:#111
    style CT1 fill:#fafafa,stroke:#9aa0a6,color:#111
    style CT2 fill:#fafafa,stroke:#9aa0a6,color:#111
```

**Estado: planificado.** El borrado de una clase es A2.7 (S2) y el de la cuenta es A4.4 (S4);
`routers/audios.py` solo implementa `POST` y `PUT`, y `routers/me.py` solo `GET` y `PATCH`.

| Aspecto | Contrato |
|---|---|
| Eventos de Google | No se borran eventos ajenos ni sin autorización; sin permisos no se garantiza el borrado en Google |
| Registro residual | `account_deletions` conserva solo lo necesario para informar el resultado, sin perfil ni texto |
| Estados de la operación | `pending`, `completed` y `completed_with_remote_failures` |
| Backups | Solo texto y metadatos cifrados; los borrados se reaplican al restaurar (se valida en F3) |

## 11. P8 · Proceso del equipo: sprint, claim, PR y definición de hecho

<!-- svg: proc-08-equipo -->
```mermaid
---
title: "P8 · Proceso del equipo: sprint, claim, PR y definición de hecho"
config:
  flowchart:
    wrappingWidth: 330
    nodeSpacing: 24
    rankSpacing: 30
    padding: 8
---
flowchart LR
    subgraph A["Lunes: abrir el sprint y reclamar"]
        direction TB
        m0["Lunes, 45 min, todo el<br/>equipo: el líder verifica la<br/>puerta anterior, la<br/>capacidad y la<br/>disponibilidad"]:::impl
        m1["Abre el sprint y congela las<br/>interfaces. El proveedor<br/>entrega contrato o mock el<br/>lunes y el código el día 3<br/>como máximo"]:::impl
        m2["Cada miembro reclama tickets<br/>con un PR que toca solo su<br/>fila de<br/>docs/sprints/CLAIMS.md:<br/>owner único, claimed_at UTC,<br/>estado y elegibilidad cloud"]:::impl
        d1{"¿Pruebas P-* o llamadas<br/>reales a proveedores?"}:::impl
        n1["Exige elegibilidad cloud<br/>verificada, con fecha y<br/>revisor"]:::impl
        d2{"¿Alguien reclamó el ticket?"}:::impl
        ld["El líder lo resuelve:<br/>reasigna, divide con IDs<br/>hijos o reduce el DoD de<br/>forma explícita"]:::impl
        m0 --> m1
        m1 --> m2
        m2 --> d1
        d1 -->|"sí"| n1
        d1 -->|"no"| d2
        n1 --> d2
        d2 -->|"no"| ld
    end
    subgraph B["Desarrollo y PR"]
        direction TB
        w["Desarrollo: un PR por<br/>ticket, con base en develop"]:::impl
        d3{"¿Cambia un contrato?"}:::impl
        sp["Actualiza ESPECIFICACION.md<br/>y PLAN_IMPLEMENTACION.md en<br/>el mismo PR"]:::impl
        ci["CI quality: python-static,<br/>python-unit, web,<br/>integration y build-images.<br/>Las pruebas P-* y load no<br/>corren en PR"]:::impl
        rv["Mínimo 1 review. Si cruza de<br/>área, revisa quien tenga<br/>claim allí o el líder"]:::impl
        mg["Merge a develop"]:::impl
        rt["Diario de 15 min. Día 3:<br/>primera integración<br/>consumible. Miércoles:<br/>integración cruzada y paso<br/>de mocks a código real"]:::impl
        w --> d3
        d3 -->|"sí"| sp
        d3 -->|"no"| ci
        sp --> ci
        ci --> rv
        rv --> mg
        w -.-> rt
    end
    subgraph C["Cierre del ticket y del sprint"]
        direction TB
        ev["Evidencia en la bitácora y<br/>en la fila de CLAIMS.md:<br/>comando, fecha UTC, entorno<br/>y resultado"]:::impl
        dd(["Ticket hecho: código, tests,<br/>contrato o documento<br/>actualizado, demostrable en<br/>el compose local y con<br/>evidencia"]):::impl
        fr["Viernes, 45 min: demo,<br/>verificación del DoD del<br/>sprint, retro y cierre de<br/>bitácoras"]:::impl
        ev --> dd
        dd --> fr
    end
    A -->|"ticket reclamado"| B
    B -->|"merge"| C

    classDef impl fill:#e6f4ea,stroke:#2e7d32,color:#111
    style A fill:#fafafa,stroke:#9aa0a6,color:#111
    style B fill:#fafafa,stroke:#9aa0a6,color:#111
    style C fill:#fafafa,stroke:#9aa0a6,color:#111
```

Fuentes: [sprints/README.md](sprints/README.md), [PLAN_SPRINTS §0 y §7](PLAN_SPRINTS.md),
[ADR-0004](adr/ADR-0004-commits-pr.md), la plantilla de PR y `.github/workflows/quality.yml`.
Transferir o abandonar un ticket exige un PR atómico con el motivo y la entrega de evidencia.

## 12. Discrepancias entre documentos

Al dibujar los procesos aparecieron inconsistencias que conviene resolver. Cada diagrama sigue la
columna indicada.

| Tema | Un documento dice | Otro dice | Este documento sigue |
|---|---|---|---|
| Destino del `PUT` | ESPECIFICACION §5: la web sube a ingesta | `infra/Caddyfile` y `routers/audios.py`: lo recibe `api` y el sink C2 está pendiente | El código |
| Lease al reservar | `plan/S2.md` (A2.3): reserva «con lease» | `services/audios.py`: `lease_expires_at` queda `NULL` hasta que ingesta toma el intento | El código |
| Evento de outbox de Calendar | DISENO §2.6: `sync_task_event` | `plan/S3.md` (A3.4): `calendar.event.sync` | Solo nombra el job `sync_task_event` |
| Orden de los eventos SSE | ESPECIFICACION M7: `delta*` y luego `citation*` | DISENO §3.10: `delta` y `citation` en cualquier orden | No fija el orden |
| Contenido del digest semanal | ESPECIFICACION M8: incluye las tareas nuevas | `plan/S4.md` (J4.6): solo próximas y conflictos | No detalla el contenido |
| Operaciones remotas tras la purga | DISENO §2.8: `google_remote_operations` sobrevive a la purga | La misma tabla declara `user_id ... ON DELETE CASCADE` | La prosa; por resolver en A4.4 |

## 13. Mantenimiento

Los diagramas son bloques Mermaid precedidos de `<!-- svg: nombre -->`. Tras editarlos, ejecuta
`python3 scripts/docs/export_diagrams.py` para regenerar `docs/diagrams/*.svg` (ver
[ARCH.md §8](ARCH.md#8-mantenimiento)). Al fusionar un ticket que cambie lo implementado,
actualiza los colores, las tablas de estado y el commit de la cabecera.
