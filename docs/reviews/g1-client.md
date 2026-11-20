# Client G1 Privacy Review (`D4.8`)

- **Ticket:** `D4.8` (`C-S4-DA-05`)
- **Review date:** `2026-11-19`
- **Primary evidence checklist:** `docs/sprints/artifacts/D4.8/g1-client-checklist.md`
- **Automated test suites:** `apps/web/tests/unit/g1-privacy.test.ts`, `apps/web/tests/unit/pwa-sw.test.ts`, `apps/web/tests/unit/stream.test.ts`

## Summary of Client-Side Invariants

1. **Zero persistent audio & zero browser playback**
   - `apps/web/tests/unit/g1-privacy.test.ts` statically inspects all `.ts`/`.tsx` files under `apps/web/src` (comments stripped) and fails if any `<audio>` element appears.
   - Selected audio `File` handles in `apps/web/src/components/upload/AudioUploadPanel.tsx` are streamed directly via PUT (`apps/web/src/lib/upload.ts`) and explicitly released when upload finishes, aborts, or cancels.
   - Transcript timestamps in `apps/web/src/components/TranscriptReader.tsx` navigate text spans/segments only; there is no media player or post-cleanup audio re-fetch.

2. **Transient Blob URLs strictly limited to explicit plain-text exports**
   - `apps/web/tests/unit/g1-privacy.test.ts` enforces that `URL.createObjectURL` may only exist in `apps/web/src/lib/reader.ts` (used to download user-initiated `.txt`/`.srt` exports) and must pair with `URL.revokeObjectURL`.

3. **Zero private data in browser storage**
   - `apps/web/tests/unit/g1-privacy.test.ts` forbids `indexedDB` and `sessionStorage` across `apps/web/src`, and restricts `localStorage` solely to theme preference in `apps/web/src/lib/theme.ts`.
   - CSRF tokens live in memory only inside `apps/web/src/lib/csrf.ts`.
   - `apps/web/src/lib/api.ts` sets `cache: "no-store"` with same-origin credentials on JSON API and bootstrap requests.
   - `apps/web/src/components/subjects/SubjectStats.tsx` renders metadata and transcript text counters only (`no audio` invariant).

4. **Strict PWA Service Worker exclusion boundary (`apps/web/public/sw.js`)**
   - Only caches the static app shell (`/`, `/manifest.webmanifest`, `/icon.svg`).
   - Immediately passes through without intercepting or caching:
     - Non-`GET` requests
     - Cross-origin requests
     - `Accept: text/event-stream` SSE connections
     - Any response containing `Cache-Control: no-store`
     - All private or operational path prefixes (`/auth`, `/me`, `/audios`, `/subjects`, `/tasks`, `/calendar`, `/chat`, `/search`, `/notifications`, `/integrations`, `/ingestion`, `/internal`, `/metrics`, `/healthz`, `/readyz`).
   - Verified in `apps/web/tests/unit/pwa-sw.test.ts` and `apps/web/tests/unit/g1-privacy.test.ts`.

5. **Public landing boundary (`/`)**
   - `apps/web/src/pages/LandingPage.tsx` and `apps/web/src/App.tsx` render the unauthenticated product landing without triggering private API requests or preloading user state; verified in `apps/web/tests/unit/landing-page.test.tsx`.

## Scope & Remaining Verification

- **Verified now:** Static source invariant checks, unit/component suite under Vitest/jsdom, production bundle build (`apps/web`), and checklist capture in `docs/sprints/artifacts/D4.8/g1-client-checklist.md`.
- **Remaining manual/host check:** Live browser DevTools Storage / Cache Storage / Network inspection during the two `D4.7` reference-host demo rehearsals.
