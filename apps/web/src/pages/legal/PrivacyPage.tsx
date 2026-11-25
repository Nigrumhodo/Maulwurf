import { useTranslation } from "react-i18next";
import { LegalLayout } from "@/components/legal/LegalLayout";
import { LegalSection, useSectionIndex } from "@/components/legal/LegalSection";
import { UiLanguageSwitcher } from "@/components/ui/UiLanguageSwitcher";

const SECTION_IDS = [
  "controller",
  "data",
  "purposes",
  "basis",
  "recipients",
  "transfers",
  "audio",
  "retention",
  "security",
  "rights",
  "storage",
  "minors",
  "changes",
] as const;

export function PrivacyPage() {
  const { t } = useTranslation("legal");
  const sections = useSectionIndex("privacy", SECTION_IDS);
  const intro = t("privacy.intro", { returnObjects: true }) as unknown as string[];
  return (
    <LegalLayout
      title={t("privacy.title")}
      sections={sections}
      languageSwitcher={<UiLanguageSwitcher labelClassName="hidden sm:inline" />}
    >
      <div className="flex max-w-prose flex-col gap-3 text-lg leading-relaxed text-lavender">
        {intro.map((paragraph, i) => (
          <p key={i}>{paragraph}</p>
        ))}
      </div>
      {SECTION_IDS.map((id) => (
        <LegalSection key={id} doc="privacy" id={id} />
      ))}
    </LegalLayout>
  );
}
