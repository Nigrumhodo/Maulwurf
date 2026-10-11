# Propuesta general del proyecto final

## Maulwurf: asistente inteligente para el estudio a partir de grabaciones de clase

### 1. Descripción general del sistema

Maulwurf será un asistente web orientado a estudiantes que permitirá convertir grabaciones de
clases en conocimiento organizado y consultable. El problema que se busca resolver es que los
estudiantes suelen acumular horas de audio, pero rara vez vuelven a escucharlas completas. Como
consecuencia, información importante —como fechas de exámenes, tareas, lecturas o
recomendaciones del profesor— queda escondida dentro de las grabaciones.

El sistema resolverá este problema mediante un flujo automático. Primero, el estudiante cargará
una grabación junto con la materia, la fecha de la clase, la zona horaria y el idioma del audio.
La grabación se procesará de forma temporal mediante NVIDIA Riva para obtener una transcripción.
El audio no se conservará permanentemente: únicamente se utilizarán archivos temporales en
memoria durante la recepción, conversión y transcripción.

Una vez obtenida la transcripción, el sistema la dividirá en fragmentos y generará embeddings
para almacenarlos en una base de datos vectorial. Esto permitirá realizar búsquedas híbridas,
combinando búsqueda semántica y búsqueda textual. Sobre esta información se construirá un chat
RAG (*Retrieval-Augmented Generation* o generación aumentada por recuperación), capaz de
responder preguntas utilizando las transcripciones del estudiante y mostrando citas que indiquen
el segmento utilizado como evidencia.

Además, un modelo de lenguaje analizará las transcripciones para detectar tareas, exámenes,
entregas, lecturas y recomendaciones. Estas detecciones se presentarán como propuestas
revisables. El estudiante deberá confirmar explícitamente una propuesta antes de crear un evento
en Google Calendar. De forma opcional, el sistema podrá enviar recordatorios y resúmenes por
correo electrónico mediante Gmail.

El proyecto priorizará la privacidad y la trazabilidad. No se ofrecerá reproducción ni descarga
del audio desde la aplicación, las fechas extraídas se podrán revisar antes de confirmarlas y el
sistema no inventará timestamps cuando el proveedor de transcripción no entregue información
temporal confiable.

### Flujo principal

1. El estudiante inicia sesión y registra una materia.
2. Selecciona la fecha, zona horaria e idioma de la clase y carga el audio.
3. El servicio de ingesta valida y normaliza el archivo de forma temporal.
4. NVIDIA Riva genera la transcripción.
5. El sistema elimina los archivos temporales de audio y guarda únicamente el texto y sus
   segmentos.
6. Se generan embeddings y se indexa la transcripción para búsqueda y chat RAG.
7. El estudiante consulta sus clases mediante el chat y recibe respuestas con citas.
8. El modelo propone tareas o fechas; el estudiante las revisa y confirma.
9. Las propuestas confirmadas se sincronizan con Google Calendar y, si está habilitado, generan
   recordatorios por Gmail.

### 2. Actores del sistema

- **Estudiante:** actor principal. Carga grabaciones, consulta transcripciones, utiliza el chat,
  revisa propuestas y confirma eventos de calendario.
- **Sistema Maulwurf:** gestiona autenticación, ingesta, transcripción, almacenamiento,
  búsqueda, chat, extracción de actividades y sincronización de tareas.
- **NVIDIA Riva:** servicio externo de reconocimiento automático de voz que convierte el audio en
  texto mediante gRPC seguro.
- **Proveedor de modelos de lenguaje y embeddings:** genera respuestas del chat, extrae
  actividades y transforma los fragmentos de texto en vectores para la búsqueda semántica.
- **Google Calendar y Gmail:** servicios externos opcionales utilizados para crear eventos,
  consultar la agenda y enviar recordatorios, siempre respetando la autorización del usuario.

### 3. Funcionalidades principales

1. **Autenticación y perfil de usuario**
   - Inicio de sesión mediante Google OAuth.
   - Configuración de zona horaria y preferencias de notificaciones.
   - Separación de la información de cada estudiante.

2. **Gestión de materias y clases**
   - Crear y administrar materias.
   - Registrar clases con fecha, idioma y metadatos básicos.
   - Consultar el estado de procesamiento de cada clase.

3. **Carga y transcripción de audio**
   - Carga de formatos comunes como MP3, M4A, WAV, OGG, OPUS, FLAC y WEBM, siempre que hayan
     sido validados por el sistema.
   - Validación de tamaño, duración y formato.
   - Normalización mediante FFmpeg y transcripción con NVIDIA Riva.
   - Eliminación del audio temporal una vez finalizada la transcripción o cuando ocurre un error.

4. **Biblioteca de transcripciones**
   - Lectura de las transcripciones organizadas por materia y clase.
   - Exportación del texto cuando exista una transcripción válida.
   - Visualización de segmentos y timestamps únicamente cuando sean confiables.

5. **Búsqueda híbrida y chat RAG**
   - Búsqueda por palabras clave y por significado.
   - Chat sobre una clase, una materia o todas las materias del estudiante.
   - Respuestas fundamentadas en fragmentos recuperados de las transcripciones.
   - Citas enlazables para comprobar el origen de cada respuesta.
   - Abstención cuando no exista evidencia suficiente, en lugar de inventar información.

