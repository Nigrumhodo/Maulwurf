"""I-S4-AN-05 (A4.5): D8 backup and restore, encrypted text/metadata only.

The suite runs the real ``scripts/ops/backup_restore.sh`` against PostgreSQL:

- a class, its transcript/chunks/embeddings, a conversation and an account are seeded;
- the encrypted backup is taken **before** deleting them, so a clean restore would
  resurrect the text if tombstones were not reapplied;
- the account is deleted through A4.4, a class and a conversation are tombstoned, and the
  identifier-only deletion log is exported;
- the backup is restored into a different, empty database and the deletion log is replayed;
- nothing deleted reappears, retained rows survive, and the account keeps only its opaque
  tombstone plus the residual operation.

It also pins the D8 retention (30 days), the pruning of older files, the refusal to restore
an expired backup, and the fail-closed refusal to dump an unexpected binary column (the
no-audio contract). No real audio is generated or stored: the only payload is text in the
database, and both the dump and the deletion log are encrypted before they are written.
"""

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import settings
from app.models import (
    Audio,
    CalendarSettings,
    Chunk,
    ChunkSegment,
    Conversation,
    Embedding,
    Message,
    MessageSource,
    Subject,
)
from app.services.audios import tombstone_audio
from app.services.conversations import delete_conversation
from app.services.deletion import (
    RESIDUAL_RETENTION_DAYS,
    create_account_deletion,
    execute_account_deletion,
)
from tests.integration.actors import (
    make_actor,
    make_calendar_event,
    make_class_with_timed_segments,
    make_google_credential,
    make_index_generation,
    make_notification,
    make_outbox_event,
    make_processing_run,
    make_task,
    make_task_revision,
    make_variant_confirmation_token,
)
from tests.integration.conftest import API_ROOT, alembic_config

pytestmark = pytest.mark.integration

REPO_ROOT = API_ROOT.parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ops" / "backup_restore.sh"
PASSPHRASE = "test-only-backup-passphrase-135"
RETENTION_DAYS = 30

ALICE_MARKER = "alice-deleted-transcript-marker-135"
BOB_DELETED_MARKER = "bob-tombstoned-transcript-marker-135"
BOB_KEPT_MARKER = "bob-retained-transcript-marker-135"

ALICE_TENANT_TABLES = (
    "subjects",
    "audios",
    "ingestion_attempts",
    "transcripts",
    "segments",
    "index_generations",
    "chunks",
    "chunk_segments",
    "embeddings",
    "conversations",
    "messages",
    "message_sources",
    "tasks",
    "task_revisions",
    "task_sources",
    "notifications",
    "processing_runs",
    "outbox_events",
    "calendar_events",
    "settings",
    "google_credentials",
    "sessions",
)


async def _admin(statement: str) -> None:
    engine = create_async_engine(
        make_url(str(settings.database_url)).set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    async with engine.connect() as connection:
        await connection.execute(text(statement))
    await engine.dispose()


async def _create_database(name: str) -> str:
    await _admin(f'CREATE DATABASE "{name}"')
    return make_url(str(settings.database_url)).set(
        database=name
    ).render_as_string(hide_password=False)


async def _drop_database(name: str) -> None:
    await _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["MAULWURF_BACKUP_PASSPHRASE"] = PASSPHRASE
    return env


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed repository script, no shell, no payload
        [str(SCRIPT), *args],
        cwd=REPO_ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )


async def _seed(source_url: str) -> dict[str, Any]:
    engine = create_async_engine(source_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    ids: dict[str, Any] = {}
    async with maker() as db:
        alice = await make_actor(db, email="alice-a45@example.test")
        alice_audio, _attempt, alice_transcript, alice_segments = (
            await make_class_with_timed_segments(db, alice)
        )
        alice_transcript.text = ALICE_MARKER
        alice_transcript.char_count = len(ALICE_MARKER)
        generation = await make_index_generation(db, alice)
        alice_chunk = Chunk(
            user_id=alice.user_id,
            audio_id=alice_audio.id,
            transcript_id=alice_transcript.id,
            transcript_version=alice_transcript.version,
            index_version=generation.version,
            ordinal=1,
            content=ALICE_MARKER,
            char_start=0,
            char_end=len(ALICE_MARKER),
            token_count=3,
        )
        db.add(alice_chunk)
        await db.flush()
        db.add_all(
            [
                Embedding(
                    user_id=alice.user_id,
                    chunk_id=alice_chunk.id,
                    index_version=alice_chunk.index_version,
                    model="test-model",
                    dimension=1536,
                    vector=[0.0] * 1536,
                ),
                ChunkSegment(
                    user_id=alice.user_id,
                    chunk_id=alice_chunk.id,
                    segment_id=alice_segments[0].id,
                    overlap_span={},
                ),
            ]
        )
        alice_conversation = Conversation(
            user_id=alice.user_id, mode="all", title="alice conversation"
        )
        db.add(alice_conversation)
        await db.flush()
        alice_message = Message(
            user_id=alice.user_id,
            conversation_id=alice_conversation.id,
            role="user",
            content=ALICE_MARKER,
        )
        db.add(alice_message)
        await db.flush()
        db.add(
            MessageSource(
                user_id=alice.user_id,
                message_id=alice_message.id,
                kind="transcript",
                audio_id=alice_audio.id,
                transcript_id=alice_transcript.id,
                transcript_version=alice_transcript.version,
                chunk_id=alice_chunk.id,
                segment_id=alice_segments[0].id,
            )
        )
        alice_task = await make_task(db, alice)
        await make_notification(db, alice)
        db.add(CalendarSettings(user_id=alice.user_id))
        await make_google_credential(db, alice)
        await make_calendar_event(db, alice)
        await make_outbox_event(db, alice)
        await make_processing_run(db, alice)
        await make_variant_confirmation_token(db, alice)
        await make_task_revision(db, alice)

        bob = await make_actor(db, email="bob-a45@example.test")
        bob_kept_audio, _bob_attempt, bob_kept_transcript, _ = (
            await make_class_with_timed_segments(db, bob)
        )
        bob_kept_transcript.text = BOB_KEPT_MARKER
        bob_kept_transcript.char_count = len(BOB_KEPT_MARKER)
        bob_kept_generation = await make_index_generation(db, bob)
        bob_kept_chunk = Chunk(
            user_id=bob.user_id,
            audio_id=bob_kept_audio.id,
            transcript_id=bob_kept_transcript.id,
            transcript_version=bob_kept_transcript.version,
            index_version=bob_kept_generation.version,
            ordinal=1,
            content=BOB_KEPT_MARKER,
            char_start=0,
            char_end=len(BOB_KEPT_MARKER),
            token_count=3,
        )
        db.add(bob_kept_chunk)

        bob_deleted_audio, _bob_deleted_attempt, bob_deleted_transcript, _ = (
            await make_class_with_timed_segments(db, bob)
        )
        bob_deleted_transcript.text = BOB_DELETED_MARKER
        bob_deleted_transcript.char_count = len(BOB_DELETED_MARKER)
        bob_deleted_generation = await make_index_generation(db, bob)
        bob_deleted_chunk = Chunk(
            user_id=bob.user_id,
            audio_id=bob_deleted_audio.id,
            transcript_id=bob_deleted_transcript.id,
            transcript_version=bob_deleted_transcript.version,
            index_version=bob_deleted_generation.version,
            ordinal=1,
            content=BOB_DELETED_MARKER,
            char_start=0,
            char_end=len(BOB_DELETED_MARKER),
            token_count=3,
        )
        db.add(bob_deleted_chunk)

        bob_deleted_conversation = Conversation(
            user_id=bob.user_id, mode="all", title="bob deleted conversation"
        )
        bob_kept_conversation = Conversation(
            user_id=bob.user_id, mode="all", title="bob kept conversation"
        )
        bob_deleted_subject = Subject(
            user_id=bob.user_id, name=f"bob deleted subject {uuid.uuid4().hex[:6]}"
        )
        db.add_all(
            [bob_deleted_conversation, bob_kept_conversation, bob_deleted_subject]
        )
        await db.flush()
        bob_deleted_message = Message(
            user_id=bob.user_id,
            conversation_id=bob_deleted_conversation.id,
            role="user",
            content=BOB_DELETED_MARKER,
        )
        bob_kept_message = Message(
            user_id=bob.user_id,
            conversation_id=bob_kept_conversation.id,
            role="user",
            content=BOB_KEPT_MARKER,
        )
        db.add_all([bob_deleted_message, bob_kept_message])
        await make_task(db, bob)

        await db.commit()
        ids = {
            "alice_user": alice.user_id,
            "alice_session": alice.session.id,
            "alice_audio": alice_audio.id,
            "alice_task": alice_task.id,
            "bob_user": bob.user_id,
            "bob_kept_audio": bob_kept_audio.id,
            "bob_kept_transcript": bob_kept_transcript.id,
            "bob_deleted_audio": bob_deleted_audio.id,
            "bob_deleted_transcript": bob_deleted_transcript.id,
            "bob_deleted_conversation": bob_deleted_conversation.id,
            "bob_deleted_subject": bob_deleted_subject.id,
            "bob_kept_conversation": bob_kept_conversation.id,
        }
    await engine.dispose()
    return ids


async def _apply_deletions(source_url: str, ids: Mapping[str, Any]) -> str:
    engine = create_async_engine(source_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        audio = await db.get(Audio, ids["bob_deleted_audio"])
        assert audio is not None
        await tombstone_audio(db, user_id=ids["bob_user"], audio=audio)
        await delete_conversation(db, ids["bob_user"], ids["bob_deleted_conversation"])
        deletion = await create_account_deletion(
            db,
            user_id=ids["alice_user"],
            session_id=ids["alice_session"],
            delete_events=False,
        )
        subject = await db.get(Subject, ids["bob_deleted_subject"])
        assert subject is not None
        subject.deleted_at = datetime.now(UTC)
        await db.commit()
        status = await execute_account_deletion(db, deletion.id)
        assert status == "completed"
        deletion_id = str(deletion.id)
    await engine.dispose()
    return deletion_id


async def _scalar(connection: Any, statement: str, **params: Any) -> Any:
    return (await connection.execute(text(statement), params)).scalar_one()


async def _count(connection: Any, table: str, user_id: uuid.UUID) -> int:
    # `table` comes from the fixed ALICE_TENANT_TABLES tuple, never from a request or log.
    return int(
        await _scalar(
            connection,
            f"SELECT count(*) FROM {table} WHERE user_id = :user_id",  # noqa: S608
            user_id=user_id,
        )
    )


async def test_i_s4_an_05_restore_reapplies_tombstones_on_a_clean_database(
    tmp_path: Path,
) -> None:
    for command_name in ("pg_dump", "pg_restore", "gpg"):
        if shutil.which(command_name) is None:
            pytest.fail(f"I-S4-AN-05 needs `{command_name}` on PATH")

    source_name = f"maulwurf_a45_src_{uuid.uuid4().hex[:10]}"
    target_name = f"maulwurf_a45_dst_{uuid.uuid4().hex[:10]}"
    source_url = await _create_database(source_name)
    target_url = await _create_database(target_name)
    try:
        await asyncio.to_thread(command.upgrade, alembic_config(source_url), "head")
        ids = await _seed(source_url)

        backups = tmp_path / "backups"
        backups.mkdir(mode=0o700)
        backup = _run(
            "backup",
            "--database-url",
            _sync_url(source_url),
            "--out",
            str(backups),
            "--label",
            "pre-delete",
        )
        assert backup.returncode == 0, backup.stderr + backup.stdout
        dumps = list(backups.glob("*.dump.gpg"))
        logs = list(backups.glob("*.deletions.json.gpg"))
        manifests = list(backups.glob("*.manifest.json"))
        assert len(dumps) == len(logs) == len(manifests) == 1
        dump, bundled_log, manifest = dumps[0], logs[0], manifests[0]

        # Encrypted at rest: neither the OpenPGP container nor the text payload is visible.
        dump_bytes = dump.read_bytes()
        assert not dump_bytes.startswith(b"PGDMP")
        assert ALICE_MARKER.encode() not in dump_bytes
        assert BOB_KEPT_MARKER.encode() not in dump_bytes
        assert ALICE_MARKER.encode() not in bundled_log.read_bytes()
        manifest_data = json.loads(manifest.read_text())
        assert manifest_data["retention_days"] == RETENTION_DAYS
        assert manifest_data["dump_sha256"]

        deletion_id = await _apply_deletions(source_url, ids)
        exported_log = backups / "post-delete.deletions.json.gpg"
        exported = _run(
            "export-deletions",
            "--database-url",
            _sync_url(source_url),
            "--out",
            str(exported_log),
        )
        assert exported.returncode == 0, exported.stderr + exported.stdout
        assert exported_log.exists()
        assert ALICE_MARKER.encode() not in exported_log.read_bytes()

        restored = _run(
            "restore",
            "--database-url",
            _sync_url(target_url),
            "--backup",
            str(dump),
            "--deletion-log",
            str(exported_log),
            "--confirm-clean",
        )
        assert restored.returncode == 0, restored.stderr + restored.stdout

        engine = create_async_engine(target_url)
        async with engine.connect() as connection:
            # Alice's account keeps only the opaque tombstone and the residual operation;
            # every tenant row and every text payload is gone.
            user_row = (
                await connection.execute(
                    text(
                        "SELECT status, email, google_sub, display_name "
                        "FROM users WHERE id = :id"
                    ),
                    {"id": ids["alice_user"]},
                )
            ).one()
            assert user_row.status == "deleted"
            assert user_row.email == f"deleted-{ids['alice_user']}@invalid"
            assert user_row.google_sub == f"deleted:{ids['alice_user']}"
            assert user_row.display_name is None
            for table in ALICE_TENANT_TABLES:
                assert await _count(connection, table, ids["alice_user"]) == 0, table
            operation = (
                await connection.execute(
                    text(
                        "SELECT id, status, expires_at FROM account_deletions "
                        "WHERE former_user_id = :id"
                    ),
                    {"id": ids["alice_user"]},
                )
            ).one()
            assert str(operation.id) == deletion_id
            assert operation.status == "completed"
            assert operation.expires_at > datetime.now(UTC)
            assert (
                await _scalar(
                    connection,
                    "SELECT count(*) FROM transcripts WHERE text = :marker",
                    marker=ALICE_MARKER,
                )
            ) == 0
            assert (
                await _scalar(
                    connection,
                    "SELECT count(*) FROM chunks WHERE content = :marker",
                    marker=ALICE_MARKER,
                )
            ) == 0
            assert (
                await _scalar(
                    connection,
                    "SELECT count(*) FROM messages WHERE content = :marker",
                    marker=ALICE_MARKER,
                )
            ) == 0

            # Bob's tombstoned class keeps the tombstone but loses every derivative.
            tombstone = (
                await connection.execute(
                    text(
                        "SELECT deleted_at, active_transcript_version FROM audios "
                        "WHERE id = :id"
                    ),
                    {"id": ids["bob_deleted_audio"]},
                )
            ).one()
            assert tombstone.deleted_at is not None
            assert tombstone.active_transcript_version is None
            assert (
                await _scalar(
                    connection,
                    "SELECT count(*) FROM transcripts WHERE audio_id = :id",
                    id=ids["bob_deleted_audio"],
                )
            ) == 0
            assert (
                await _scalar(
                    connection,
                    "SELECT count(*) FROM chunks WHERE audio_id = :id",
                    id=ids["bob_deleted_audio"],
                )
            ) == 0
            assert (
                await _scalar(
                    connection,
                    "SELECT count(*) FROM messages WHERE content = :marker",
                    marker=BOB_DELETED_MARKER,
                )
            ) == 0

            # Bob's deleted conversation keeps its tombstone, with no title or messages.
            conversation = (
                await connection.execute(
                    text(
                        "SELECT deleted_at, title FROM conversations WHERE id = :id"
                    ),
                    {"id": ids["bob_deleted_conversation"]},
                )
            ).one()
            assert conversation.deleted_at is not None
            assert conversation.title is None
            assert (
                await _scalar(
                    connection,
                    "SELECT count(*) FROM messages WHERE conversation_id = :id",
                    id=ids["bob_deleted_conversation"],
                )
            ) == 0
            assert (
                await _scalar(
                    connection,
                    "SELECT deleted_at FROM subjects WHERE id = :id",
                    id=ids["bob_deleted_subject"],
                )
            ) is not None

            # Retained data survives, including Bob's active class and conversation.
            assert (
                await _scalar(
                    connection,
                    "SELECT count(*) FROM transcripts WHERE id = :id AND text = :marker",
                    id=ids["bob_kept_transcript"],
                    marker=BOB_KEPT_MARKER,
                )
            ) == 1
            assert (
                await _scalar(
                    connection,
                    "SELECT count(*) FROM chunks WHERE content = :marker",
                    marker=BOB_KEPT_MARKER,
                )
            ) == 1
            assert (
                await _scalar(
                    connection,
                    "SELECT count(*) FROM messages WHERE conversation_id = :id",
                    id=ids["bob_kept_conversation"],
                )
            ) == 1
            assert (
                await _scalar(
                    connection,
                    "SELECT status FROM users WHERE id = :id",
                    id=ids["bob_user"],
                )
            ) == "active"

            # The no-audio contract is a schema/catalog property, not a convention: the
            # only binary columns are the AES-GCM Google token blobs.
            binary_columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT table_name || '.' || column_name "
                            "FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND data_type = 'bytea'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert binary_columns <= {
                "google_credentials.access_token_enc",
                "google_credentials.refresh_token_enc",
            }
        await engine.dispose()

        # D8 retention: an expired backup is refused before touching a database. The backup
        # window must stay equal to the residual deletion window, or an old backup could
        # outlive the deletion record it needs to replay.
        assert RETENTION_DAYS == RESIDUAL_RETENTION_DAYS == 30
        expired_manifest = tmp_path / "expired.manifest.json"
        expired = dict(manifest_data)
        expired["created_at"] = (
            datetime.now(UTC) - timedelta(days=RETENTION_DAYS + 1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        expired_manifest.write_text(json.dumps(expired))
        refused = _run(
            "verify",
            "--backup",
            str(dump),
            "--manifest",
            str(expired_manifest),
        )
        assert refused.returncode != 0
        assert "expired" in refused.stderr.lower()

        # Older files are pruned; the fresh backup stays.
        stale = backups / "maulwurf-19700101T000000Z-old.dump.gpg"
        stale.write_bytes(b"stale")
        old = time.time() - (RETENTION_DAYS + 1) * 86_400
        os.utime(stale, (old, old))
        pruned = _run("prune", "--out", str(backups))
        assert pruned.returncode == 0, pruned.stderr + pruned.stdout
        assert not stale.exists()
        assert dump.exists()

        # A future audio/blob column stops the backup instead of leaking into an archive.
        engine = create_async_engine(source_url)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE forbidden_audio_payload "
                    "(id integer PRIMARY KEY, payload bytea NOT NULL)"
                )
            )
        await engine.dispose()
        forbidden = _run(
            "backup",
            "--database-url",
            _sync_url(source_url),
            "--out",
            str(tmp_path / "refused"),
        )
        assert forbidden.returncode != 0
        assert "unexpected binary" in forbidden.stderr
    finally:
        await _drop_database(source_name)
        await _drop_database(target_name)
