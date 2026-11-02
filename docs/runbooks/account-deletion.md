# Account deletion runbook (J4.8)

> **Ticket:** J4.8 (`MAULW-146`) · **Depends on:** A4.4, A4.5, J3.3 · **Test:** C-S4-JF-10
> (the executable contract is I-S4-AN-04). **Review:** an active API-area claim or the lead,
> plus the security/privacy reviewer for any Google-side action.
>
> This runbook drives the observable, resumable `DELETE /me` operation. It never copies a
> profile, transcript, prompt, token or provider payload into the incident or the evidence.
>
> References: [specification §M1/§M8 and §8](../ESPECIFICACION.md),
> [restore](restore.md), [tokens](tokens.md), [provider outage](provider-down.md).

## 1. Contract

| Step | Endpoint | Result |
|---|---|---|
| Request | `DELETE /me` | `202` + `{deletion_operation_id}`. The `users` tombstone, blocked sessions/jobs and the residual operation are committed before any remote work |
| Status | `GET /me/deletion/{id}` | `pending`, `running`, `completed` or `completed_with_remote_failures`; `no-store`; owner/restricted-session scoped |
| Resume | `POST /me/deletion/{id}/resume` | `202` while pending/running, `200` when already closed; never repeats a successful Google delete |
| Retention | hourly scheduler | Expired `account_deletions` and `google_remote_operations` rows are deleted in bounded batches after **30 days** |

The initiating session enters **restricted mode**: after the tombstone it can only read the
deletion status, resume that operation and log out. Every other session is revoked. The
operation is idempotent per former user; a second `DELETE /me` returns the same id.

`delete_maulwurf_events` defaults to `false` (keep the Google events). When the user
explicitly requests deletion, the task-linked and mirrored Maulwurf events plus the dedicated
write calendar are deleted **before** the encrypted credential is removed. The operation
records the authorized remote ids and a redacted result; it never stores the event body,
title, attendee or token.

## 2. Roles and access

- **Requester:** the authenticated user. A support operator must not press `DELETE /me` on
  the user's behalf without a recorded authorization.
- **Executor:** an operator with the API and database access needed for the verification.
  Use a short-lived session; never a shared administrator account.
- **Reviewer:** an active API-area claim or the lead. The security/privacy reviewer signs
  any evidence that includes a Google-side fact.
- Use UTC for every timestamp. The operation id is the only identifier needed in the
  incident; do not copy the former email or display name.

## 3. Request the deletion

Preferred path: the authenticated UI. The API equivalent, with the user's session cookie and
CSRF token:

```bash
curl -fsS -X DELETE https://<host>/me \
  -b cookies.txt \
  -H "Origin: https://<host>" \
  -H "X-CSRF-Token: <csrf>" \
  -H 'Content-Type: application/json' \
  -d '{"delete_maulwurf_events": false}'
```

Expected: HTTP `202` and a body such as `{"deletion_operation_id":"<uuid>"}`. A missing
Origin/CSRF is `403`; an unknown field is `422`; an unauthenticated call is `401`. Set
`delete_maulwurf_events` to `true` only after the user's explicit instruction to remove the
Google events too.

The remote phase runs inline after the tombstone. A provider failure does not turn the
`202` into a `500`: the operation stays `pending`/`running` and can be resumed.

## 4. Observe the operation

```bash
curl -fsS https://<host>/me/deletion/<operation-id> -b cookies.txt
```

Expected fields: `status`, `delete_maulwurf_events`, `created_at`, `completed_at`,
`expires_at` and a redacted `error_summary` (closed provider codes only). `expires_at` is 30
days after creation; after that the status route answers `404` and the hourly job deletes the
residual row.

Typical terminal states:

- `completed`: remote work (if authorized) and the purge both succeeded.
- `completed_with_remote_failures`: the local purge succeeded but at least one Google
  deletion failed. The user and the residual record stay observable; successful deletes are
  not repeated.
- `pending`/`running` after the inline call: the process lost the claim or a purge failed.
  Resume it (below); do not delete the row by hand.

## 5. Resume a stuck operation

```bash
curl -fsS -X POST https://<host>/me/deletion/<operation-id>/resume \
  -b cookies.txt \
  -H "Origin: https://<host>" \
  -H "X-CSRF-Token: <csrf>"
```

Expected: `202` when it was pending/running, `200` with the final status when it was already
closed. The resume requires the restricted initiating session plus Origin/CSRF; another
session gets `401`, a foreign/expired operation gets `404`.

