// Values that legal/ownership has not defined yet. `null` renders as a visible
// `[completar: ...]` placeholder (see `useLegalValues`); never invent a value here.
export type LegalValue = string | null;

export const LEGAL = {
  // TODO(legal): the project owner/legal counsel must set the controller's legal name.
  controllerName: null as LegalValue,
  // TODO(legal): the project owner/legal counsel must set the controller's postal address.
  address: null as LegalValue,
  // TODO(legal): the project owner must set a monitored privacy contact e-mail.
  email: null as LegalValue,
  // TODO(legal): legal counsel must say whether a DPO is required and name them (or "none").
  dpo: null as LegalValue,
  // TODO(legal): legal counsel must name the competent supervisory authority.
  authority: null as LegalValue,
  // TODO(legal): legal counsel must set the governing law and jurisdiction.
  law: null as LegalValue,
  // TODO(legal): legal counsel must set the minimum age (and any parental-consent rule).
  minAge: null as LegalValue,
  // TODO(legal): the maintainers must confirm this is the public source of deployed versions.
  sourceUrl: "https://github.com/Nigrumhodo/Maulwurf" as LegalValue,
  // TODO(legal): update on every text change that legal review approves.
  lastUpdated: "2026-11-24" as LegalValue,
  // TODO(legal): legal counsel must replace this draft tag with the approved policy version.
  version: "borrador-2026-11-24" as LegalValue,
} as const;

export type LegalField = keyof typeof LEGAL;

export const LEGAL_FIELDS = Object.keys(LEGAL) as LegalField[];

export const AGPL_URL = "https://www.gnu.org/licenses/agpl-3.0.html";
