# Legal pages (privacy policy, terms of use, AGPL notice)

## Status

Technical draft pending legal and security/privacy review (AGENTS.md guardrails 6 and 9). The
text describes what the application does today, written only from
`docs/ESPECIFICACION.md` and the code. It makes no certification or legal-compliance claim.
Every page shows a visible draft banner with the last-updated date.

## Where things live

| What | Path |
|---|---|
| Routes `/privacidad`, `/terminos` (public, outside `RequireAuth`) | `apps/web/src/App.tsx` |
| Pages | `apps/web/src/pages/legal/{PrivacyPage,TermsPage}.tsx` |
| Shell, sections, links, consent line | `apps/web/src/components/legal/` |
| Placeholder values | `apps/web/src/legal/controller.ts` |
| Copy (es source, en, fr) | `apps/web/src/locales/{es,en,fr}/legal.json` |
| Tests | `apps/web/tests/unit/legal-pages.test.tsx` |

`LegalLinks` and `LegalConsentNotice` are not mounted anywhere yet; footers and the
login/register pages adopt them in a later block. `LegalLayout` has a `languageSwitcher` slot.

## Fields to fill (`TODO(legal)` in `controller.ts`)

Unknown values render as `[completar: ...]` (translated) until they are set.

- `controllerName`: controller's legal name.
- `address`: controller's postal address.
- `email`: monitored privacy contact e-mail.
- `dpo`: data protection officer, or a statement that none is required.
- `authority`: competent supervisory authority.
- `law`: governing law and jurisdiction (also used for liability limits).
- `minAge`: minimum age and any parental-consent rule.
- `sourceUrl`: public source URL of deployed versions (default is the repository remote,
  to be confirmed; AGPL-3.0 §13).
- `lastUpdated` and `version`: update on every approved text change.

## Open items

- Align the server `privacy_notice_version` (`2026-09-v1`) with the published policy. This is
  an API contract change and must update `docs/ESPECIFICACION.md` and
  `docs/PLAN_IMPLEMENTACION.md` together. Not done here.
- Review the retention of data already sent to third parties (NVIDIA, LLM and embedding
  providers, Google). The pages say this review is open and do not promise zero retention.
- Legal basis per processing, international transfer safeguards and minors' rules are
  placeholders in the text.
