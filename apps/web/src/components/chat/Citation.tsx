import { Link } from "react-router-dom";
import type { ChatCitation } from "@/lib/chat";
import { safeCitationHref } from "@/lib/markdown";

const linkClass =
  "inline border-b-2 border-mint/60 px-0.5 text-mint hover:border-mint hover:bg-mint hover:text-on-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mint";

export interface CitationLinkProps {
  citation: ChatCitation;
}

export function CitationLink({ citation }: CitationLinkProps) {
  const text = `[${citation.index}] ${citation.label}`;
  const href = safeCitationHref(citation.url);
  if (href === null) {
    return <span className="text-lavender/60">{text}</span>;
  }
  return (
    <Link
      to={href}
      aria-label={`abrir cita ${citation.index} en el lector`}
      className={linkClass}
    >
      {text}
    </Link>
  );
}

export interface CitationListProps {
  citations: ChatCitation[];
}

export function CitationList({ citations }: CitationListProps) {
  if (citations.length === 0) return null;
  return (
    <ul aria-label="fuentes de la respuesta" className="flex flex-col gap-1 text-xs text-lavender/60">
      {citations.map((citation) => (
        <li key={citation.index}>
          <CitationLink citation={citation} />
        </li>
      ))}
    </ul>
  );
}
