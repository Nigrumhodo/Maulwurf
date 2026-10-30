// D1.5 — Allowlist de idiomas de audio (S1.A3: `es`, `en`, `fr`). El select de
// idioma NO preselecciona nada; estos son los únicos códigos aceptados.
export const LANGUAGES = [
  { code: "es", label: "español" },
  { code: "en", label: "english" },
  { code: "fr", label: "français" },
] as const;

export type LanguageCode = (typeof LANGUAGES)[number]["code"];
