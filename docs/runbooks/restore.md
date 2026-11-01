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
| Deletions | A clean restore replays the deletion log pinned by the manifest, so an account, class, subject or conversation deleted after the backup does **not** reappear; a missing log fails closed |
| Deletion log | Identifiers and tombstone timestamps only; no profile, transcript, message, prompt, token or error payload. Its SHA-256 is pinned in the manifest and a log that does not match is never decrypted |
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
| `maulwurf-<UTC>-<label>.deletions.json.gpg` | Encrypted identifier-only deletion log; the manifest pins its SHA-256 |
| `maulwurf-<UTC>-<label>.manifest.json` | Format, UTC creation time, 30-day retention, SHA-256 of both encrypted files |

The command fails closed when the passphrase is missing or short, the role cannot see every
tenant, a `bytea` column outside `google_credentials.access_token_enc` and
`google_credentials.refresh_token_enc` exists, the database contains large objects, or a
half-written archive would remain. The connection URL is never passed as a command-line
argument: the script exports `PG*` variables and writes the password to a private
`PGPASSFILE`, so `ps` and `/proc/<pid>/cmdline` do not expose credentials.

## 4. Deletion log after a deletion

A backup carries the tombstones that existed when it was taken. To restore an **older**
backup without resurrecting data deleted later, refresh that backup's deletion log while
the source database is still available:

```bash
scripts/ops/backup_restore.sh export-deletions \
  --database-url 'postgresql://maulwurf@postgres:5432/maulwurf' \
  --refresh-backup /srv/maulwurf-backups/maulwurf-20261101T070000Z-daily.dump.gpg
```

`--refresh-backup` exports the current `account_deletions` rows and the `users`, `audios`,
`subjects` and `conversations` tombstones, replaces the paired
`maulwurf-<UTC>-<label>.deletions.json.gpg` atomically, and updates the manifest's
`deletion_log` and `deletion_log_sha256`. It refuses to touch an expired, modified or
incomplete backup. The log contains identifiers and timestamps only; no text is included.

`--out` exports a standalone copy for archival or evidence. Such a copy is **not** accepted
by `restore`: every log replayed must be the one the manifest pins. To restore an older dump
with a post-deletion log, attach it with `--refresh-backup` first, or take a new backup after
the deletion (its dump and log already contain the tombstones).

## 5. Verify before restoring

```bash
scripts/ops/backup_restore.sh verify \
  --backup /srv/maulwurf-backups/maulwurf-20261101T070000Z-daily.dump.gpg
```

`verify` checks the manifest SHA-256 of the dump and of the pinned deletion log, and the
30-day window, without opening a database. It exits non-zero for a modified, missing or
expired archive. When the manifest pins a deletion log and the file is not next to the dump,
`verify` prints an explicit `WARNING` and **fails**: a backup set without its log can
resurrect data on restore. `--allow-missing-deletion-log` verifies the dump alone and keeps
the warning, but it does not make a restore safe. A backup is not restorable after the D8
window even if the file still exists.

## 6. Clean restore

Create a new empty database (or an empty cluster) and keep the live database untouched:

```bash
createdb -h postgres -U maulwurf maulwurf_restore
scripts/ops/backup_restore.sh restore \
  --database-url 'postgresql://maulwurf@postgres:5432/maulwurf_restore' \
  --backup /srv/maulwurf-backups/maulwurf-20261101T070000Z-daily.dump.gpg \
  --confirm-clean
```

By default `restore` replays the deletion log bundled with the backup. It refuses to
continue when:

- the manifest is expired, the encrypted dump checksum differs, or the pinned deletion log
  is missing;
- an explicit `--deletion-log` does not match `deletion_log_sha256` in the manifest (a log
  from another backup or a tampered file is rejected **before** anything is decrypted);
- the target has application tables or a non-default extension;
- the target cluster is missing `maulwurf_app`, `maulwurf_jobs` or `maulwurf_ingest`;
- `--confirm-clean` is missing.

`--allow-missing-deletion-log` is the only way to restore without a log. It prints a
`WARNING` to stderr and replays only the tombstones already inside the dump, so an account,
class, subject or conversation deleted after the backup **can reappear**. Use it only for a
documented recovery decision, never as the normal path.

It decrypts straight into `pg_restore`, applies
[`scripts/ops/restore_reapply.sql`](../../scripts/ops/restore_reapply.sql), and removes the
temporary plaintext deletion log on exit. The replay:

1. inserts or updates the residual `account_deletions` rows from the log;
2. purges every tenant row of a deleted account and scrubs the `users` tombstone to the
   opaque `deleted-<id>@invalid` / `deleted:<id>` placeholders; the log's `users` array also
   covers a user tombstoned without a residual `account_deletions` row;
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
| `verify` or `restore` reports a missing deletion log | Locate the paired log, refresh it with `export-deletions --refresh-backup` while the source is available, or take a new backup. `--allow-missing-deletion-log` only for a documented decision: it warns and can resurrect deleted data |
| `restore` rejects an explicit deletion log | Its SHA-256 is not the one in the manifest. Use the paired log or attach the intended log with `export-deletions --refresh-backup`; never edit the manifest |
| Target is not clean | Choose another database. Do not drop the live database to make the check pass |
| `pg_restore` fails | **Discard the target database** and repeat the restore into a new, clean database. `pg_restore` may already have committed rows, so the target may contain the data the dump carried, including rows deleted later. The live database was never touched |
| Replay fails | **Discard the target database** and restore into a new, clean database. The replay transaction rolls back, but `pg_restore` already wrote the dump (including rows deleted after it); the target is partially restored and must not be used |
| Missing deletion log | A backup older than a deletion fails closed without its log. Prefer a new backup after the deletion, or refresh the old backup's log with `export-deletions --refresh-backup`; export scheduled logs before an incident |

## 9. Evidence

The executable validation is `I-S4-AN-05`:

```bash
cd apps/api
uv run pytest tests/integration/test_backup_restore.py -q
```

It creates a source database, seeds text/metadata, takes the encrypted backup, deletes an
account, tombstones a class, a subject and a conversation, refreshes the backup's deletion
log, restores into a second empty database, and asserts that nothing deleted reappears while
retained rows survive. It also checks retention, pruning, the expired archive refusal, the
fail-closed binary-column guard, and the deletion-log rules: a missing log is refused and an
explicit log from another backup is rejected by checksum before the target is touched,
while `--allow-missing-deletion-log` emits the warning. Local result on 2026-11-01
(PostgreSQL 16.15, GPG 2.4): see the A4.5 artifact.

The reference-environment drill (J4.8) must repeat this procedure against the real backup
volume and secret store and record the command, result and redacted evidence. Until that
drill exists, this runbook is validated locally, not in production.
