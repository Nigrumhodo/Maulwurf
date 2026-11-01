# Backup and restore runbook (A4.5)

> **Ticket:** A4.5 (`MAULW-135`) · **Depends on:** A4.4, D8 approved · **Test:**
> I-S4-AN-05. **Review:** an active API-area claim or the lead.
>
> This is the executable D8 procedure for backups and a clean restore. It never stores,
> copies, replays or downloads audio: the database has no audio payload by contract, and the
> backup script refuses to run if an unexpected binary column appears.
>
> References: [S4 detailed specification](../plan/S4.md), [specification](../ESPECIFICACION.md),
> [cleanup runbook](cleanup.md), [development runbook](../RUNBOOK-dev.md).

## 1. D8 policy this runbook implements

| Item | Decision |
|---|---|
| Backup content | PostgreSQL text and metadata only, including transcripts, chunks, embeddings, tasks, conversations, settings and encrypted Google token blobs |
| Audio | Never backed up: it exists only in the ingest tmpfs and is destroyed after verified cleanup |
| Encryption | The dump and the identifier-only deletion log are encrypted with GPG AES-256 before they are written; no plaintext archive touches the backup directory |
| Retention | **30 days maximum**, matching `RESIDUAL_RETENTION_DAYS` in `apps/api/app/services/deletion.py` |
| Expiration | `backup` prunes files older than 30 days; `restore` refuses an expired archive and an expired manifest is never used |
| Deletions | A clean restore replays the deletion log, so an account, class, subject or conversation deleted after the backup does **not** reappear |
| Deletion log | Identifiers and tombstone timestamps only; no profile, transcript, message, prompt, token or error payload |
| Target | A new, clean database; never the live database |

The 30-day backup window and the 30-day residual window are deliberately equal. A backup old
enough to predate a purged deletion record is already outside the retention window, so it
cannot be restored. If either window changes, change both in the same revision.

## 2. Prerequisites

- `pg_dump` and `pg_restore` version 16 (the server version), `gpg`, `python3`, `psql`.
- A database role that is a superuser or has `BYPASSRLS`. Every tenant table uses forced
  row-level security; a normal owner would silently dump zero tenant rows.
- The target cluster must already have the runtime roles `maulwurf_app`, `maulwurf_jobs` and
  `maulwurf_ingest`. `pg_dump` does not include roles, and the restored RLS policies name
  them. A cluster that already ran the migrations has them; otherwise create them before the
  restore.
- The passphrase in a backend secret store, exposed only as
  `MAULWURF_BACKUP_PASSPHRASE` or `MAULWURF_BACKUP_PASSPHRASE_FILE` (at least 16 characters).
  Never put it in a command line, the repository, a ticket or a log.
- A backup directory outside the repository with mode `0700`, owned by the backup operator.
  Prefer an encrypted volume; the streamed plaintext exists only in the `pg_dump`/GPG pipes.

## 3. Backup

Run from the repository root:

```bash
export MAULWURF_BACKUP_PASSPHRASE_FILE=/run/secrets/maulwurf-backup
scripts/ops/backup_restore.sh backup \
  --database-url 'postgresql://maulwurf@postgres:5432/maulwurf' \
  --out /srv/maulwurf-backups \
  --label daily
```

The command writes three files and prints only their names:

| File | Content |
|---|---|
| `maulwurf-<UTC>-<label>.dump.gpg` | Encrypted `pg_dump --format=custom` archive |
| `maulwurf-<UTC>-<label>.deletions.json.gpg` | Encrypted identifier-only deletion log at backup time |
| `maulwurf-<UTC>-<label>.manifest.json` | Format, UTC creation time, 30-day retention, SHA-256 of both encrypted files |

The command fails closed when the passphrase is missing or short, the role cannot see every
tenant, a `bytea` column outside `google_credentials.access_token_enc` and
`google_credentials.refresh_token_enc` exists, or a half-written archive would remain.

## 4. Deletion log after a deletion

A backup carries the tombstones that existed when it was taken. To restore an **older**
backup without resurrecting data deleted later, export the current log after the deletion
(or on a schedule) to a separate location:

```bash
scripts/ops/backup_restore.sh export-deletions \
  --database-url 'postgresql://maulwurf@postgres:5432/maulwurf' \
  --out /srv/maulwurf-backups/post-delete.deletions.json.gpg
```

