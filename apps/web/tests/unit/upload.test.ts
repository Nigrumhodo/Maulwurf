// D2.1 — U-S2-DA-01: formulario de subida. Sin DOM se prueban los helpers puros que
// alimentan `UploadForm`: idioma SIN preselección, aviso versionado, DOS consentimientos
// obligatorios, límites honestos (sin valores provisionales) y el body/errores de
// `POST /audios`. Añade la lectura de `/ingestion/capabilities` (A2.3).
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "@/lib/api";
import { fetchCapabilities } from "@/lib/capabilities";
import type { IngestionCapabilities } from "@/lib/capabilities";
import {
  buildAudioCreate,
  capabilitiesUsable,
  languageOptions,
  publishedLanguageCodes,
  uploadFormError,
  validateUpload,
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
