import { Link } from "react-router-dom";
import type { ChatProposal } from "@/lib/chat";

// D3.8 - `propose_task` only creates an inbox proposal. This card never says the task was
// written to Calendar and has no confirmation button: the human review lives in Tareas.

export interface ProposalCardProps {
  proposal: ChatProposal;
}

export function ProposalCard({ proposal }: ProposalCardProps) {
  return (
    <div className="lowercase flex flex-col gap-1 border-2 border-mint px-3 py-2 text-mint">
      <p className="font-bold">propuesta creada en la bandeja</p>
      <p className="text-base">
        estado {proposal.status} ·{" "}
        {proposal.evidence === "corpus" ? "con cita de la clase" : "sugerencia general"}
      </p>
      <p className="text-base">
        no se escribió en Calendar; revísala y confírmala en tareas.
      </p>
      <Link
        to="/gestion/tareas"
        className="inline-flex w-fit items-center gap-1 border-2 border-lavender/40 px-2 py-0.5 underline decoration-dotted hover:border-mint hover:bg-mint hover:text-on-accent"
      >
        ver en tareas
      </Link>
    </div>
  );
}
