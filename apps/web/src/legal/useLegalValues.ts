import { useTranslation } from "react-i18next";
import { LEGAL, LEGAL_FIELDS } from "./controller";
import type { LegalField } from "./controller";

/** Interpolation values for the legal copy: real values or translated `[completar: ...]`. */
export function useLegalValues(): Record<LegalField, string> {
  const { t } = useTranslation("legal");
  const out = {} as Record<LegalField, string>;
  for (const field of LEGAL_FIELDS) {
    const value = LEGAL[field];
    out[field] =
      value ?? t("common.placeholder", { field: t(`common.fields.${field}`) });
  }
  return out;
}
