import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type { ChatProposal } from "@/lib/chat";

// D3.8 - `propose_task` only creates an inbox proposal. This card never says the task was
// written to Calendar and has no confirmation button: the human review lives in Tareas.

export interface ProposalCardProps {
  proposal: ChatProposal;
}

export function ProposalCard({ proposal }: ProposalCardProps) {
  const { t } = useTranslation("study");
  return (
    <div className="lowercase flex flex-col gap-1 border-2 border-mint px-3 py-2 text-mint">
      <p className="font-bold">{t("proposal.created")}</p>
      <p className="text-base">
        {t("proposal.state", {
          status: proposal.status,
          evidence:
            proposal.evidence === "corpus"
              ? t("proposal.evidenceCorpus")
              : t("proposal.evidenceGeneral"),
        })}
      </p>
      <p className="text-base">
        {t("proposal.notWritten")}
      </p>
      <Link
        to="/gestion/tareas"
        className="inline-flex w-fit items-center gap-1 border-2 border-lavender/40 px-2 py-0.5 underline decoration-dotted hover:border-mint hover:bg-mint hover:text-on-accent"
      >
        {t("proposal.viewTasks")}
      </Link>
    </div>
  );
}
