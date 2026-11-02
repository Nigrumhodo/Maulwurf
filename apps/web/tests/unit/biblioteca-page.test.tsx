// @vitest-environment jsdom
//
// D2.4 — E-S2-DA-06 (soporte de componente): listado real, agrupación por materia,
// `partial` con la etapa fallida, respectivamente «volver a subir» sin texto y
// «reintentar análisis» con texto; nunca se mezclan. El lector sigue deshabilitado (D2.5).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BibliotecaPage } from "@/pages/BibliotecaPage";
import { setCsrfToken, clearCsrfToken } from "@/lib/csrf";
import type { IngestionCapabilities } from "@/lib/capabilities";
import type { LibraryAudio, LibraryAudioDetail } from "@/lib/library";

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
  asr: { provider: "nvidia-riva", model: "whisper-large-v3", timestamp_precision: null },
  active_slots: 1,
  required_stages: ["index"],
};

function attempt(status: string, cleanup = "verified") {
  return {
    attempt_id: `at-${status}`,
    status,
    received_bytes: 1024,
    fragments_done: 2,
    fragments_total: 2,
    cleanup_status: cleanup,
  };
}

const ITEMS: LibraryAudio[] = [
  {
    id: "a1",
    subject: { id: "s1", name: "calculo", color: "#111111" },
    title: "clase lista",
    class_date: "2026-10-29",
    class_timezone: "America/Bogota",
    language_code: "es",
    duration_seconds: 1800,
    created_at: "2026-10-29T12:00:00Z",
    attempt: attempt("succeeded"),
    stages: { index: "succeeded", analyze: "not_requested" },
    aggregate_status: "ready",
  },
  {
    id: "a2",
    subject: { id: "s1", name: "calculo", color: "#111111" },
    title: "clase parcial",
    class_date: "2026-10-28",
    class_timezone: "America/Bogota",
    language_code: "es",
    duration_seconds: 1800,
    created_at: "2026-10-28T12:00:00Z",
    attempt: attempt("succeeded"),
    stages: { index: "succeeded", analyze: "failed" },
    aggregate_status: "partial",
  },
  {
    id: "a3",
    subject: { id: "s2", name: "fisica", color: "#222222" },
    title: "clase fallida",
    class_date: "2026-10-27",
    class_timezone: "America/Bogota",
    language_code: "es",
    duration_seconds: null,
    created_at: "2026-10-27T12:00:00Z",
    attempt: attempt("requires_reupload"),
    stages: { index: "failed", analyze: "not_requested" },
    aggregate_status: "failed",
  },
  {
    id: "a4",
    subject: { id: "s2", name: "fisica", color: "#222222" },
    title: "clase limpiando",
    class_date: "2026-10-26",
    class_timezone: "America/Bogota",
    language_code: "es",
    duration_seconds: 1800,
    created_at: "2026-10-26T12:00:00Z",
    attempt: attempt("transcript_committed_cleanup_pending", "pending"),
    stages: { index: "not_requested", analyze: "not_requested" },
    aggregate_status: "processing",
  },
];

const DETAIL_A2: LibraryAudioDetail = {
  ...ITEMS[1],
  audio_retained: false,
  transcript: { version: 7, timestamp_precision: "segment", char_count: 1200 },
  exports: { txt: true, srt: true },
};

interface Call {
  url: string;
  method: string;
  body: unknown;
}

