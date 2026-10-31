# ADR-0010 — Ingress: Caddy terminates TLS directly; Cloudflare Tunnel dropped

- Date (UTC): 2026-10-31
- Status: Accepted (decided under the J2.10 claim owned by jpinillaz; the ticket PR is the
  review)
- Ticket: J2.10 (gap P1, QA review of 2026-10-11)
- Closes: the "Ingress and upload limit" row of [ARCH.md](../ARCH.md) §7 and the
  "Upload limits" row of §6
- Changes: [PROPUESTA_PROYECTO_FINAL.md](../PROPUESTA_PROYECTO_FINAL.md) §5 (infrastructure
  row), [plan/S4.md](../plan/S4.md) J4.7 and
  [DISENO_BD_API_SPRINTS.md](../DISENO_BD_API_SPRINTS.md) J4.7
- Related: [ADR-0008](ADR-0008-api-ingest-reception.md) (API→ingest reception),
  [J1.3](../plan/S1.md) (Caddy without buffering), S1.A8/D4 (unapproved limits),
  [J2.10 measurement record](../spike/J2.10-ingress-limits.md),
  `infra/Caddyfile`, `scripts/load/ingress_limits.sh`
- Tests: C-S2-JF-13

## Context

The original proposal put Cloudflare Tunnel (`cloudflared`) and Cloudflare bot protection
in front of Caddy. That design is no longer planned:

- Cloudflare documents a 100 MB request-body limit on Free and Pro plans and 200 MB on
  Business, below the provisional 200 MiB target. A body cut at the edge would be answered
  by the edge, not by the API contract.
- A tunnel or CDN in front of Caddy has its own timeouts and may buffer request bodies.
  Uploads are the one stream that must reach the API without a durable copy, so an
  intermediary that retains or replays the body is a privacy and capacity risk.
- It adds a third party and a second credential to the audio path for no functional
  requirement of the single-host deployment.

The repository already exposes Caddy directly on 80/443 in `infra/docker-compose.yml`
with `tls internal` for local development. Production deployment is J4.7 and is not
implemented yet, so the ingress decision must be explicit before D4/M2 publish limits.

## Options

| | (a) Caddy is the only HTTP ingress | (b) L4/TCP load balancer in front | (c) Cloudflare Tunnel or an HTTP CDN | (d) API gateway / ingress controller |
|---|---|---|---|---|
| Body limit | Only Caddy's `request_body` | None at L4; Caddy still applies its own | 100/200 MB and plan-dependent | Another HTTP hop with its own limit |
| Buffering | None; Caddy streams to the upstream | None at L4; Caddy streams | Possible; depends on the provider | Possible; depends on the product |
| Audio path | Browser → Caddy → API | Browser → L4 → Caddy → API | Browser → provider → tunnel → Caddy → API | Browser → gateway → Caddy → API |
| Extra credentials | None | None | Tunnel token / API token | Gateway credentials |
| Operational cost | One public port pair and the host firewall | One more managed hop | Provider account and configuration | New product to operate |
| Fits one ARM host | Yes | Optional | No need | No need |

## Decision

**Option (a).** Caddy is the only HTTP ingress. The deployment host exposes 80/443 (or the
configured `CADDY_HTTP_PORT`/`CADDY_HTTPS_PORT`) and Caddy terminates TLS: `tls internal`
in local compose, a public ACME certificate in production (J4.7). There is no Cloudflare
Tunnel, no CDN, and no HTTP reverse proxy or API gateway in front of Caddy.

A future L4/TCP load balancer is compatible with this decision because it does not parse
or buffer HTTP bodies. It is not implemented or required for S2; if one is added, the
measurement below must be repeated and the record updated. An HTTP-layer ingress requires
a new decision, not a configuration change.

## Edge envelope

The contract limit is the one published by `GET /ingestion/capabilities` and enforced by
the API. The Caddy values are the outer envelope; they are not a second source of the
contract.

