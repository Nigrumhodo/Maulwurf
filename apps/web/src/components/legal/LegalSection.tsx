import { useTranslation } from "react-i18next";
import { useLegalValues } from "@/legal/useLegalValues";

interface SectionCopy {
  title: string;
  p?: string[];
  items?: { name: string; text: string }[];
}

export type LegalDocument = "privacy" | "terms";

// Reads `<doc>.sections.<id>` from the legal namespace; `returnObjects` keeps the copy in
// the JSON files only. Interpolation uses the resolved legal values (or placeholders).
export function useSectionCopy(doc: LegalDocument, id: string): SectionCopy {
  const { t } = useTranslation("legal");
  const values = useLegalValues();
  const loose = t as unknown as (key: string, options: object) => unknown;
  return loose(`${doc}.sections.${id}`, { returnObjects: true, ...values }) as SectionCopy;
}

export function useSectionIndex(doc: LegalDocument, ids: readonly string[]) {
  const { t } = useTranslation("legal");
  const loose = t as unknown as (key: string) => string;
  return ids.map((id) => ({ id, title: loose(`${doc}.sections.${id}.title`) }));
}

export function LegalSection({
  doc,
  id,
  children,
}: {
  doc: LegalDocument;
  id: string;
  children?: React.ReactNode;
}) {
  const copy = useSectionCopy(doc, id);
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-4">
      <h2 id={`${id}-title`} className="text-2xl text-mint">
        {copy.title}
      </h2>
      <div className="mt-3 flex max-w-prose flex-col gap-3 text-lg leading-relaxed text-lavender">
        {copy.p?.map((paragraph, i) => <p key={i}>{paragraph}</p>)}
        {copy.items ? (
          <ul className="flex list-disc flex-col gap-2 pl-6">
            {copy.items.map((item) => (
              <li key={item.name}>
                <strong className="font-normal text-mint">{item.name}</strong>: {item.text}
              </li>
            ))}
          </ul>
        ) : null}
        {children}
      </div>
    </section>
  );
}
