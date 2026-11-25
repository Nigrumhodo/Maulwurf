import { useTranslation } from "react-i18next";
import { LegalLayout } from "@/components/legal/LegalLayout";
import { LegalSection, useSectionIndex } from "@/components/legal/LegalSection";
import { AGPL_URL, LEGAL } from "@/legal/controller";
import { useLegalValues } from "@/legal/useLegalValues";

// The license section keeps the stable `licencia` anchor used by `LegalLinks`.
const SECTION_IDS = [
  "service",
  "account",
  "use",
  "content",
  "ai",
  "actions",
  "availability",
  "licencia",
  "termination",
  "law",
  "changes",
  "contact",
] as const;

const linkClass = "text-mint underline hover:text-lavender break-all";

export function TermsPage() {
  const { t } = useTranslation("legal");
  const values = useLegalValues();
  const sections = useSectionIndex("terms", SECTION_IDS);
  const intro = t("terms.intro", { returnObjects: true }) as unknown as string[];
  return (
    <LegalLayout title={t("terms.title")} sections={sections}>
      <div className="flex max-w-prose flex-col gap-3 text-lg leading-relaxed text-lavender">
        {intro.map((paragraph, i) => (
          <p key={i}>{paragraph}</p>
        ))}
      </div>
      {SECTION_IDS.map((id) => (
        <LegalSection key={id} doc="terms" id={id}>
          {id === "licencia" ? (
            <ul className="flex flex-col gap-2">
              <li>
                {t("terms.licenseLinkLabel")}:{" "}
                <a href={AGPL_URL} className={linkClass} target="_blank" rel="noreferrer">
                  {AGPL_URL}
                </a>
              </li>
              <li>
                {t("terms.sourceLabel")}:{" "}
                {LEGAL.sourceUrl ? (
                  <a
                    href={LEGAL.sourceUrl}
                    className={linkClass}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {LEGAL.sourceUrl}
                  </a>
                ) : (
                  values.sourceUrl
                )}
              </li>
            </ul>
          ) : null}
        </LegalSection>
      ))}
    </LegalLayout>
  );
}
