// @vitest-environment jsdom
//
// D2.2 — U-S2-DA-02: cancelar la subida aborta el `PUT`, llama a la API de cancelación y
// libera el archivo; un `202` muestra el resultado admitido con los bytes reales recibidos.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { UploadForm } from "@/components/upload/UploadForm";
import { clearCsrfToken, setCsrfToken } from "@/lib/csrf";
import type { IngestionCapabilities } from "@/lib/capabilities";

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

interface Call {
  url: string;
  method: string;
  signal?: AbortSignal | null;
}

let calls: Call[] = [];
let putSignal: AbortSignal | null | undefined;
let putMode: "pending" | "accept" = "pending";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  calls = [];
  putSignal = undefined;
  putMode = "pending";
  setCsrfToken("csrf");
  // El resultado admitido monta `IngestProgress` (D2.3); aquí solo importa que no abra red.
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
      calls.push({ url, method, signal: init?.signal });

      if (url === "/subjects" && method === "GET") {
        return json({
          items: [{ id: "s1", name: "calculo", color: "#000000", teacher: null, class_count: 0 }],
        });
      }
      if (url === "/ingestion/capabilities" && method === "GET") {
        return json(CAPABILITIES);
      }
      if (url === "/audios" && method === "POST") {
        return json(
          {
            audio_id: "a1",
            attempt_id: "at1",
            upload_url: "/audios/a1/content?attempt_id=at1",
            upload_expires_at: "2026-10-30T00:00:00Z",
            outcome: "new",
          },
          201,
        );
      }
      if (method === "PUT") {
        putSignal = init?.signal;
        if (putMode === "accept") {
          const reader = (init?.body as ReadableStream<Uint8Array>).getReader();
          let sent = 0;
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            sent += value.byteLength;
          }
          return json({ attempt_id: "at1", status: "received", received_bytes: sent }, 202);
        }
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        });
      }
      if (url === "/audios/a1/attempts/at1/cancel" && method === "POST") {
        return json({ attempt_id: "at1", status: "cancelled" }, 202);
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

async function reserveClass() {
  render(
    <MemoryRouter>
      <UploadForm />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByLabelText(/materia/i)).toBeTruthy());

  fireEvent.change(screen.getByLabelText(/materia/i), { target: { value: "s1" } });
  fireEvent.change(screen.getByLabelText(/fecha de clase/i), {
    target: { value: "2026-10-29" },
  });
  fireEvent.change(screen.getByLabelText(/zona horaria/i), {
    target: { value: "America/Bogota" },
  });
  fireEvent.change(screen.getByLabelText(/idioma del audio/i), { target: { value: "es" } });
  fireEvent.click(screen.getByLabelText(/tratamiento en la nube/i));
  fireEvent.click(screen.getByLabelText(/no habrá reproducción/i));
  fireEvent.click(screen.getByRole("button", { name: "reservar subida" }));

  await screen.findByText(/reserva creada/i);
}

function pickFile(name = "clase.wav") {
  const input = screen.getByLabelText(/archivo de audio/i) as HTMLInputElement;
  const file = new File([new Uint8Array([1, 2, 3, 4])], name, { type: "audio/wav" });
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

describe("UploadForm · cancelar subida (U-S2-DA-02)", () => {
  it("aborta el PUT, cancela el intento y libera el archivo", async () => {
    await reserveClass();
    pickFile();
    await screen.findByText(/clase\.wav/);

    fireEvent.click(screen.getByRole("button", { name: "enviar audio" }));
    await screen.findByRole("progressbar");
    expect(putSignal?.aborted).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "cancelar subida" }));

    await waitFor(() => expect(putSignal?.aborted).toBe(true));
    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.url === "/audios/a1/attempts/at1/cancel" && call.method === "POST",
        ),
      ).toBe(true),
    );

    await screen.findByRole("button", { name: "reservar subida" });
    expect(screen.queryByText(/clase\.wav/)).toBeNull();
  });

  it("muestra el resultado admitido con los bytes reales recibidos", async () => {
    putMode = "accept";
    await reserveClass();
    pickFile();

    fireEvent.click(screen.getByRole("button", { name: "enviar audio" }));

    await screen.findByText(/contenido admitido/i);
    expect(
      calls.some(
        (call) => call.method === "PUT" && call.url === "/audios/a1/content?attempt_id=at1",
      ),
    ).toBe(true);
    expect(screen.getByText(/4 B/)).toBeTruthy();
  });
});
