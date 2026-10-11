# General proposal for the final project

## Maulwurf: intelligent study assistant built from class recordings

### 1. System overview

Maulwurf will be a web assistant aimed at students that turns class recordings into
organized, searchable knowledge. The problem it aims to solve is that students tend to
accumulate hours of audio but rarely listen to it in full again. As a result, important
information —such as exam dates, assignments, readings, or the teacher's
recommendations— stays hidden inside the recordings.

The system will solve this problem through an automatic flow. First, the student uploads
a recording along with the subject, the class date, the time zone, and the audio language.
The recording is processed temporarily with NVIDIA Riva to obtain a transcript. The audio
is not kept permanently: only temporary in-memory files are used during reception,
conversion, and transcription.

Once the transcript is obtained, the system splits it into chunks and generates embeddings
to store them in a vector database. This enables hybrid searches, combining semantic and
textual search. On top of this information the system builds a RAG chat (*Retrieval-
Augmented Generation*), able to answer questions using the student's transcripts and
showing citations that point to the segment used as evidence.

In addition, a language model analyzes the transcripts to detect tasks, exams,
deliverables, readings, and recommendations. These detections are presented as reviewable
proposals. The student must explicitly confirm a proposal before an event is created in
Google Calendar. Optionally, the system can send reminders and digests by email through
Gmail.

The project prioritizes privacy and traceability. Audio playback and download are not
offered from the application, extracted dates can be reviewed before confirming them, and
the system does not invent timestamps when the transcription provider does not deliver
reliable temporal information.

### Main flow

1. The student signs in and registers a subject.
2. They select the class date, time zone, and language, and upload the audio.
3. The ingestion service validates and normalizes the file temporarily.
4. NVIDIA Riva generates the transcript.
5. The system deletes the temporary audio files and stores only the text and its
   segments.
6. Embeddings are generated and the transcript is indexed for search and RAG chat.
7. The student queries their classes through the chat and receives answers with citations.
8. The model proposes tasks or dates; the student reviews and confirms them.
9. Confirmed proposals are synchronized with Google Calendar and, if enabled, generate
   Gmail reminders.

### 2. System actors

- **Student:** primary actor. Uploads recordings, queries transcripts, uses the chat,
  reviews proposals, and confirms calendar events.
- **Maulwurf system:** manages authentication, ingestion, transcription, storage,
  search, chat, activity extraction, and task synchronization.
- **NVIDIA Riva:** external automatic speech recognition service that converts audio into
  text over secure gRPC.
- **Language model and embeddings provider:** generates chat answers, extracts
  activities, and turns text chunks into vectors for semantic search.
- **Google Calendar and Gmail:** optional external services used to create events, query
  the agenda, and send reminders, always respecting the user's authorization.

### 3. Main features

1. **Authentication and user profile**
   - Sign-in through Google OAuth.
   - Time zone and notification preference settings.
   - Separation of each student's information.

2. **Subject and class management**
   - Create and manage subjects.
   - Register classes with date, language, and basic metadata.
   - Check the processing status of each class.

3. **Audio upload and transcription**
   - Upload of common formats such as MP3, M4A, WAV, OGG, OPUS, FLAC, and WEBM, provided
     they have been validated by the system.
   - Validation of size, duration, and format.
   - Normalization with FFmpeg and transcription with NVIDIA Riva.
   - Deletion of the temporary audio once the transcription finishes or when an error
     occurs.

4. **Transcript library**
   - Reading transcripts organized by subject and class.
   - Exporting the text when a valid transcript exists.
   - Displaying segments and timestamps only when they are reliable.

5. **Hybrid search and RAG chat**
   - Search by keywords and by meaning.
   - Chat about a class, a subject, or all of the student's subjects.
   - Answers grounded in fragments retrieved from the transcripts.
   - Linkable citations to check the origin of each answer.
   - Abstention when there is not enough evidence, instead of inventing information.

