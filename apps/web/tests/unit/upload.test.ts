// D2.1 — U-S2-DA-01: formulario de subida. Sin DOM se prueban los helpers puros que
// alimentan `UploadForm`: idioma SIN preselección, aviso versionado, DOS consentimientos
// obligatorios, límites honestos (sin valores provisionales) y el body/errores de
// `POST /audios`. Añade la lectura de `/ingestion/capabilities` (A2.3).
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "@/lib/api";
import { fetchCapabilities } from "@/lib/capabilities";
import type { IngestionCapabilities } from "@/lib/capabilities";
import { clearCsrfToken, setCsrfToken } from "@/lib/csrf";
import {
  acceptedFileExtensions,
  buildAudioCreate,
  cancelAttempt,
  capabilitiesUsable,
  createProgressStream,
  formatBytes,
  languageOptions,
  mapUploadError,
  pickUploadFile,
  publishedLanguageCodes,
  releaseUploadResources,
  uploadContent,
  uploadFormError,
  validateFile,
  validateUpload,
  variantConflict,
} from "@/lib/upload";
import type { UploadDraft } from "@/lib/upload";

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

const DRAFT: UploadDraft = {
  subjectId: "s-1",
  classDate: "2026-10-29",
  classTimezone: "America/Bogota",
  languageCode: "es",
  title: "  ",
  teacher: "  ",
  cloudAccepted: true,
  voiceAcknowledged: true,
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearCsrfToken();
});

describe("languageOptions", () => {
  it("usa la etiqueta conocida y deja el código si no la tiene", () => {
    expect(languageOptions(["es", "de"])).toEqual([
      { code: "es", label: "español" },
      { code: "de", label: "de" },
    ]);
  });

  it("no preselecciona nada: la lista vacía no inventa un idioma", () => {
    expect(languageOptions([])).toEqual([]);
  });
});

describe("capabilitiesUsable", () => {
  it("exige límites validados y versión de aviso", () => {
    expect(capabilitiesUsable(null)).toBe(false);
    expect(capabilitiesUsable({ ...CAPABILITIES, limits_validated: false })).toBe(false);
    expect(capabilitiesUsable({ ...CAPABILITIES, privacy_notice_version: null })).toBe(false);
    expect(capabilitiesUsable(CAPABILITIES)).toBe(true);
  });
});

describe("publishedLanguageCodes", () => {
  it("quita `multi`, que es política y no un idioma ofrecible", () => {
    expect(publishedLanguageCodes({ ...CAPABILITIES, languages: ["es", "multi", "en"] })).toEqual([
      "es",
      "en",
    ]);
  });

  it("no inventa códigos cuando el servidor no publica ninguno", () => {
    expect(publishedLanguageCodes({ ...CAPABILITIES, languages: [] })).toEqual([]);
    expect(publishedLanguageCodes(null)).toEqual([]);
  });
});

describe("validateUpload (U-S2-DA-01)", () => {
  it("exige idioma SIN preselección", () => {
    const errors = validateUpload({ ...DRAFT, languageCode: "" }, CAPABILITIES);
    expect(errors.fields.languageCode).toBeDefined();
  });

  it("exige los DOS consentimientos", () => {
    expect(validateUpload({ ...DRAFT, cloudAccepted: false }, CAPABILITIES).consent).toBeDefined();
    expect(validateUpload({ ...DRAFT, voiceAcknowledged: false }, CAPABILITIES).consent).toBeDefined();
  });

  it("rechaza un idioma que el servidor no publicó", () => {
    const errors = validateUpload({ ...DRAFT, languageCode: "de" }, CAPABILITIES);
    expect(errors.fields.languageCode).toBeDefined();
  });

  it("rechaza `multi` aunque venga en la lista publicada", () => {
    const errors = validateUpload(
      { ...DRAFT, languageCode: "multi" },
      { ...CAPABILITIES, languages: ["es", "multi"] },
    );
    expect(errors.fields.languageCode).toBeDefined();
  });

  it("bloquea el envío si no hay idiomas publicados", () => {
    const errors = validateUpload(DRAFT, { ...CAPABILITIES, languages: [] });
    expect(errors.form).toBeDefined();
  });

  it("exige materia, fecha y zona", () => {
    const errors = validateUpload(
      { ...DRAFT, subjectId: "", classDate: "", classTimezone: "" },
      CAPABILITIES,
    );
    expect(errors.fields.subjectId).toBeDefined();
    expect(errors.fields.classDate).toBeDefined();
    expect(errors.fields.classTimezone).toBeDefined();
  });

  it("no admite envío sin límites publicados (sin valores provisionales)", () => {
    const errors = validateUpload(DRAFT, { ...CAPABILITIES, limits_validated: false });
    expect(errors.form).toBeDefined();
    expect(errors.consent).toBeUndefined();
  });

  it("acepta un borrador completo", () => {
    expect(validateUpload(DRAFT, CAPABILITIES)).toEqual({ fields: {} });
  });
});