| Scope | Setting | Value | Reason |
|---|---|---|---|
| Upload `PUT /audios/*/content` | `request_body { max_size 220MB }` | 220,000,000 bytes | Strictly above the provisional API limit (200 MiB = 209,715,200 bytes) so the API returns its structured `payload_too_large` envelope. Bodies above the ceiling are stopped at the edge. |
| JSON API routes | `request_body { max_size 10MB }` | 10,000,000 bytes | Abuse guard for JSON endpoints; not a contract limit. |
| All HTTPS requests | `servers.timeouts.read_body` | 20m | Covers the provisional 15-minute upload window with margin at a realistic slow link. |
| All HTTPS requests | `servers.timeouts.read_header` | 30s | Header read bound; below Caddy's 1m default. |
| All HTTPS requests | `servers.timeouts.write` | 0 (no total deadline) | `/audios/*/progress` is SSE with `flush_interval -1`; a finite total write deadline would cut long classes. |
| All HTTPS requests | `servers.timeouts.idle` | 5m | Keep-alive idle bound; Caddy's default. |

The 200 MiB/3 h target remains **unapproved** until D4 closes; `capabilities` keeps
publishing `null` while `limits_validated=false`. Caddy does not make that target valid.
When D4 approves a lower or larger limit, update the published capability and re-check the
220 MB ceiling in the same revision: it must stay above the published upload limit.

## Measurement

`scripts/load/ingress_limits.sh` measures the envelope with synthetic bytes generated in
RAM:

1. a 1 MiB upload and an upload of exactly `request_body.max_size` reach the sink and are
   received byte for byte;
2. one byte over the ceiling returns `413` and the sink receives nothing;
3. one byte over the `@api` 10 MB ceiling returns `413`;
4. a copy of the Caddyfile with `read_body 2s` cuts a 5-second upload and the sink receives
   nothing;
5. the J1.3 checks repeat: no new proxy files, no deleted file descriptors (spool) and no
   request bodies in the access log; the native mode also prints Caddy's peak RSS to show
   the body is not held whole in memory.

The J1.3 `proxy_no_buffering.sh` default size drops from 512 MiB to 200 MiB (below the
220 MB ceiling) so the stream still reaches the sink; a larger `SIZE_BYTES` is now cut at the
edge with `413`.

The record with date, environment and observed status codes is in
[`docs/spike/J2.10-ingress-limits.md`](../spike/J2.10-ingress-limits.md). The static
regression check `scripts/ci/check_caddy_limits.py` runs in CI.

## Consequences

- A request whose declared or streamed body exceeds the published limit but is below
  220 MB gets the API's `413 payload_too_large` envelope with `max_bytes`. Above 220 MB it
  gets Caddy's plain `413`; the edge protects the API before the application can answer.
  This is the intended trade-off: the edge ceiling is an abuse guard, not the contract.
- No Cloudflare bot protection or L7 DDoS mitigation exists. Do not claim it. Edge
  hardening that needs an HTTP intermediary is a new decision; application and provider
  rate limits stay as they are.
- Caddy stays on the API path for every upload. The extra hop of ADR-0008 (`api` →
  `ingest`) is unchanged; the A2.13 D-5 bounded risk (httpx sends the body before reading
  an early rejection) is not made worse by an ingress that buffers, because there is none.
- `/internal/*` remains a Caddy `404`; `ingest` is never exposed from the edge
  (ADR-0008).
- A chunked or resumable upload protocol is a contract change: it goes through
  ESPECIFICACION/PLAN_IMPLEMENTACION first, as a new ticket.

## Verification (C-S2-JF-13)

- [x] Ingress decided and recorded in this ADR; Cloudflare assumptions removed from the
  proposal and the S4/DB design deployment rows.
- [x] Caddy sets upload and JSON body limits and server read/write/idle timeouts in
  `infra/Caddyfile`.
- [x] Body limit, timeouts and no buffering measured with synthetic bytes; date,
  environment and status codes in `docs/spike/J2.10-ingress-limits.md`.
- [x] Static assertions run without Docker (`python3 scripts/ci/check_caddy_limits.py`).
- [ ] D4 has not approved 200 MiB/3 h; `capabilities` keeps `null`. This ADR does not
  approve it.
