# Token and credential runbook (J4.8)

> **Ticket:** J4.8 (`MAULW-146`) · **Depends on:** J4.7, S4.1, A4.5 · **Test:**
> C-S4-JF-10. **Review:** an active Infra claim, the lead and the security/privacy reviewer
> for anything that touches Google grants or encryption keys.
>
> This runbook covers revoked, expired, rotated or leaked credentials. It never prints a
> value and never stores a secret in the repository, a ticket, a shell history, a screenshot
> or an evidence file.
>
> References: [specification §7–§8](../ESPECIFICACION.md), [ADR-0008](../adr/ADR-0008-ingest-reception.md),
> [provider outage](provider-down.md), [restore](restore.md), [development runbook](../RUNBOOK-dev.md).

## 1. Inventory

| Credential | Location | Owner | Rotation support today |
|---|---|---|---|
| Google OAuth client secret | backend secret store (`MAULWURF_GOOGLE_CLIENT_SECRET`) | deployment operator + Google Cloud project | Replace and redeploy; old secret stays valid at Google until revoked |
| Google refresh/access tokens | `google_credentials` table, AES-GCM encrypted | each user, issued by Google | `invalid_grant` auto-disconnects; user reconnects with consent |
| Ingest shared credential | `MAULWURF_INGEST_CREDENTIAL_SECRET`/`_KID` on `api`, `INGEST_CREDENTIAL_SECRET`/`_KID` on `ingest` | deployment operator | **Dual-key rotation** with `INGEST_CREDENTIAL_PREVIOUS_*` |
| NVIDIA Riva API key | `NVIDIA_API_KEY` on `ingest` only | deployment operator + NVIDIA | Replace and redeploy; no dual key in this codebase |
| LLM API key | `MAULWURF_LLM_API_KEY` on `api`, `worker`, `scheduler` | deployment operator + provider | Replace and redeploy |
| Embeddings API key | `MAULWURF_EMBEDDING_API_KEY` on `api`, `worker` | deployment operator + provider | Replace and redeploy |
| Session/OAuth state secrets | `MAULWURF_SECRET_KEY`, `MAULWURF_OAUTH_STATE_SECRET` | deployment operator | Replace and redeploy; it invalidates live sessions/states |
| Data encryption key | `MAULWURF_ENCRYPTION_KEY` + `MAULWURF_ENCRYPTION_KEY_VERSION` | deployment operator | **Not supported across versions** — stop and open a follow-up |
| Backup passphrase | `MAULWURF_BACKUP_PASSPHRASE` or `_FILE` | backup operator | New backups can use a new passphrase; old archives need the old one |

The database is never a place to read a secret back: `google_credentials` stores encrypted
blobs and the schema deliberately has no plaintext token column.

## 2. Global rules

1. Work from the secret store, not from a shell variable exported in a shared terminal.
   Use a file descriptor or the deployment manager's secret injection where possible.
2. Never run `docker compose config` on a production env file: it prints resolved secrets.
   To prove presence, print a boolean:

   ```bash
   docker compose exec -T api python -c \
     "import os; print('llm_key_present', bool(os.environ.get('MAULWURF_LLM_API_KEY')))"
   ```

3. Rotate in two phases: **deploy the new credential**, verify the path, **then revoke the
   old one**. Revoking first guarantees an outage.
4. After any Google grant change, verify `GET /integrations/status` and one Calendar read;
   after any Riva change, run one synthetic clip through the authorized test setup, never a
   real class.
5. If a credential leaked, treat the incident as a privacy event: revoke first, then rotate,
   then review logs/artifacts for exposure. Do not paste the value into the incident.
6. Every command below is read-only or changes deployment configuration. No SQL is needed
   for a normal rotation.

## 3. Ingest credential: zero-downtime dual-key rotation

The `api` mints a token with the current `kid`/secret; `ingest` verifies against the current
and the optional previous pair. Rotation order:

```bash
# 1. Generate the new secret without printing it.
openssl rand -hex 32 > /run/secrets/maulwurf-ingest-next
chmod 600 /run/secrets/maulwurf-ingest-next

# 2. Deploy ingest with the new current pair and the old pair as previous.
#    The deployment reads these from its secret store, not from this shell.
#    INGEST_CREDENTIAL_SECRET=<new>          INGEST_CREDENTIAL_KID=k2
#    INGEST_CREDENTIAL_PREVIOUS_SECRET=<old> INGEST_CREDENTIAL_PREVIOUS_KID=k1

# 3. Deploy api with the new pair (it mints only with the current kid).
#    MAULWURF_INGEST_CREDENTIAL_SECRET=<new> MAULWURF_INGEST_CREDENTIAL_KID=k2

# 4. Verify the hop and that the old token is no longer minted.
cd infra
docker compose exec -T ingest python -c \
  "import os; print('current_kid', os.environ.get('INGEST_CREDENTIAL_KID')); print('previous_kid', os.environ.get('INGEST_CREDENTIAL_PREVIOUS_KID'))"
docker compose logs --since 10m api ingest | grep -E 'internal_credential|401|credential'
```

In Compose the previous pair is exported as `INGEST_CREDENTIAL_PREVIOUS_SECRET`/`_KID` in
the shared `.env` (the `INGEST_` prefix keeps it outside the API's unknown-`MAULWURF_*`
guard); `api` receives the current pair as
`MAULWURF_INGEST_CREDENTIAL_SECRET`/`MAULWURF_INGEST_CREDENTIAL_KID`. An empty previous
pair is treated as absent by the ingest settings.