describe("buildAudioCreate", () => {
  it("construye el body del contrato con opcionales vacíos en null", () => {
    expect(buildAudioCreate(DRAFT, "2026-09-v1")).toEqual({
      subject_id: "s-1",
      class_date: "2026-10-29",
      class_timezone: "America/Bogota",
      language_code: "es",
      title: null,
      teacher: null,
      privacy_notice_version: "2026-09-v1",
      cloud_processing_accepted: true,
      third_party_voice_acknowledged: true,
    });
  });

  it("recorta título y profesor no vacíos", () => {
    const body = buildAudioCreate(
      { ...DRAFT, title: "  integrales ", teacher: " profe " },
      "2026-09-v1",
    );
    expect(body.title).toBe("integrales");
    expect(body.teacher).toBe("profe");
  });
});

describe("uploadFormError", () => {
  it("consent_required pide volver a aceptar", () => {
    const mapped = uploadFormError(
      new ApiHttpError(422, {
        code: "consent_required",
        message: "El aviso de privacidad cambió.",
        details: { privacy_notice_version: "2026-09-v1" },
      }),
    );
    expect(mapped.field).toBe("consent");
    expect(mapped.resetConsent).toBe(true);
  });

  it("language_not_allowed expone los idiomas aceptados", () => {
    const mapped = uploadFormError(
      new ApiHttpError(422, {
        code: "language_not_allowed",
        message: "Idioma no admitido.",
        details: { allowed: ["es"] },
      }),
    );
    expect(mapped.field).toBe("language");
    expect(mapped.allowed).toEqual(["es"]);
  });

  it("validation_failed muestra el primer campo", () => {
    const mapped = uploadFormError(
      new ApiHttpError(422, {
        code: "validation_failed",
        message: "Hay campos inválidos.",
        details: { fields: { class_timezone: "zona horaria IANA desconocida" } },
      }),
    );
    expect(mapped.message).toContain("IANA");
  });

  it("429 y 503 añaden un consejo de reintento", () => {
    expect(
      uploadFormError(new ApiHttpError(429, { code: "rate_limited", message: "Cuota superada." })).message,
    ).toContain("inténtalo de nuevo");
    expect(
      uploadFormError(new ApiHttpError(503, { code: "capacity_unavailable", message: "Sin slot." })).message,
    ).toContain("reintenta");
  });
});

describe("fetchCapabilities", () => {
  it("lee GET /ingestion/capabilities y devuelve el JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(CAPABILITIES), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const capabilities = await fetchCapabilities();

    expect(capabilities).toEqual(CAPABILITIES);
    expect(fetchMock).toHaveBeenCalledWith("/ingestion/capabilities", expect.anything());
  });
});

// D2.2 — U-S2-DA-02