Execution is serialized by an atomic `pending -> running` claim with a one-hour lease. A
crash after the claim is reclaimed only after the lease, so two operators must not "help" by
running SQL deletes or by editing `status` in parallel. If the claim is live, wait; if the
lease expired, the resume is the supported path.

## 6. What the purge removes

The tenant rows are deleted in FK order: sessions, subjects, classes and their transcripts,
segments, chunks, embeddings and citations, tasks/revisions/sources, conversations and
messages, notifications, attempts, outbox rows, settings and encrypted Google credentials.
The `users` row keeps only an opaque `deleted-<id>@invalid` / `deleted:<id>` placeholder and
the `deleted` status. Residual tables keep only the opaque former user id, operation status,
authorization, timestamps, the 30-day expiry and remote ids with redacted error codes.

Read-only verification (print counts only; do not select text):

```sql
-- Replace the UUID with the operation's former user id, kept in the incident.
SELECT status, email, google_sub FROM users WHERE id = '<former-user-id>';
SELECT status, delete_events, error_summary, expires_at
  FROM account_deletions WHERE id = '<operation-id>';
SELECT kind, status, count(*) FROM google_remote_operations
 WHERE user_id = '<former-user-id>' GROUP BY kind, status;
SELECT count(*) FROM sessions WHERE user_id = '<former-user-id>';
SELECT count(*) FROM subjects WHERE user_id = '<former-user-id>';
SELECT count(*) FROM audios WHERE user_id = '<former-user-id>';
SELECT count(*) FROM conversations WHERE user_id = '<former-user-id>';
SELECT count(*) FROM messages WHERE user_id = '<former-user-id>';
SELECT count(*) FROM notifications WHERE user_id = '<former-user-id>';
```

Expected: the user is `deleted` with placeholders; every tenant count is `0`; the residual
operation and its remote ids exist until the 30-day expiry. Then run the read-only orphan
sweep as the cross-tenant jobs role:

```bash
cd /path/to/maulwurf
MAULWURF_ENV=local MAULWURF_DATABASE_ROLE=maulwurf_jobs \
  MAULWURF_DATABASE_URL='postgresql+asyncpg://maulwurf:<password>@<host>:5432/maulwurf' \
  PYTHONPATH=apps/api \
  python scripts/maintenance/sweep_orphans.py --user-id '<former-user-id>'
```

The command is read-only and prints only check names and counts; never put the resolved DSN
in the evidence.

Expected: `clean` (exit `0`). A non-clean result is an incident: do not fix it by deleting
rows manually; record the check names and follow A4.7.

## 7. Google-side failures

`completed_with_remote_failures` means the local purge is complete but one or more Google
operations failed. Inspect only `kind`, `status`, `error_code` and the timestamp (never the
calendar/event body):

```sql
SELECT kind, status, error_code, attempts, finished_at
  FROM google_remote_operations
 WHERE user_id = '<former-user-id>' ORDER BY created_at;
```

If the error is a revoked grant (`invalid_grant`), the credential is already gone; the
remote event may remain in Google's calendar. The user authorized the deletion, so an
operator may finish it manually in the Google account with the user's authorization, record
the fact (ids redacted) and leave the residual row as the audit trail. There is no automatic
retry for a terminal remote failure. Do not reset the operation to `pending`; open a
follow-up if a retry surface is required.

## 8. Backups and restore

A clean restore replays the deletion log pinned by the backup manifest, so a deleted account
does not reappear. The 30-day residual window equals the 30-day D8 backup window. If a backup
is older than the deletion and its log was not refreshed, the restore **fails closed**; use
`export-deletions --refresh-backup` while the source is available. See
[restore.md](restore.md) §4–§6.

Do not restore a deleted account into a live database. If an incident requires a forensic
copy, use a new, clean database and keep it isolated.

## 9. Evidence and limits

The executable contract is I-S4-AN-04 in
`apps/api/tests/integration/test_account_deletion.py`; the local C-S4-JF-10 record is in
[`docs/sprints/artifacts/J4.8/`](../sprints/artifacts/J4.8/). The real Google deletion drill
(protected account, real Calendar IDs) is **NOT RUN** until J4.7 and the authorized test
account exist. The suite proves tombstones, restricted sessions, idempotency, concurrent
claims, authorized Google deletion before token removal, resumability and the retention
purge with a deterministic Google double.

Stop conditions:

- the tombstone is not durable before any Google call;
- a remote deletion is repeated after it succeeded;
- tenant rows are deleted from a partially restored target instead of discarding it;
- a profile, transcript, token or Google event body is copied into the incident or evidence.