6. **Activity extraction and review**
   - Detection of tasks, exams, deliverables, readings, and recommendations.
   - Structured extraction of dates, times, time zone, and textual evidence.
   - A review tray to edit, confirm, or discard proposals.
   - No proposal is scheduled automatically based on its confidence level.

7. **Google Calendar and Gmail integration**
   - Idempotent event creation after the student confirms.
   - Querying upcoming events from the chat or the main panel.
   - Daily or weekly digests and reminders through Gmail as an optional feature.

### 4. Member roles

The names can be replaced with the team's real names.

| Member | Role | Main responsibilities |
| --- | --- | --- |
| **Member 1 — José Leonardo Pinilla Zamora** | **Technical lead and RAG specialist** | Design the overall system architecture; implement the RAG pipeline; define chunking, embeddings, hybrid retrieval, RRF, prompts, and citations; coordinate integration between backend, models, and frontend; review technical decisions and validate answer quality. |
| **Member 2 — Andres Felipe** | Backend and database | Implement the API with FastAPI; design PostgreSQL models and migrations; develop authentication, users, subjects, classes, and endpoints; ensure data isolation between users. |
| **Member 3 — Santiago Montealegre** | Audio processing and transcription | Integrate NVIDIA Riva through `nvidia-riva-client`; implement validation with FFmpeg/FFprobe, normalization, chunking, and limit control; manage temporary files, cleanup, ingestion states, and ASR contract tests. |
| **Member 4 — Daniel Samaca** | Frontend and user experience | Develop the interface with Next.js; build the upload form, transcript library, segment reader, chat, review tray, and dashboard; present statuses, citations, and errors clearly and accessibly. |
| **Member 5 — Jefferson Figueroa** | Integrations, infrastructure, and quality | Implement Google Calendar and Gmail; configure Redis, ARQ, Docker Compose, and the runtime environment; prepare CI/CD, integration and E2E tests; document deployment, security, monitoring, and cost control. |

Although each member will have a main area, the team will work through code reviews,
integrated tests, and follow-up meetings. The technical lead coordinates the interfaces
between modules so that RAG behavior does not depend on contracts incompatible with the
API, the transcription, or the user interface.

### 5. Technologies and frameworks

| Area | Proposed technology | Use within the project |
| --- | --- | --- |
| Frontend | Next.js, TypeScript, Tailwind CSS, and shadcn/ui | Web application, dashboard, chat, and library |
| Backend | Python, FastAPI, Pydantic, and SQLAlchemy | REST API, validation, and system logic |
| Database | PostgreSQL | Users, subjects, transcripts, tasks, and metadata |
| Vector search | pgvector with HNSW indexes | Embeddings and semantic retrieval |
| Text search | PostgreSQL Full-Text Search | Keyword retrieval |
| Job queue | Redis and ARQ | Asynchronous processing of text, analysis, and integrations |
| Transcription | NVIDIA Riva, `nvidia-riva-client`, and gRPC/TLS | Audio-to-text conversion |
| Media processing | FFmpeg and FFprobe | Audio validation, conversion, and temporal normalization |
| Language models | Pool of LLMs from free inference providers | RAG chat and structured extraction |
| Embeddings | Configurable model, subject to future changes | Vector representation of chunks |
| Integrations | Google OAuth, Google Calendar API, and Gmail API | Authentication, scheduling, and notifications |
| Infrastructure | ARM server, own domain, Cloudflare Tunnel (`cloudflared`), and Cloudflare bot protection | Deployment, secure access, and service protection |

The architecture will be designed so that providers can be swapped through adapters, but
any model or provider change must be validated with quality, capacity, privacy, and
compatibility tests. Permanent storage will be limited to text, embeddings, and the
required metadata; audio will not be part of the system's durable storage.

## Expected outcome

At the end of the project the expectation is a web prototype that allows uploading a
class, obtaining its transcript, searching information through a RAG chat with citations,
and turning detected to-dos into reviewable proposals for Google Calendar. The system will
demonstrate a complete flow from ingestion to action, keeping the student inside the
decision process and avoiding unauthorized automatic writes to external services.