function responseJson(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("formatBytes y extensiones aceptadas", () => {
  it("formatea sin inventar un límite ausente", () => {
    expect(formatBytes(null)).toBe("no publicado");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2 KiB");
    expect(formatBytes(1024 * 1024)).toBe("1.0 MiB");
  });

  it("deriva las extensiones del input desde los formatos publicados", () => {
    expect(
      acceptedFileExtensions({ ...CAPABILITIES, accepted_input_formats: ["wav", "MP3"] }),
    ).toEqual([".wav", ".mp3"]);
    expect(acceptedFileExtensions(null)).toEqual([]);
  });
});

describe("validateFile", () => {
  const caps: IngestionCapabilities = {
    ...CAPABILITIES,
    max_upload_bytes: 16,
    accepted_input_formats: ["wav"],
  };

  it("rechaza un archivo vacío", () => {
    const check = validateFile(new File([], "vacio.wav", { type: "audio/wav" }), caps);
    expect(check.ok).toBe(false);
    expect(check.error).toContain("vacío");
  });

  it("rechaza un archivo por encima del máximo publicado", () => {
    const file = new File([new Uint8Array(17)], "grande.wav", { type: "audio/wav" });
    expect(validateFile(file, caps).ok).toBe(false);
  });

  it("rechaza un formato no publicado aunque el MIME sea de audio", () => {
    const file = new File([new Uint8Array(2)], "otro.mp3", { type: "audio/mpeg" });
    const check = validateFile(file, caps);
    expect(check.ok).toBe(false);
    expect(check.error).toContain("wav");
  });

  it("acepta el formato publicado dentro del máximo", () => {
    const file = new File([new Uint8Array(2)], "clase.wav", { type: "audio/wav" });
    expect(validateFile(file, caps)).toEqual({ ok: true, format: "wav" });
  });
});

describe("pickUploadFile", () => {
  it("comparte la validación entre subida nueva y reupload", () => {
    expect(pickUploadFile(null, CAPABILITIES)).toEqual({ file: null, error: null });

    const invalid = new File([new Uint8Array(2)], "clase.mp3", { type: "audio/mpeg" });
    const rejected = pickUploadFile(invalid, CAPABILITIES);
    expect(rejected.file).toBeNull();
    expect(rejected.error).toContain("wav");

    const valid = new File([new Uint8Array(2)], "clase.wav", { type: "audio/wav" });
    expect(pickUploadFile(valid, CAPABILITIES)).toEqual({ file: valid, error: null });
  });
});

describe("createProgressStream", () => {
  it("cuenta los bytes reales entregados al fetch", async () => {
    const file = new File([new Uint8Array([1, 2, 3]), new Uint8Array([4, 5])], "clase.wav", {
      type: "audio/wav",
    });
    const seen: number[] = [];
    const reader = createProgressStream(file, (progress) => seen.push(progress.sentBytes)).getReader();

    let total = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
    }

    expect(total).toBe(5);
    expect(seen.at(-1)).toBe(5);
    expect(seen.every((value, index) => index === 0 || value >= seen[index - 1])).toBe(true);
  });
});

describe("uploadContent", () => {
  it("hace un PUT relativo en streaming con CSRF y devuelve accepted", async () => {
    setCsrfToken("csrf-1");
    const captured: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        captured.push({ url: String(input), init: init ?? {} });
        const reader = (init?.body as ReadableStream<Uint8Array>).getReader();
        let sent = 0;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          sent += value.byteLength;
        }
        return responseJson(
          { attempt_id: "at1", status: "received", received_bytes: sent },
          202,
        );
      }),
    );

    const seen: number[] = [];
    const result = await uploadContent({
      uploadUrl: "/audios/a1/content?attempt_id=at1",
      file: new File([new Uint8Array([1, 2, 3, 4])], "clase.wav", { type: "audio/wav" }),
      onProgress: (progress) => seen.push(progress.sentBytes),
    });

    expect(captured[0].url).toBe("/audios/a1/content?attempt_id=at1");
    expect(captured[0].init.method).toBe("PUT");
    expect(new Headers(captured[0].init.headers).get("X-CSRF-Token")).toBe("csrf-1");
    expect(result).toEqual({
      outcome: "accepted",
      attemptId: "at1",
      status: "received",
      receivedBytes: 4,
    });
    expect(seen.at(-1)).toBe(4);
  });

  it("reconoce duplicate_exact como resultado, no como error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        await (init?.body as ReadableStream<Uint8Array>).cancel();
        return responseJson({ audio_id: "a9", outcome: "duplicate_exact" }, 200);
      }),
    );

    await expect(
      uploadContent({
        uploadUrl: "/audios/a1/content?attempt_id=at1",
        file: new File([new Uint8Array([1])], "clase.wav", { type: "audio/wav" }),
      }),
    ).resolves.toEqual({ outcome: "duplicate_exact", audioId: "a9" });
  });

  it("exige una URL relativa (mismo origen)", async () => {
    await expect(
      uploadContent({
        uploadUrl: "https://evil.example/upload",
        file: new File([new Uint8Array([1])], "clase.wav", { type: "audio/wav" }),
      }),
    ).rejects.toThrow("relativa");
  });
});

