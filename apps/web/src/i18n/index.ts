import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { DEFAULT_NAMESPACE, NAMESPACES, resources } from "./resources";

// UI language only. The audio language of a class (`lib/languages.ts`, contract M2) is a
// separate, explicit per-upload choice and must never follow this setting.
export const UI_LOCALES = [
  { code: "es", label: "español" },
  { code: "en", label: "english" },
  { code: "fr", label: "français" },
] as const;

export type UiLocale = (typeof UI_LOCALES)[number]["code"];

export const DEFAULT_LOCALE: UiLocale = "es";

const STORAGE_KEY = "maulwurf-locale";

export function isUiLocale(value: unknown): value is UiLocale {
  return UI_LOCALES.some((locale) => locale.code === value);
}

/**
 * Stored choice or Spanish. The browser language is deliberately ignored: Spanish stays
 * the default until the user picks another language, which keeps first render and the
 * test environment (jsdom reports `en-US`) deterministic.
 */
export function getStoredLocale(): UiLocale {
  if (typeof window === "undefined") return DEFAULT_LOCALE;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isUiLocale(stored) ? stored : DEFAULT_LOCALE;
  } catch {
    return DEFAULT_LOCALE;
  }
}

function syncDocumentLang(locale: UiLocale): void {
  if (typeof document !== "undefined") {
    document.documentElement.lang = locale;
  }
}

/** Switches the UI language, persists it in localStorage and updates `<html lang>`. */
export async function applyLocale(locale: UiLocale): Promise<void> {
  await i18n.changeLanguage(locale);
  syncDocumentLang(locale);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(STORAGE_KEY, locale);
    } catch {
      // Private mode or a full quota: the language still applies for this page load.
    }
  }
}

/** BCP 47 tag for `Intl.*` formatters so dates and numbers follow the UI language. */
export function intlLocale(): UiLocale {
  return isUiLocale(i18n.resolvedLanguage) ? i18n.resolvedLanguage : DEFAULT_LOCALE;
}

const initialLocale = getStoredLocale();

void i18n.use(initReactI18next).init({
  resources,
  lng: initialLocale,
  fallbackLng: DEFAULT_LOCALE,
  supportedLngs: UI_LOCALES.map((locale) => locale.code),
  ns: [...NAMESPACES],
  defaultNS: DEFAULT_NAMESPACE,
  // Bundled resources: initialise synchronously so the first render already has copy.
  initAsync: false,
  // React already escapes text; double escaping would show `&amp;` in the UI.
  interpolation: { escapeValue: false },
  returnNull: false,
  // No vendor promo line in the browser console.
  showSupportNotice: false,
});

syncDocumentLang(initialLocale);

export default i18n;
