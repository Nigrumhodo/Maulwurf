import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

const linkBase = "lowercase inline-flex min-h-8 min-w-8 items-center hover:text-mint";

export interface LegalLinksProps {
  className?: string;
  /** Narrow columns (app sidebar): smaller meta text and the short license label. */
  compact?: boolean;
}

/** Inline footer links: privacy · terms · AGPL-3.0 license. */
export function LegalLinks({ className = "", compact = false }: LegalLinksProps) {
  const { t } = useTranslation("legal");
  const linkClass = `${linkBase} ${compact ? "text-sm text-lavender/60" : "text-base text-lavender/80"}`;
  // Compact drops the `·` separators: in a narrow column a wrapped line would leave one dangling.
  const gap = compact ? "gap-x-3" : "gap-x-2";
  const separator = compact ? null : (
    <span aria-hidden="true" className="text-lavender/80">
      ·
    </span>
  );
  return (
    <nav
      aria-label={t("links.label")}
      className={`flex flex-wrap items-center ${gap} gap-y-1 ${className}`.trim()}
    >
      <Link to="/privacidad" className={linkClass}>
        {t("links.privacy")}
      </Link>
      {separator}
      <Link to="/terminos" className={linkClass}>
        {t("links.terms")}
      </Link>
      {separator}
      <Link to="/terminos#licencia" className={linkClass}>
        {compact ? t("links.licenseShort") : t("links.license")}
      </Link>
    </nav>
  );
}
