# 2. CRC Cards

A CRC card (Class, Responsibilities, Collaborators) lists what a class knows and does, and which
classes it needs to do it. The cards cover the **main classes** of the design: the persistent
domain entities (left part of the [class diagram](03-class-diagram.md)) and the services that
own the business processes. Each card states its module and how much of it exists in the
repository today:

- **implemented**: the class exists on `develop` with tests;
- **partial**: the class exists, but part of its behavior is still a planned ticket;
- **planned**: designed in the specification, not implemented yet (ticket in brackets).

Class names follow the code and the specification. Some persisted fields keep the Spanish
identifiers frozen in the extraction contract (`tipo`, `titulo`, `detalle`, `cita_textual`:
type, title, detail and literal quote).

## Domain entities

### CRC-01 · User

*Entity · `apps/api/app/models/core.py` · implemented*

| Responsibilities | Collaborators |
|---|---|
| Identify the student by the Google `sub`, never by email | Google Identity (via the auth service) |
| Keep profile data: email, display name, IANA timezone | — |
| Own every tenant resource (subjects, classes, conversations, tasks) | Subject, Audio, Conversation, Task |
| Track the account lifecycle `active → deleting → deleted` | Session, OutboxEvent (`account_deleted`) |

### CRC-02 · Session

*Entity · `apps/api/app/models/core.py`, `services/sessions.py` · implemented*

| Responsibilities | Collaborators |
|---|---|
| Represent one opaque sign-in; store only the hash of its identifier | User |
| Hold the hash of the CSRF token shared by the session's tabs | — |
| Expire, rotate and be revoked (logout, account deletion) | User |
| Be the single source of the current user for every request | API dependencies (`core/deps.py`) |

### CRC-03 · Subject

*Entity · `apps/api/app/models/core.py`, `routers/subjects.py` · implemented*

| Responsibilities | Collaborators |
|---|---|
| Group the classes of one course (name, color, teacher) | Audio |
| Keep names unique among the student's active subjects | User |
| Refuse deletion while it has active classes, unless explicitly forced | Audio |

### CRC-04 · Audio (class record)

*Entity · `apps/api/app/models/ingestion.py` · partial (state and deletion: A2.6, A2.7)*

| Responsibilities | Collaborators |
|---|---|
| Describe one class without storing its audio: subject, class date, timezone, language, title | Subject |
| Hold the content identity (SHA-256 + context) used for deduplication | UploadService |
| Point to the active transcript, analysis and index versions | Transcript, Chunk |
| Derive the aggregate status `processing / ready / partial / failed` from its stages | IngestionAttempt, ProcessingRun |
| Act as tombstone when the class is deleted, so late jobs cannot write | OutboxEvent |

### CRC-05 · IngestionAttempt

*Entity · `apps/api/app/models/ingestion.py`, `apps/ingest/maulwurf_ingest/lease.py` ·
implemented*

| Responsibilities | Collaborators |
|---|---|
| Record versioned consent before any byte is accepted | Audio, UploadService |
| Reserve ingestion capacity and expire the upload window | UploadService |
| Follow the ASR state machine `awaiting_upload → receiving → transcribing → transcript_committed_cleanup_pending → succeeded` (or `rejected`, `requires_reupload`, `cancelled`) | IngestionSupervisor |
| Carry the lease and fencing token so an expired owner cannot publish | IngestionSupervisor |
| Record cleanup status and evidence separately from the ASR result | IngestionSupervisor |
| Report real progress (bytes received, fragments done/total) | Web client (progress SSE) |

### CRC-06 · Transcript and Segment

*Entity · `apps/api/app/models/transcript.py` · partial (schema implemented; commit: S2.5,
A2.10)*

| Responsibilities | Collaborators |
|---|---|
| Store the full text of one version, with language, model and configuration | Audio |
| Declare the real timestamp precision `word / segment / none` | NvidiaRivaTranscriptionService |
| Keep ordered segments with Unicode character offsets and, only if valid, times | Segment |
| Be the evidence base for citations and extracted tasks | Chunk, Task, Message |
| Export TXT always and SRT only with valid times | Web client |