The log contains `account_deletions` rows plus the `users`, `audios`, `subjects` and
`conversations` tombstones. No text is included. The restore reads both the log passed with
`--deletion-log` and the tombstones already present in the restored database, so a backup
taken after the deletion is replayed correctly too.

## 5. Verify before restoring

```bash
scripts/ops/backup_restore.sh verify \
  --backup /srv/maulwurf-backups/maulwurf-20261101T070000Z-daily.dump.gpg
```

`verify` checks the manifest SHA-256 and the 30-day window without opening a database. It
exits non-zero for a modified, missing or expired archive. A backup is not restorable after
the window even if the file still exists.

## 6. Clean restore

Create a new empty database (or an empty cluster) and keep the live database untouched:

```bash
createdb -h postgres -U maulwurf maulwurf_restore
scripts/ops/backup_restore.sh restore \
  --database-url 'postgresql://maulwurf@postgres:5432/maulwurf_restore' \
  --backup /srv/maulwurf-backups/maulwurf-20261101T070000Z-daily.dump.gpg \
  --deletion-log /srv/maulwurf-backups/post-delete.deletions.json.gpg \
  --confirm-clean
```

`restore` refuses to continue when:

- the manifest is expired or the encrypted dump checksum differs;
- the target has application tables or a non-default extension;
- `--confirm-clean` is missing.

It decrypts straight into `pg_restore`, applies
[`scripts/ops/restore_reapply.sql`](../../scripts/ops/restore_reapply.sql), and removes the
temporary plaintext deletion log on exit. The replay:

1. inserts or updates the residual `account_deletions` rows from the log;
2. purges every tenant row of a deleted account and scrubs the `users` tombstone to the
   opaque `deleted-<id>@invalid` / `deleted:<id>` placeholders;
3. marks tombstoned `audios`, purges their transcripts, segments, chunks, embeddings,
   citations, tasks and pending work, and empties the messages that cited them;
4. preserves tombstoned `subjects` and `conversations`, clearing the conversation title and
   purging its messages and sources.

## 7. Post-restore verification

Run these read-only checks against the restored database. They print counts only, never
content:

```sql
SELECT status, email, google_sub FROM users WHERE status <> 'active';
SELECT count(*) FROM account_deletions WHERE status <> 'completed';
SELECT id FROM audios WHERE deleted_at IS NOT NULL;
SELECT count(*) FROM conversations WHERE deleted_at IS NOT NULL AND title IS NOT NULL;
```

Expected: every deleted user is `deleted` with opaque placeholders, every residual operation
is `completed`, and a deleted conversation has no title. Then run the isolated API against
the restored database and exercise login, library, search and chat with a synthetic tenant.
Do not point the live compose stack at the restored database until the checks pass.

## 8. Failure handling

| Failure | Action |
|---|---|
| `verify` reports a checksum mismatch | Do not restore. Take a new backup from the live database or locate the intact copy; never edit the manifest to bypass the check |
| `verify` reports `expired` | The archive is outside D8. Restore is not allowed; use a newer backup |
| Target is not clean | Choose another database. Do not drop the live database to make the check pass |
| `pg_restore` fails | Drop the target database, fix the cause (version, extension, role) and repeat the clean restore. The live database was never touched |
| Replay fails | The transaction rolls back; the target keeps the pre-replay state. Fix the log/manifest and run the restore again from an empty target |
| Missing deletion log | A backup older than a deletion cannot be safely restored unless the deletion is already inside it. Prefer the newest backup and export the log before the incident |

## 9. Evidence

The executable validation is `I-S4-AN-05`:

```bash
cd apps/api
uv run pytest tests/integration/test_backup_restore.py -q
```

It creates a source database, seeds text/metadata, takes the encrypted backup, deletes an
account, tombstones a class and a conversation, exports the log, restores into a second empty
database, and asserts that nothing deleted reappears while retained rows survive. It also
checks retention, pruning, the expired-backup refusal and the fail-closed binary-column
guard. Local result on 2026-11-01 (PostgreSQL 16.15, GPG 2.4): **1 passed**.

The reference-environment drill (J4.8) must repeat this procedure against the real backup
volume and secret store and record the command, result and redacted evidence. Until that
drill exists, this runbook is validated locally, not in production.