Expected: no `401` from `ingest` after the API rollout; the old `kid` is accepted only while
it is the configured previous pair. Then remove `INGEST_CREDENTIAL_PREVIOUS_*` and redeploy
`ingest`; from that point an old token is rejected. The `k1`→`k2` transition is covered by
the ingest credential tests; a real deployment must record the two deployments and the
absence of 401s.

**Revocation without a graceful window:** deploy ingest with only the new pair. In-flight
`api` instances still minting the old `kid` receive `401 internal_credential_invalid`; they
do not touch the attempt row. Finish the `api` rollout immediately.

## 4. Google credentials

### 4.1 `invalid_grant` (refresh token revoked or expired)

Symptom: `google_tokens.invalid_grant user_id=<uuid>` in the API log; the credential is
`disconnected`; a task stays `blocked`; `GET /integrations/status` reports
`"status": "disconnected"`.

Procedure:

1. Do not retry the job; `invalid_grant` is terminal by design.
2. Ask the user to reconnect from the integrations screen. The app starts an incremental
   consent and stores a new encrypted grant; A4.4's deletion flow is not involved.
3. If the account is gone or the user cannot consent, leave it disconnected. Calendar
   writes remain blocked; the library and chat keep working.
4. Verify:

   ```bash
   curl -fsS https://<host>/integrations/status -b cookies.txt
   curl -fsS -X POST https://<host>/calendar/sync -b cookies.txt \
     -H "Origin: https://<host>" -H "X-CSRF-Token: <csrf>"
   ```

   Expected: `connected`, and a `202` with the covered -7/+60 window; a transient provider failure
   is `503` and leaves the mirror untouched.

### 4.2 Emergency revoke of one user's grant

Prefer revoking the app's access in the user's Google account (Google Security → Third-party
access). The next refresh receives `invalid_grant` and the integration disconnects without a
retry. If the incident requires immediate containment before that refresh, mark the row
`disconnected` with the operator database role, never by deleting the encrypted blob first:

```sql
UPDATE google_credentials SET status = 'disconnected' WHERE user_id = '<uuid>';
```

Then revoke at Google and ask the user to reconnect. Do not log the row, the ciphertext or
the scopes beyond the minimum.

### 4.3 OAuth client secret rotation

1. Create the new client secret in the Google Cloud project; keep the old one valid.
2. Deploy `api` with the new `MAULWURF_GOOGLE_CLIENT_SECRET`.
3. Verify one login and `GET /integrations/status` with a test account.
4. Revoke the old client secret in Google Cloud.
5. Existing refresh tokens remain valid; a client-secret change does not by itself force a
   reconnect.

## 5. Provider API keys

### 5.1 NVIDIA Riva (`NVIDIA_API_KEY`)

1. Create the replacement key in the NVIDIA account if the provider allows overlapping
   keys.
2. Deploy `ingest` with the new key (the value never reaches `api`; the compose service has
   an explicit allowlist).
3. Verify the key is present as a boolean, then run one synthetic clip through the
   authorized test setup. `P-*` provider-contract runs require their own budget.
4. Revoke the old key at NVIDIA.
5. If the old key was already revoked, uploads end `requires_reupload` with
   `asr_rejected`; this is a known outage, not a reason to retry blindly.

### 5.2 LLM and embeddings

1. Create the new key at the provider.
2. Deploy `api`, `worker` and `scheduler` with the new value.
3. Verify one chat SSE answer ends `done` and one index generation activates.
4. Revoke the old key.
5. A 401/403 is permanent: do not retry it as transient, and do not replace a failed
   embedding with a zero vector.

## 6. Session, OAuth-state and encryption keys

- `MAULWURF_SECRET_KEY` and `MAULWURF_OAUTH_STATE_SECRET` are boundary secrets. Rotating
  them is a planned maintenance action: every opaque session and in-flight OAuth state
  becomes invalid. Announce it, rotate, and verify `/healthz`, login and one CSRF mutation.
- `MAULWURF_ENCRYPTION_KEY`/`MAULWURF_ENCRYPTION_KEY_VERSION` rotation is **not
  implemented**. `app/core/crypto.py` decrypts only with the current version and raises
  `DecryptionError` for another. Do not change the version in production: it would make
  every stored Google token unreadable. Open a dedicated ticket for a multi-version keyring
  and a re-encryption job; until then, treat this key as long-lived and protect it
  accordingly.

## 7. Backup passphrase

`backup_restore.sh` accepts one passphrase per run. A new passphrase applies to new backups;
an archive encrypted with the old passphrase still needs the old passphrase to restore.
Keep both in the secret store until the old archives leave the 30-day retention window. See
[restore.md](restore.md) §6–§8.

## 8. Verification and evidence

Run the deterministic suites that pin the rotation paths:

```bash
cd apps/api
uv run pytest tests/unit/test_google_tokens.py tests/unit/test_google_calendar.py -q
uv run pytest tests/unit/test_ingest_credential.py -q
cd ../ingest
uv run pytest tests/test_credential_catalog.py tests/test_riva_client.py -q
```

Expected: `invalid_grant` disconnects without retry; a wrong/old ingest `kid` is rejected; a
missing NVIDIA key makes ASR unavailable instead of using a placeholder. These are local
deterministic results, not a real provider revocation.

Evidence for C-S4-JF-10 lives in
[`docs/sprints/artifacts/J4.8/`](../sprints/artifacts/J4.8/). The reference drill (real
Google/NVIDIA revocation, real secret-store rotation) is **NOT RUN** until J4.7 exists.

Stop conditions:

- a secret appears in a command line, log, ticket, screenshot or repository file;
- the encryption key version is changed without a keyring and re-encryption plan;
- an `invalid_grant` or 401/403 is retried as a transient error;
- a rotation is declared complete without a verified request on the new credential.