let calls: Call[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  calls = [];
  setCsrfToken("csrf");
  class IdleEventSource {
    addEventListener(): void {}
    close(): void {}
  }
  vi.stubGlobal("EventSource", IdleEventSource);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ url, method, body });

      if (method === "GET" && url.startsWith("/audios?limit=100")) {
        return json({ items: ITEMS });
      }
      if (method === "GET" && url === "/ingestion/capabilities") return json(CAPABILITIES);
      if (method === "GET" && url === "/audios/a2") return json(DETAIL_A2);
      if (method === "POST" && url === "/audios/a2/retry") {
        return json(
          {
            audio_id: "a2",
            transcript_version: 7,
            runs: [{ stage: "analyze", run_id: "r1", status: "pending" }],
          },
          202,
        );
      }
      if (method === "POST" && url === "/audios/a3/reupload") {
        return json(
          {
            audio_id: "a3",
            attempt_id: "at3",
            upload_url: "/audios/a3/content?attempt_id=at3",
            upload_expires_at: "2026-10-30T00:00:00Z",
            outcome: "reupload",
          },
          201,
        );
      }
      if (method === "POST" && url === "/audios/a3/attempts/at3/cancel") {
        return json({ attempt_id: "at3", status: "cancelled" }, 202);
      }
      if (method === "PUT" && url === "/audios/a3/content?attempt_id=at3") {
        const reader = (init?.body as ReadableStream<Uint8Array>).getReader();
        let received = 0;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          received += value.byteLength;
        }
        return json({ attempt_id: "at3", status: "received", received_bytes: received }, 202);
      }
      return json({});
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearCsrfToken();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <BibliotecaPage />
    </MemoryRouter>,
  );
}

describe("BibliotecaPage", () => {
  it("lista por materia con estados, partial explícito y aviso de audio no conservado", async () => {
    renderPage();

    expect(await screen.findByText("calculo")).toBeTruthy();
    expect(screen.getByText("fisica")).toBeTruthy();
    expect(screen.getByText(/audio no conservado/i)).toBeTruthy();
    expect(screen.getByText(/parcial · falló análisis/i)).toBeTruthy();

    const readerButtons = screen.getAllByRole("button", { name: "abrir transcripción" });
    expect(readerButtons.length).toBeGreaterThan(0);
    expect(readerButtons.every((button) => (button as HTMLButtonElement).disabled)).toBe(true);

    expect(screen.getByRole("button", { name: "reintentar análisis" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "volver a subir" })).toBeTruthy();
    expect(screen.getByText(/limpieza pendiente/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "ver progreso" })).toBeTruthy();
  });

  it("reintenta solo la etapa fallida con la versión del detalle vigente", async () => {
    renderPage();
    await screen.findByRole("button", { name: "reintentar análisis" });
    fireEvent.click(screen.getByRole("button", { name: "reintentar análisis" }));

    await waitFor(() =>
      expect(calls.some((call) => call.url === "/audios/a2/retry")).toBe(true),
    );
    const retry = calls.find((call) => call.url === "/audios/a2/retry");
    expect(retry?.method).toBe("POST");
    expect(retry?.body).toEqual({ stages: ["analyze"], transcript_version: 7 });
    expect(await screen.findByText(/etapas encoladas/i)).toBeTruthy();
  });

  it("«volver a subir» abre una reserva y envía el archivo por la URL relativa", async () => {
    renderPage();
    await screen.findByRole("button", { name: "volver a subir" });
    fireEvent.click(screen.getByRole("button", { name: "volver a subir" }));

    const input = (await screen.findByLabelText(/archivo de audio/i)) as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3, 4])], "clase.wav", { type: "audio/wav" });
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "enviar audio" }));

    await waitFor(() =>
      expect(calls.some((call) => call.url === "/audios/a3/content?attempt_id=at3")).toBe(true),
    );
    const upload = calls.find((call) => call.url === "/audios/a3/content?attempt_id=at3");
    expect(upload?.method).toBe("PUT");
    expect(await screen.findByText(/audio recibido/i)).toBeTruthy();
  });

  it("cancelar la reserva de reupload cancela el intento y libera el panel", async () => {
    renderPage();
    await screen.findByRole("button", { name: "volver a subir" });
    fireEvent.click(screen.getByRole("button", { name: "volver a subir" }));

    await screen.findByLabelText(/archivo de audio/i);
    fireEvent.click(screen.getByRole("button", { name: "descartar reserva" }));

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url === "/audios/a3/attempts/at3/cancel" && call.method === "POST",
        ),
      ).toBe(true),
    );
    expect(screen.queryByLabelText(/archivo de audio/i)).toBeNull();
  });
});
