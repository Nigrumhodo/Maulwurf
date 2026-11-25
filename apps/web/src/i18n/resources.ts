import enAuth from "@/locales/en/auth.json";
import enCommon from "@/locales/en/common.json";
import enLanding from "@/locales/en/landing.json";
import enLegal from "@/locales/en/legal.json";
import enPlan from "@/locales/en/plan.json";
import enSettings from "@/locales/en/settings.json";
import enStudy from "@/locales/en/study.json";
import esAuth from "@/locales/es/auth.json";
import esCommon from "@/locales/es/common.json";
import esLanding from "@/locales/es/landing.json";
import esLegal from "@/locales/es/legal.json";
import esPlan from "@/locales/es/plan.json";
import esSettings from "@/locales/es/settings.json";
import esStudy from "@/locales/es/study.json";
import frAuth from "@/locales/fr/auth.json";
import frCommon from "@/locales/fr/common.json";
import frLanding from "@/locales/fr/landing.json";
import frLegal from "@/locales/fr/legal.json";
import frPlan from "@/locales/fr/plan.json";
import frSettings from "@/locales/fr/settings.json";
import frStudy from "@/locales/fr/study.json";

// Bundled on purpose: no runtime fetch, so the PWA shell and the unit suites never depend
// on the network to render copy. Spanish is the source language; `en`/`fr` must keep the
// same keys (enforced by tests/unit/i18n-parity.test.ts).
export const NAMESPACES = [
  "common",
  "landing",
  "auth",
  "legal",
  "study",
  "plan",
  "settings",
] as const;

export type Namespace = (typeof NAMESPACES)[number];

export const DEFAULT_NAMESPACE: Namespace = "common";

const es = {
  common: esCommon,
  landing: esLanding,
  auth: esAuth,
  legal: esLegal,
  study: esStudy,
  plan: esPlan,
  settings: esSettings,
};

export const resources = {
  es,
  en: {
    common: enCommon,
    landing: enLanding,
    auth: enAuth,
    legal: enLegal,
    study: enStudy,
    plan: enPlan,
    settings: enSettings,
  },
  fr: {
    common: frCommon,
    landing: frLanding,
    auth: frAuth,
    legal: frLegal,
    study: frStudy,
    plan: frPlan,
    settings: frSettings,
  },
} as const;

/** Spanish resources drive the typed keys of `t()` (see `i18next.d.ts`). */
export type SourceResources = typeof es;
