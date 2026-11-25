// @vitest-environment jsdom
//
// W3 — the study views (upload, library, transcript reader, chat) follow the UI language;
// Spanish stays the default and the privacy notice links to the public policy.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ChatPage } from "@/pages/ChatPage";
import { BibliotecaPage } from "@/pages/BibliotecaPage";
import { ClassStatus } from "@/components/transcript/ClassStatus";
import { UploadForm } from "@/components/upload/UploadForm";
import { applyLocale } from "@/i18n";
import { libraryActionError } from "@/lib/library";
import { ApiHttpError } from "@/lib/api";
import { validateUpload } from "@/lib/upload";
import type { UploadDraft } from "@/lib/upload";
import type { IngestionCapabilities } from "@/lib/capabilities";
import type { LibraryAudioDetail } from "@/lib/library";

const CAPABILITIES: IngestionCapabilities = {
  source_report: "docs/spike/F0.1-informe-riva.md",
  source_version: "approved-2026-10-05",
  limits_validated: true,
  privacy_notice_version: "2026-09-v1",
  max_upload_bytes: 960044,
  max_duration_seconds: 30,
  accepted_input_formats: ["wav"],
  languages: ["es", "en"],
  language_policy: "explicit_select_required_no_multi",
  ttl: { upload_start_minutes: 10, receive_minutes: 30, asr_minutes: 60 },
  ttl_source_report: "docs/eval/ttl-benchmark.md",
  ttl_source_version: "2026-11-01",
  asr: { provider: "nvidia-riva", model: "whisper-large-v3", timestamp_precision: null },
  active_slots: 1,
  required_stages: ["index"],
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/subjects") {
        return json({
          items: [{ id: "s1", name: "calculo", color: "#000000", teacher: null, class_count: 0 }],
        });
      }
      if (url === "/ingestion/capabilities") return json(CAPABILITIES);
      if (url.startsWith("/audios")) return json({ items: [] });
      if (url.startsWith("/chat/conversations")) return json({ items: [], next_cursor: null });
      return json({});
    }),
  );
});

afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  await applyLocale("es");
});

function renderInRouter(node: React.ReactNode) {
  return render(<MemoryRouter>{node}</MemoryRouter>);
}

describe("study i18n — upload", () => {
  it("renders the privacy notice in English and links to /privacidad", async () => {
    await applyLocale("en");
    renderInRouter(<UploadForm />);
    const link = await screen.findByRole("link", { name: "privacy notice" });
    expect(screen.getByText("> privacy")).toBeTruthy();
    expect(link.getAttribute("href")).toBe("/privacidad");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(screen.getByText(/consent of the other people recorded/)).toBeTruthy();
  });

  it("links the Spanish privacy notice to /privacidad and keeps its version text", async () => {
    renderInRouter(<UploadForm />);
    const link = await screen.findByRole("link", { name: "aviso de privacidad" });
    expect(link.getAttribute("href")).toBe("/privacidad");
    expect(link.parentElement?.textContent).toBe("aviso de privacidad · versión 2026-09-v1");
  });

  it("resolves client-side validation messages at call time", async () => {
    const draft: UploadDraft = {
      subjectId: "",
      classDate: "",
      classTimezone: "",
      languageCode: "",
      title: "",
      teacher: "",
      cloudAccepted: false,
      voiceAcknowledged: false,
    };
    expect(validateUpload(draft, CAPABILITIES).fields.subjectId).toBe("elige una materia");
    await applyLocale("fr");
    expect(validateUpload(draft, CAPABILITIES).fields.subjectId).toBe("choisis une matière");
  });
});

describe("study i18n — library", () => {
  it("renders the empty library in English", async () => {
    await applyLocale("en");
    renderInRouter(<BibliotecaPage />);
    expect(await screen.findByRole("heading", { name: "library", level: 1 })).toBeTruthy();
    await waitFor(() => expect(screen.getByText("no classes yet")).toBeTruthy());
  });

  it("translates library action errors but keeps the server message verbatim", async () => {
    await applyLocale("en");
    const error = new ApiHttpError(404, {
      code: "not_found",
      message: "no encontrado",
      details: {},
    } as never);
    expect(libraryActionError(error).message).toBe("the class no longer exists; refresh the library.");
    const busy = new ApiHttpError(503, { code: "x", message: "servidor ocupado", details: {} } as never);
    expect(libraryActionError(busy).message).toBe("servidor ocupado Try again in a moment.");
  });
});

describe("study i18n — transcript", () => {
  it("renders the class status actions in English", async () => {
    await applyLocale("en");
    const detail = {
      id: "a1",
      subject: { id: "sub1", name: "materia", color: "#112233" },
      title: "clase",
      class_date: "2026-11-04",
      class_timezone: "America/Bogota",
      language_code: "es",
      duration_seconds: 20,
      created_at: "2026-11-04T12:00:00Z",
      attempt: null,
      stages: { index: "succeeded", analyze: "not_requested" },
      aggregate_status: "ready",
      audio_retained: false,
      analysis_backfill_available: true,
      transcript: null,
      exports: { txt: false, srt: false },
    } as LibraryAudioDetail;
    renderInRouter(
      <ClassStatus detail={detail} busy={false} onBackfill={vi.fn()} onRetryIndex={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "analyze this class" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "class status" })).toBeTruthy();
  });
});

describe("study i18n — chat", () => {
  it("renders the chat page in French", async () => {
    await applyLocale("fr");
    renderInRouter(<ChatPage />);
    expect(screen.getByRole("heading", { name: "chat", level: 1 })).toBeTruthy();
    expect(screen.getByText(/pose une question sur tous tes cours/)).toBeTruthy();
    expect(await screen.findByLabelText("question")).toBeTruthy();
  });

  it("renders the composer in English", async () => {
    await applyLocale("en");
    renderInRouter(<ChatPage />);
    expect(await screen.findByPlaceholderText("ask about your classes…")).toBeTruthy();
  });
});