### CRC-07 · Chunk (with Embedding and IndexGeneration)

*Entity · `apps/api/app/models/chunk.py` · partial (schema implemented; indexing: L2.1, L2.2)*

| Responsibilities | Collaborators |
|---|---|
| Split a transcript into ~800-token pieces with ~100 overlap, keeping character offsets | Transcript |
| Link each chunk to the segments it covers, which remain the unit of evidence | Segment |
| Hold one embedding per index generation (model, dimension, vector) and a full-text vector | Embedding, IndexGeneration |
| Publish a new generation atomically; never mix models or dimensions | IndexGeneration |

### CRC-08 · Conversation and Message

*Entity · `apps/api/app/models/chat.py` · partial (schema implemented; endpoints: A2.12)*

| Responsibilities | Collaborators |
|---|---|
| Scope a chat to all classes, one subject or one class of the same student | Subject, Audio |
| Keep the ordered history with roles and statuses (`streaming`, `completed`, `failed`, `cancelled`) | Message |
| Make sending idempotent through `client_message_id` | RAGService |
| Record every source delivered to the model, cited or not, so deletions can purge answers | MessageSource, Chunk, Segment |

### CRC-09 · Task

*Entity · planned (S3: A3.x)*

| Responsibilities | Collaborators |
|---|---|
| Represent one exam, assignment, deliverable, reading, reminder, recommendation or project | Audio, Subject |
| Keep the due date (`due_date`) or instant (`due_at`), never both, with its timezone | — |
| Move through `needs_review / pending → confirmed → completed`, or `dismissed` | TaskRevision |
| Hold the textual evidence (segment spans and literal quote) that justifies it | TaskEvidence, Segment |
| Track the Calendar sync status and the optimistic-locking version | GoogleCalendarService, OutboxEvent |

### CRC-10 · TaskRevision

*Entity · planned (S3: A3.x)*

| Responsibilities | Collaborators |
|---|---|
| Hold proposed field changes for a confirmed task without touching its active fields | Task |
| Record the base version it was proposed against | Task |
| Become active only on confirmation with the current version; otherwise `409 version_conflict` | Task, OutboxEvent |
| Carry re-analysis results as revisions, never as overwrites | AnalysisJob |

### CRC-11 · OutboxEvent

*Entity · `apps/api/app/models/ingestion.py`, `workers/dispatcher.py` · partial (consumer
acknowledgement and retries: J2.1)*

| Responsibilities | Collaborators |
|---|---|
| Record durable work in the same transaction as the change that causes it | Transcript, Task, User |
| Carry only IDs and non-sensitive configuration, never audio or tokens | — |
| Keep `index_requested` and `analyze_requested` blocked until cleanup is verified | IngestionAttempt |
| Be dispatched once per deduplication key, with bounded retries | OutboxDispatcher, Redis |

## Services

### CRC-12 · UploadService

*Service · `apps/api/app/services/uploads.py`, `services/audios.py` · partial (reception sink to
ingestion: A2.13, S2.11)*

| Responsibilities | Collaborators |
|---|---|
| Validate metadata, language and consent, then reserve an attempt (`POST /audios`) | IngestionAttempt, Subject |
| Stream the binary `PUT` with incremental SHA-256 and cut it at the size limit | ReceptionSink |
| Decide exact duplicate, variant (with confirmation token) or new content | Audio, VariantConfirmationToken |
| Forward new content to the ingestion service over the internal network (ADR-0008) | IngestionSupervisor |
| Map every failure to a cataloged error code | — |

### CRC-13 · IngestionSupervisor

*Service · `apps/ingest/maulwurf_ingest/supervisor.py` · partial (not yet reachable from the
API: S2.11)*

