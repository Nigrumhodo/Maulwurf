// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ProposalCard } from "@/components/chat/ProposalCard";
import type { ChatProposal } from "@/lib/chat";

const PROPOSAL: ChatProposal = {
  task_id: "t1",
  source: "chat",
  status: "needs_review",
  calendar_sync_status: "not_requested",
  evidence: "general_suggestion",
};

afterEach(cleanup);

describe("ProposalCard", () => {
  it("shows an inbox proposal and never a Calendar write", () => {
    render(
      <MemoryRouter>
        <ProposalCard proposal={PROPOSAL} />
      </MemoryRouter>,
    );

    expect(screen.getByText(/propuesta creada en la bandeja/)).toBeTruthy();
    expect(screen.getByText(/needs_review/)).toBeTruthy();
    expect(screen.getByText(/no se escribió en Calendar/)).toBeTruthy();
    const link = screen.getByRole("link", { name: "ver en tareas" });
    expect(link.getAttribute("href")).toBe("/gestion/tareas");
    expect(screen.queryByRole("button", { name: /confirmar/i })).toBeNull();
  });

  it("marks a corpus-grounded proposal without inventing a Calendar action", () => {
    render(
      <MemoryRouter>
        <ProposalCard proposal={{ ...PROPOSAL, evidence: "corpus" }} />
      </MemoryRouter>,
    );

    expect(screen.getByText(/con cita de la clase/)).toBeTruthy();
    expect(screen.queryByText(/escrito en Calendar/)).toBeNull();
  });
});
