# Security cross-review (L4.2)

Checklist for C-S4-JL-02. The author drafted it from the code and the tests named below.
It is not a peer review. A reviewer other than the author still has to run it and sign.

| | |
|---|---|
| Author | satnovaOG |
| Date drafted (UTC) | 2026-11-02 |
| Reviewer | |
| Reviewed at (UTC) | |
| Signature | unsigned |

Status: **unsigned**. Do not treat any row as accepted until the signature line is filled
by someone who did not write this file.

## 1. Tenant isolation in SSE, tools, and jobs

Cited. `apps/api/tests/integration/test_isolation_final.py` covers library, search, chat,
and `test_sse_channels_reject_foreign_ids_before_streaming`. `app/services/tools.py`
takes the tenant from the authenticated user and ignores a user id in the model payload.
`analyze` claims across tenants only to find the owner, then calls `bind_user` for the
read and the write (`app/workers/jobs/analyze.py`).

Gap. This draft did not re-run that suite. A peer still has to.

## 2. Validated evidence

Cited for tasks. A proposal whose quote is not the segment slice is dropped.
`app/services/analysis_validation.py` and `app/services/evidence.py`
(`resolve_textual_evidence`) rebuild the span from the persisted segment. The analyze job
does not insert that item.

Gap. The L3.8 class summary is stored after a shape and secret check
(`app/services/summary.py`). It is not a quote reconstructed from the transcript. It must
not be presented as evidence. Task rows remain the only place a citation is persisted.

## 3. No external write without confirmation

Cited. `propose_task` creates `source=chat` with `calendar_sync_status=not_requested`
and does not call Calendar or Gmail (`app/services/tools.py`;
`tests/integration/test_tool_proposals.py`). `update_is_safe` refuses an in-place update
of a confirmed, dismissed, or completed task; reanalysis writes a
`pending_confirmation` revision (`app/services/analysis.py`).

Gap. This draft did not re-execute the Calendar confirm and sync suites (J3.4 / J3.6).
Those jobs are the only path that talks to Google, and only after confirmation enqueues
the outbox. A peer should re-read that path before signing.

## 4. Secrets out of logs and traces

Cited. The analyze job logs the audio id, the run id, the attempt, and
`type(exc).__name__`. It does not log the transcript, the summary, or the exception
text (`app/workers/jobs/analyze.py`). `tests/integration/test_redis_policy.py` forbids
`Bearer ` and `NVIDIA_API_KEY` in Redis values. `test_openapi_exposes_contract_without_secrets`
in `tests/integration/test_a18_endpoints.py` forbids those markers in the OpenAPI document.

Gap. There is no repo-wide log scan in this draft. `riva_client.py` places the key in
gRPC metadata (`authorization`, `Bearer` plus the env value). This review did not prove
the gRPC stack does not log metadata.

## 5. `NVIDIA_API_KEY` stays on the backend

Cited. The name is read from the environment in `apps/ingest/maulwurf_ingest/asr/riva_client.py`.
Compose does not give it a default (`scripts/ci/check_compose_ingest_env.py`). A search of
`apps/web` finds no `NVIDIA_API_KEY`. The API OpenAPI document is asserted not to contain
the name.

Gap. Cloud retention of audio sent to NVIDIA is still unresolved (D3-Audio `blocked`).
Backend-only storage of the key does not answer that.

## What would close C-S4-JL-02

1. A reviewer who is not the author re-runs the cited suites.
2. That reviewer fills the name, the UTC date, and the signature above.
3. Any row they cannot confirm stays a gap. A signature does not delete a gap.