| Responsibilities | Collaborators |
|---|---|
| Take the attempt's lease and keep it alive with a heartbeat | IngestionAttempt |
| Receive the stream into a private, bounded `tmpfs` directory | Receiver |
| Validate with ffprobe and normalize with ffmpeg, with no shell, network or unbounded resources | ffmpeg/ffprobe |
| Run ASR fragment by fragment in a killable subprocess with backpressure | NvidiaRivaTranscriptionService |
| Commit transcript and segments, then delete and **verify** the deletion in `finally` | Transcript, Cleanup |
| Close admission when cleanup fails and raise an alert | Admission |

### CRC-14 · NvidiaRivaTranscriptionService

*Service (adapter of `TranscriptionService`) · `apps/ingest/maulwurf_ingest/asr/riva_client.py`
· partial (not wired into the pipeline: S2.10)*

| Responsibilities | Collaborators |
|---|---|
| Send one normalized WAV fragment to Riva over gRPC TLS with the selected language | NVIDIA Riva |
| Never request translation or `multi`; reject codes outside the allowlist | — |
| Retry transient errors (at most 3, backoff with jitter) within the deadline; never retry auth, language or format errors | — |
| Support cancellation of the call in flight | IngestionSupervisor |
| Return words and offsets only when the provider really delivers them | Transcript |

### CRC-15 · SearchService

*Service · planned (S2: L2.3, L2.4)*

| Responsibilities | Collaborators |
|---|---|
| Run a full-text branch (per-language `tsvector`) and a vector branch (pgvector, cosine) | Chunk, Embedding |
| Filter both branches by tenant, active version and tombstones **before** the top-k | IndexGeneration, Audio |
| Fuse results with Reciprocal Rank Fusion, deterministically | — |
| Serve `GET /search` and the chat retrieval | RAGService |

### CRC-16 · RAGService

*Service · planned (S2: L2.5, L2.6)*

| Responsibilities | Collaborators |
|---|---|
| Build the context: question embedding, hybrid candidates, about eight chunks within the token budget | SearchService |
| Stream the answer over SSE (`delta`, `citation`, `done`, `error`) | Conversation, Message |
| Emit citations only to segment IDs validated in the database | Segment, MessageSource |
| Abstain when the evidence is insufficient | LLM adapter |
| From F2, call read-only tools (agenda, pending tasks) and propose tasks for review | GoogleCalendarService, Task |

### CRC-17 · AnalysisJob (`analyze(audio_id)`)

*Service (ARQ job) · stub in `apps/api/app/workers/arq_app.py` · planned (S3: L3.x)*

| Responsibilities | Collaborators |
|---|---|
| Send the transcript in windows, with subject, teacher, class date and timezone | Transcript, LLM adapter |
| Validate the structured output with Pydantic and deterministic rules (dates, enums, spans) | `AnalysisResult` schema |
| Check every quote against the segment text; resolve IDs and times from the database | TaskEvidence, Segment |
| Create tasks or revisions idempotently per class, stage, transcript and prompt version | Task, TaskRevision, ProcessingRun |

### CRC-18 · GoogleCalendarService

*Service · planned (S3: J3.x)*

| Responsibilities | Collaborators |
|---|---|
| Create or update exactly one event per confirmed task version, with a deterministic event ID | Task, Google Calendar API |
| Keep a local cache of upcoming events for the dashboard and chat tools | CalendarEvent |
| Detect conflicts between timed events and same-day deadlines | Task |
| Mark the integration `disconnected` on `invalid_grant` and block syncs until reconnection | GoogleCredential |

### CRC-19 · NotificationScheduler

*Service (ARQ cron) · planned (S4: J4.x)*

| Responsibilities | Collaborators |
|---|---|
| Compute reminder times per confirmed task and due version, in the student's local time | Task, User |
| Group and deduplicate reminders with a unique key; build daily and weekly digests | Notification |
| Send through the student's Gmail, opt-in only | Gmail API, GoogleCredential |
| Mark an uncertain send as `delivery_unknown` and never resend it automatically | Notification |