6. **Extracción y revisión de actividades**
   - Detección de tareas, exámenes, entregas, lecturas y recomendaciones.
   - Extracción estructurada de fechas, horas, zona horaria y evidencia textual.
   - Bandeja de revisión para editar, confirmar o descartar propuestas.
   - Ninguna propuesta se agenda automáticamente por su nivel de confianza.

7. **Integración con Google Calendar y Gmail**
   - Creación idempotente de eventos después de la confirmación del estudiante.
   - Consulta de eventos próximos desde el chat o el panel principal.
   - Recordatorios y resúmenes diarios o semanales mediante Gmail como funcionalidad opcional.

### 4. Roles de los integrantes

Los nombres pueden sustituirse por los nombres reales del equipo.

| Integrante | Rol | Responsabilidades principales |
| --- | --- | --- |
| **Integrante 1 — José Leonardo Pinilla Zamora** | **Líder técnico y especialista en RAG** | Diseñar la arquitectura general del sistema; implementar el pipeline RAG; definir chunking, embeddings, recuperación híbrida, RRF, prompts y citas; coordinar la integración entre backend, modelos y frontend; revisar decisiones técnicas y validar la calidad de las respuestas. |
| **Integrante 2 — Andres Felipe** | Backend y base de datos | Implementar la API con FastAPI; diseñar modelos y migraciones de PostgreSQL; desarrollar autenticación, usuarios, materias, clases y endpoints; asegurar el aislamiento de datos entre usuarios. |
| **Integrante 3 — Santiago Montealegre** | Procesamiento de audio y transcripción | Integrar NVIDIA Riva mediante `nvidia-riva-client`; implementar validación con FFmpeg/FFprobe, normalización, fragmentación y control de límites; gestionar archivos temporales, limpieza, estados de ingesta y pruebas del contrato de ASR. |
| **Integrante 4 — Daniel Samaca** | Frontend y experiencia de usuario | Desarrollar la interfaz con Next.js; crear el formulario de carga, biblioteca de transcripciones, lector de segmentos, chat, panel de revisión y dashboard; mostrar estados, citas y errores de forma clara y accesible. |
| **Integrante 5 — Jefferson Figueroa** | Integraciones, infraestructura y calidad | Implementar Google Calendar y Gmail; configurar Redis, ARQ, Docker Compose y el entorno de ejecución; preparar CI/CD, pruebas de integración y E2E; documentar despliegue, seguridad, monitoreo y control de costos. |

Aunque cada integrante tendrá un área principal, el equipo trabajará mediante revisiones de
código, pruebas integradas y reuniones de seguimiento. El líder técnico coordinará las
interfaces entre los módulos para evitar que el funcionamiento del RAG dependa de contratos
incompatibles con la API, la transcripción o la interfaz de usuario.

### 5. Tecnologías y frameworks

| Área | Tecnología propuesta | Uso dentro del proyecto |
| --- | --- | --- |
| Frontend | Next.js, TypeScript, Tailwind CSS y shadcn/ui | Aplicación web, dashboard, chat y biblioteca |
| Backend | Python, FastAPI, Pydantic y SQLAlchemy | API REST, validación y lógica del sistema |
| Base de datos | PostgreSQL | Usuarios, materias, transcripciones, tareas y metadatos |
| Búsqueda vectorial | pgvector con índices HNSW | Embeddings y recuperación semántica |
| Búsqueda textual | PostgreSQL Full-Text Search | Recuperación por palabras clave |
| Cola de trabajos | Redis y ARQ | Procesamiento asíncrono de texto, análisis e integraciones |
| Transcripción | NVIDIA Riva, `nvidia-riva-client` y gRPC/TLS | Conversión de audio a texto |
| Procesamiento multimedia | FFmpeg y FFprobe | Validación, conversión y normalización temporal del audio |
| Modelos de lenguaje | Pool de LLMs de proveedores de inferencia gratuita | Chat RAG y extracción estructurada |
| Embeddings | Modelo configurable, sujeto a cambios futuros | Representación vectorial de fragmentos |
| Integraciones | Google OAuth, Google Calendar API y Gmail API | Autenticación, agenda y notificaciones |
| Infraestructura | Servidor ARM, dominio propio, Cloudflare Tunnel (`cloudflared`) y protección contra bots de Cloudflare | Despliegue, acceso seguro y protección del servicio |

La arquitectura se diseñará para que los proveedores puedan sustituirse mediante adaptadores,
pero cualquier cambio de modelo o proveedor deberá validarse con pruebas de calidad, capacidad,
privacidad y compatibilidad. El almacenamiento permanente se limitará a texto, embeddings y
metadatos necesarios; el audio no formará parte del almacenamiento durable del sistema.

## Resultado esperado

Al finalizar el proyecto se espera contar con un prototipo web que permita subir una clase,
obtener su transcripción, buscar información mediante un chat RAG con citas y convertir los
pendientes detectados en propuestas revisables para Google Calendar. El sistema demostrará un
flujo completo desde la ingesta hasta la acción, manteniendo al estudiante dentro del proceso de
decisión y evitando escrituras automáticas no autorizadas en servicios externos.
