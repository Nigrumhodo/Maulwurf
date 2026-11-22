# Security policy

Maulwurf keeps class audio ephemeral and treats transcripts, OAuth material, and provider
credentials as private. This document is how to report a vulnerability. It is not a
procedure for testing against a system you do not operate.

## Reporting

Use a private GitHub security advisory on
[NigrumH0do/Maulwurf](https://github.com/NigrumH0do/Maulwurf) (Security → Advisories →
Report a vulnerability). Do not open a public issue, pull request, or discussion for a
suspected vulnerability.

Include the affected version or commit, the component (`apps/api`, `apps/ingest`,
`apps/web`, `infra`, or CI), and what an operator would observe. Do not include live
secrets, access tokens, class recordings, transcripts, prompts, or data from a real
student. A synthetic description is enough to reproduce the defect.

## What happens next

We acknowledge the report, confirm whether it is in scope, and coordinate a fix before
any public write-up. Please give us time to patch and release before disclosing details.
We do not run a paid bounty program.

Out of scope: findings that require a secret already committed to the repository (rotate
that secret and tell us in the advisory), volumetric denial of service without a
configuration defect, and issues in an unmodified upstream image that upstream has not
yet fixed. Dependency and image updates are tracked by Dependabot.

## Secrets

Never commit `.env`, `.env.prod`, or files under `infra/deploy/secrets/`. Those paths
are gitignored. Production templates that are safe to commit are `.env.example` and
`infra/deploy/.env.prod.example`.