describe("cancelAttempt", () => {
  it("cancela el intento con POST y absorbe 409/404 ya finalizados", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(input), method: (init?.method ?? "GET").toUpperCase() });
        if (calls.length === 1) {
          return responseJson({ attempt_id: "at1", status: "cancelled" }, 202);
        }
        return responseJson({ error: { code: "invalid_transition", message: "final" } }, 409);
      }),
    );

    expect(await cancelAttempt("a1", "at1")).toBe("cancelled");
    expect(await cancelAttempt("a1", "at1")).toBeNull();
    expect(calls[0]).toEqual({
      url: "/audios/a1/attempts/at1/cancel",
      method: "POST",
    });
  });
});

describe("variantConflict", () => {
  it("lee el token opaco y la clase existente del 409 duplicate_variant", () => {
    const error = new ApiHttpError(409, {
      code: "duplicate_variant",
      message: "Existe una clase con el mismo contenido.",
      details: {
        existing_audio_id: "a9",
        variant_confirmation_token: "tok",
        expires_at: "2026-10-30T00:00:00Z",
      },
    });
    expect(variantConflict(error)).toEqual({
      existingAudioId: "a9",
      token: "tok",
      expiresAt: "2026-10-30T00:00:00Z",
    });
  });

  it("no trata otros 409 ni un token ausente como variante", () => {
    expect(
      variantConflict(new ApiHttpError(409, { code: "attempt_not_active", message: "x" })),
    ).toBeNull();
    expect(
      variantConflict(
        new ApiHttpError(409, { code: "duplicate_variant", message: "x", details: {} }),
      ),
    ).toBeNull();
  });
});

describe("mapUploadError", () => {
  it("distingue reinicio de reserva, reintento y límite real", () => {
    const tooBig = mapUploadError(
      new ApiHttpError(413, {
        code: "payload_too_large",
        message: "El archivo supera el tamaño máximo permitido.",
        details: { max_bytes: 960044 },
      }),
    );
    expect(tooBig.message).toContain("máximo");
    expect(tooBig.resetReservation).toBeUndefined();

    expect(
      mapUploadError(new ApiHttpError(410, { code: "upload_expired", message: "Expiró." }))
        .resetReservation,
    ).toBe(true);
    expect(
      mapUploadError(
        new ApiHttpError(409, { code: "attempt_not_active", message: "Inactivo." }),
      ).resetReservation,
    ).toBe(true);
    expect(
      mapUploadError(
        new ApiHttpError(422, { code: "variant_hash_mismatch", message: "No coincide." }),
      ).resetReservation,
    ).toBe(true);
    expect(
      mapUploadError(
        new ApiHttpError(503, { code: "capacity_unavailable", message: "Ocupado." }),
      ).retry,
    ).toBe(true);
  });
});

describe("buildAudioCreate · token de variante", () => {
  it("solo añade el token cuando el humano confirmó la variante", () => {
    expect(buildAudioCreate(DRAFT, "2026-09-v1")).not.toHaveProperty(
      "variant_confirmation_token",
    );
    expect(buildAudioCreate(DRAFT, "2026-09-v1", "tok").variant_confirmation_token).toBe("tok");
  });
});

describe("releaseUploadResources", () => {
  it("revoca la URL de objeto si existiera y tolera null", () => {
    const revoke = vi.fn();
    vi.stubGlobal("URL", { revokeObjectURL: revoke });

    releaseUploadResources("blob:preview");
    releaseUploadResources(null);

    expect(revoke).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith("blob:preview");
  });
});
