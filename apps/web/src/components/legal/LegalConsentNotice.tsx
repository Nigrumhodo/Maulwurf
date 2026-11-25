import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

const linkClass = "text-mint underline hover:text-lavender";

export interface LegalConsentNoticeProps {
  className?: string;
}

/** Informative line for login/register (owner decision: a notice, not a checkbox). */
export function LegalConsentNotice({ className = "" }: LegalConsentNoticeProps) {
  const { t } = useTranslation("legal");
  return (
    <p className={`text-base text-lavender/80 ${className}`.trim()}>
      {t("consent.before")}
      <Link to="/terminos" className={linkClass}>
        {t("consent.terms")}
      </Link>
      {t("consent.middle")}
      <Link to="/privacidad" className={linkClass}>
        {t("consent.privacy")}
      </Link>
      {t("consent.after")}
    </p>
  );
}
