// D2.4 — E-S2-DA-06 (soporte unitario): contrato de biblioteca, agrupación y reglas que
// separan «volver a subir» de «reintentar» sin deducir estados.
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "@/lib/api";
import {
  audioLabel,
  failedStages,
  fetchAudioDetail,
  fetchAudios,
  groupBySubject,
  hasUsableTranscript,
  isCleanupPending,
  libraryActionError,
  reuploadAudio,
  retryAudio,
} from "@/lib/library";
import type { LibraryAudio, LibraryAudioDetail } from "@/lib/library";

function audio(overrides: Partial<LibraryAudio> = {}): LibraryAudio {
  return {
    id: "a1",
    subject: { id: "s1", name: "calculo", color: "#112233" },
    title: "integrales",
    class_date: "2026-10-29",
    class_timezone: "America/Bogota",
    language_code: "es",
    duration_seconds: 1800,
    created_at: "2026-10-29T12:00:00Z",
    attempt: {
      attempt_id: "at1",
      status: "succeeded",
      received_bytes: 1024,
      fragments_done: 4,
      fragments_total: 4,
      cleanup_status: "verified",
    },
    stages: { index: "succeeded", analyze: "not_requested" },
    aggregate_status: "ready",
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("agrupación y reglas honestas", () => {
  it("agrupa por materia preservando el orden del servidor", () => {
    const groups = groupBySubject([
      audio({ id: "a1", subject: { id: "s1", name: "calculo", color: "#111111" } }),
      audio({ id: "a2", subject: { id: "s2", name: "fisica", color: "#222222" } }),
      audio({ id: "a3", subject: { id: "s1", name: "calculo", color: "#111111" } }),
    ]);
    expect(groups.map((group) => group.name)).toEqual(["calculo", "fisica"]);
    expect(groups[0]?.items.map((item) => item.id)).toEqual(["a1", "a3"]);
    expect(groups[1]?.items.map((item) => item.id)).toEqual(["a2"]);
  });

  it("solo ready/partial implican texto utilizable", () => {
    expect(hasUsableTranscript(audio({ aggregate_status: "ready" }))).toBe(true);
    expect(hasUsableTranscript(audio({ aggregate_status: "partial" }))).toBe(true);
    expect(hasUsableTranscript(audio({ aggregate_status: "processing" }))).toBe(false);
    expect(hasUsableTranscript(audio({ aggregate_status: "failed" }))).toBe(false);
  });

  it("reintenta solo etapas failed/cancelled", () => {
    expect(
      failedStages(
        audio({ stages: { index: "succeeded", analyze: "failed" }, aggregate_status: "partial" }),
      ),
    ).toEqual(["analyze"]);
    expect(
      failedStages(
        audio({
          stages: { index: "cancelled", analyze: "pending" },
          aggregate_status: "partial",
        }),
      ),
    ).toEqual(["index"]);
    expect(
      failedStages(
        audio({ stages: { index: "succeeded", analyze: "not_requested" }, aggregate_status: "ready" }),
      ),
    ).toEqual([]);
  });

  it("detecta limpieza pendiente y no la confunde con un estado final", () => {
    expect(
      isCleanupPending(
        audio({
          attempt: {
            attempt_id: "at1",
            status: "transcript_committed_cleanup_pending",
            received_bytes: 10,
            fragments_done: 1,
            fragments_total: 1,
            cleanup_status: "pending",
          },
        }),
      ),
    ).toBe(true);
    expect(isCleanupPending(audio())).toBe(false);
    expect(audioLabel(audio({ title: "  " }))).toBe("clase sin título");
  });
});

describe("llamadas al contrato", () => {
  it("lista con el límite máximo y el filtro de materia, sin caché", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [audio()] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const items = await fetchAudios("s1");

    expect(items).toHaveLength(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/audios?limit=100&subject_id=s1");
    expect(init.cache).toBe("no-store");
  });

  it("lee el detalle y envía retry solo con etapas y versión vigente", async () => {
    const detail: LibraryAudioDetail = {
      ...audio({ aggregate_status: "partial", stages: { index: "succeeded", analyze: "failed" } }),
      audio_retained: false,
      transcript: { version: 3, timestamp_precision: "segment", char_count: 900 },
      exports: { txt: true, srt: true },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(detail), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            audio_id: "a1",
            transcript_version: 3,
            runs: [{ stage: "analyze", run_id: "r1", status: "pending" }],
          }),
          { status: 202, headers: { "content-type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchAudioDetail("a1")).resolves.toEqual(detail);
    await retryAudio("a1", ["analyze"], 3);

    const [retryUrl, retryInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(retryUrl).toBe("/audios/a1/retry");
    expect(retryInit.method).toBe("POST");
    expect(JSON.parse(String(retryInit.body))).toEqual({
      stages: ["analyze"],
      transcript_version: 3,
    });
  });

  it("reupload es un POST sin cuerpo a la clase exacta", async () => {
    const created = {
      audio_id: "a1",
      attempt_id: "at2",
      upload_url: "/audios/a1/content?attempt_id=at2",
      upload_expires_at: "2026-10-30T00:00:00Z",
      outcome: "reupload",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(created), {
        status: 201,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(reuploadAudio("a1")).resolves.toEqual(created);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/audios/a1/reupload");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });
});

describe("errores de acciones", () => {
  it("marca recarga en transiciones obsoletas y consejo en límites", () => {
    expect(
      libraryActionError(
        new ApiHttpError(409, { code: "invalid_transition", message: "Estado inválido." }),
      ),
    ).toEqual({
      message: "Estado inválido. Actualiza la biblioteca para ver el estado real.",
      refresh: true,
    });
    expect(
      libraryActionError(
        new ApiHttpError(409, { code: "transcript_unavailable", message: "Sin texto." }),
      ).refresh,
    ).toBe(true);
    expect(
      libraryActionError(new ApiHttpError(429, { code: "rate_limited", message: "Cuota." }))
        .message,
    ).toContain("Reintenta");
  });
});
