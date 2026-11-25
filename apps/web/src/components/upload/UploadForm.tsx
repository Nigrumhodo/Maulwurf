import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { IngestProgress } from "@/components/IngestProgress";
import { AudioUploadPanel } from "@/components/upload/AudioUploadPanel";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DateInput } from "@/components/ui/DateInput";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { LanguageSelect } from "@/components/ui/LanguageSelect";
import { TimezoneSelect } from "@/components/ui/TimezoneSelect";
import { intlLocale } from "@/i18n";
import { ApiHttpError } from "@/lib/api";
import { fetchCapabilities } from "@/lib/capabilities";
import type { IngestionCapabilities } from "@/lib/capabilities";
import { fetchSubjects } from "@/lib/subjects";
import type { Subject } from "@/lib/subjects";
import {
  buildAudioCreate,
  cancelAttempt,
  capabilitiesUsable,
  createAudio,
  formatBytes,
  languageOptions,
  mapUploadError,
  pickUploadFile,
  publishedLanguageCodes,
  uploadContent,
  uploadFormError,
  validateUpload,
  variantConflict,
} from "@/lib/upload";
import type {
  AudioCreated,
  UploadDraft,
  UploadErrors,
  UploadProgress,
  UploadResult,
  VariantConflict,
} from "@/lib/upload";

// D2.1 — Formulario de subida (M2). Reserva la clase con `POST /audios` antes de aceptar
// bytes: materia del tenant, fecha, zona, idioma SIN preselección (opciones publicadas por
// `/ingestion/capabilities`), título/profesor opcionales y consentimiento versionado con DOS
// acknowledgements obligatorios.
//
// D2.2 — Envío binario (U-S2-DA-02): `PUT` en streaming a la URL relativa de la reserva con
// progreso real de bytes; cancelar aborta el `fetch` y llama a la API de cancelación, y
// libera el `File`. Un `409 duplicate_variant` pide confirmación y repite `POST` + `PUT` con
// el token; `duplicate_exact` se muestra como duplicado. Nunca se persiste el audio.
type Phase = "form" | "ready" | "uploading" | "done";

const selectClass =
  "bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint focus:border-mint focus:outline-none";

const EMPTY_DRAFT: UploadDraft = {
  subjectId: "",
  classDate: "",
  classTimezone: "",
  languageCode: "",
  title: "",
  teacher: "",
  cloudAccepted: false,
  voiceAcknowledged: false,
};

export function UploadForm() {
  const { t } = useTranslation("study");
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [capabilities, setCapabilities] = useState<IngestionCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<UploadDraft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<UploadErrors>({ fields: {} });
  const [busy, setBusy] = useState(false);
  const [languageOverride, setLanguageOverride] = useState<string[] | null>(null);

  const [reservation, setReservation] = useState<AudioCreated | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [phase, setPhase] = useState<Phase>("form");
  const [result, setResult] = useState<UploadResult | null>(null);
  const [variant, setVariant] = useState<VariantConflict | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const loadGeneration = useRef(0);

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setLoadError(null);
    try {
      const [nextSubjects, nextCapabilities] = await Promise.all([
        fetchSubjects(),
        fetchCapabilities(),
      ]);
      if (generation !== loadGeneration.current) return;
      setSubjects(nextSubjects);
      setCapabilities(nextCapabilities);
    } catch (error) {
      if (generation !== loadGeneration.current) return;
      setLoadError(
        error instanceof ApiHttpError || (error instanceof Error && !(error instanceof TypeError))
          ? error.message
          : t("uploadForm.loadFailed"),
      );
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      loadGeneration.current += 1;
    };
  }, [load]);

  // Un desmontaje a mitad de subida cancela el intento (el servidor limpia al desconectar).
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  const usable = capabilitiesUsable(capabilities);
  const noticeVersion = capabilities?.privacy_notice_version ?? null;
  // A server hint about allowed languages still must not expose the `multi` policy value.
  const languageCodes =
    languageOverride !== null
      ? languageOverride.filter((code) => code !== "multi")
      : publishedLanguageCodes(capabilities);
  const languages = useMemo(() => languageOptions(languageCodes), [languageCodes]);
  const consentReady = draft.cloudAccepted && draft.voiceAcknowledged;
  const canSubmit = usable && consentReady && !busy;

  function update<K extends keyof UploadDraft>(key: K, value: UploadDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  const releaseFile = useCallback(() => {
    setSelectedFile(null);
    setProgress(null);
  }, []);

  function reset() {
    releaseFile();
    setDraft(EMPTY_DRAFT);
    setErrors({ fields: {} });
    setReservation(null);
    setResult(null);
    setVariant(null);
    setLanguageOverride(null);
    setPhase("form");
  }

  async function reserve(token: string | null = null): Promise<AudioCreated | null> {
    if (noticeVersion === null) return null;
    setBusy(true);
    try {
      const created = await createAudio(buildAudioCreate(draft, noticeVersion, token));
      setReservation(created);
      setResult(null);
      setVariant(null);
      setConfirmOpen(false);
      setPhase("ready");
      return created;
    } catch (error) {
      if (!(error instanceof ApiHttpError)) {
        setErrors({
          fields: {},
          form:
            error instanceof Error && !(error instanceof TypeError)
              ? error.message
              : t("uploadForm.reserveFailed"),
        });
        return null;
      }
      const mapped = uploadFormError(error);
      if (mapped.field === "consent") {
        setErrors({ fields: {}, consent: mapped.message });
        if (mapped.resetConsent) {
          update("cloudAccepted", false);
          update("voiceAcknowledged", false);
        }
      } else if (mapped.field === "language") {
        setErrors({ fields: { languageCode: mapped.message } });
        if (mapped.allowed && mapped.allowed.length > 0) setLanguageOverride(mapped.allowed);
      } else if (mapped.field === "variant" && mapped.variant) {
        setVariant({
          existingAudioId: mapped.variant.existing_audio_id,
          token: mapped.variant.variant_confirmation_token,
          expiresAt: mapped.variant.expires_at,
        });
        setConfirmOpen(true);
      } else {
        setErrors({ fields: {}, form: mapped.message });
      }
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setErrors({ fields: {} });
    const validation = validateUpload(draft, capabilities);
    if (
      validation.form != null ||
      validation.consent != null ||
      Object.keys(validation.fields).length > 0
    ) {
      setErrors(validation);
      return;
    }
    await reserve();
  }

  async function runUpload(target: AudioCreated, file: File) {
    const controller = new AbortController();
    abortRef.current = controller;
    setFileError(null);
    setProgress({ sentBytes: 0, totalBytes: file.size });
    setPhase("uploading");
    try {
      const next = await uploadContent({
        uploadUrl: target.upload_url,
        file,
        signal: controller.signal,
        onProgress: setProgress,
      });
      setResult(next);
      setPhase("done");
      releaseFile();
    } catch (error) {
      // La cancelación la gobierna `handleCancel`; aquí solo se evita pisar su estado.
      if (controller.signal.aborted) return;
      if (error instanceof ApiHttpError) {
        const conflict = variantConflict(error);
        if (conflict !== null) {
          setVariant(conflict);
          setConfirmOpen(true);
          setPhase("ready");
          return;
        }
        const view = mapUploadError(error);
        setErrors({ fields: {}, form: view.message });
        if (view.resetReservation === true) {
          releaseFile();
          setReservation(null);
          setPhase("form");
        } else {
          setPhase("ready");
        }
        return;
      }
      setErrors({
        fields: {},
        form: t("libraryPage.sendFailed"),
      });
      setPhase("ready");
    } finally {
      abortRef.current = null;
    }
  }

  async function handleCancel() {
    abortRef.current?.abort();
    abortRef.current = null;
    if (reservation !== null) {
      try {
        await cancelAttempt(reservation.audio_id, reservation.attempt_id);
      } catch {
        // El servidor también cancela al detectar la desconexión (`_abort_disconnected`).
      }
    }
    releaseFile();
    setReservation(null);
    setVariant(null);
    setConfirmOpen(false);
    setResult(null);
    setErrors({ fields: {} });
    setPhase("form");
  }

  function handleFileChange(file: File | null) {
    setFileError(null);
    setResult(null);
    const picked = pickUploadFile(file, capabilities);
    setSelectedFile(picked.file);
    setFileError(picked.error);
  }

  async function handleConfirmVariant() {
    const file = selectedFile;
    const token = variant?.token;
    setConfirmOpen(false);
    setVariant(null);
    if (token === undefined) return;
    const created = await reserve(token);
    if (created !== null && file !== null) await runUpload(created, file);
  }

  if (loading) {
    return <p className="lowercase text-base text-lavender/60">{t("uploadForm.loading")}</p>;
  }

  if (loadError != null) {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="lowercase text-mint">
          &gt; {loadError}
        </p>
        <Button type="button" variant="secondary" onClick={() => void load()}>
          {t("uploadForm.retry")}
        </Button>
      </div>
    );
  }

  if (phase === "done" && result !== null) {
    const accepted = result.outcome === "accepted";
    // El `PUT` admite la reserva, así que su audio_id es el que progresa; `duplicate_exact`
    // devuelve la clase canónica que ya existía.
    const progressAudioId =
      result.outcome === "duplicate_exact" ? result.audioId : (reservation?.audio_id ?? null);
    return (
      <div className="lowercase flex flex-col gap-3">
        <p className="text-mint">
          {accepted ? t("uploadForm.acceptedMsg") : t("uploadForm.duplicateMsg")}
        </p>
        <dl className="grid gap-1 text-lavender/80">
          {accepted ? (
            <>
              <div className="flex gap-2">
                <dt className="text-lavender/60">{t("uploadForm.attempt")}</dt>
                <dd>{result.attemptId ?? "—"}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="text-lavender/60">{t("uploadForm.receivedBytes")}</dt>
                <dd>
                  {result.receivedBytes !== null ? formatBytes(result.receivedBytes) : "—"}
                </dd>
              </div>
            </>
          ) : (
            <div className="flex gap-2">
              <dt className="text-lavender/60">{t("uploadForm.existingClass")}</dt>
              <dd>{result.audioId ?? "—"}</dd>
            </div>
          )}
        </dl>
        <p className="text-lavender/60">
          {t("uploadForm.notKept")}
        </p>
        {progressAudioId !== null && <IngestProgress audioId={progressAudioId} />}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="secondary" onClick={reset}>
            {t("uploadForm.uploadAnother")}
          </Button>
          <Link
            to="/gestion/biblioteca"
            className="lowercase font-pixel border-2 border-mint px-3 py-2 text-mint hover:bg-mint hover:text-on-accent"
          >
            {t("uploadForm.goToLibrary")}
          </Link>
        </div>
      </div>
    );
  }

  if (reservation !== null) {
    const uploading = phase === "uploading";
    const subjectName = subjects.find((subject) => subject.id === draft.subjectId)?.name ?? t("uploadForm.defaultSubject");
    return (
      <div className="lowercase flex flex-col gap-4">
        <div className="flex flex-col gap-1 text-base text-lavender/80">
          <p>
            {t("uploadForm.reserved", {
              subject: subjectName,
              date: draft.classDate || t("uploadForm.noDate"),
            })}
          </p>
          <p className="text-lavender/60">
            {t("uploadForm.expires", {
              date: new Date(reservation.upload_expires_at).toLocaleString(intlLocale()),
            })}
          </p>
        </div>

        <AudioUploadPanel
          capabilities={capabilities}
          selectedFile={selectedFile}
          fileError={fileError}
          progress={progress}
          uploading={uploading}
          formError={errors.form ?? null}
          onFileChange={handleFileChange}
          onUpload={() => {
            if (selectedFile !== null) void runUpload(reservation, selectedFile);
          }}
          onCancel={() => void handleCancel()}
        />

        <ConfirmDialog
          open={confirmOpen}
          title={t("uploadForm.variantTitle")}
          description={
            variant !== null ? (
              <span>{t("uploadForm.variantDescription", { id: variant.existingAudioId })}</span>
            ) : null
          }
          confirmLabel={t("uploadForm.variantConfirm")}
          cancelLabel={t("shared.cancel")}
          onConfirm={() => void handleConfirmVariant()}
          onCancel={() => {
            setConfirmOpen(false);
            setVariant(null);
          }}
        />
      </div>
    );
  }

  if (subjects.length === 0) {
    return (
      <EmptyState
        title={t("uploadForm.noSubjectsTitle")}
        description={t("uploadForm.noSubjectsDescription")}
        action={
          <Link
            to="/gestion/materias"
            className="lowercase font-pixel border-2 border-mint px-3 py-2 text-mint hover:bg-mint hover:text-on-accent"
          >
            {t("uploadForm.createSubject")}
          </Link>
        }
      />
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col divide-y-2 divide-lavender/20"
      noValidate
    >
      <section aria-label={t("uploadForm.limitsAria")} className="lowercase flex flex-col gap-1 pb-5 text-base">
        {usable ? (
          <p className="text-lavender/80">
            {t("uploadForm.limits", {
              max: formatBytes(capabilities?.max_upload_bytes ?? null),
              seconds: capabilities?.max_duration_seconds ?? "?",
              formats:
                (capabilities?.accepted_input_formats ?? []).join(", ") ||
                t("uploadForm.noFormats"),
              slots: capabilities?.active_slots ?? "?",
            })}
          </p>
        ) : (
          <p role="status" className="text-mint">
            {t("uploadForm.noLimits")}
          </p>
        )}
      </section>

      <section className="flex flex-col gap-3 py-5">
        <h3 className="text-lg text-mint">{t("uploadForm.secWhat")}</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <label className="lowercase font-pixel flex flex-col gap-1">
              <span className="text-mint">{t("uploadForm.subject")}</span>
              <select
                className={selectClass}
                value={draft.subjectId}
                onChange={(event) => update("subjectId", event.target.value)}
              >
                <option value="" disabled>
                  {t("uploadForm.selectSubject")}
                </option>
                {subjects.map((subject) => (
                  <option key={subject.id} value={subject.id}>
                    {subject.name}
                  </option>
                ))}
              </select>
            </label>
            {errors.fields.subjectId != null && (
              <span className="lowercase text-mint">{errors.fields.subjectId}</span>
            )}
          </div>

          <Input
            label={t("uploadForm.titleLabel")}
            placeholder={t("uploadForm.titlePlaceholder")}
            value={draft.title}
            onChange={(event) => update("title", event.target.value)}
          />
          <Input
            label={t("uploadForm.teacherLabel")}
            placeholder=""
            value={draft.teacher}
            onChange={(event) => update("teacher", event.target.value)}
          />
        </div>
      </section>

      <section className="flex flex-col gap-3 py-5">
        <h3 className="text-lg text-mint">{t("uploadForm.secWhen")}</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <DateInput
              label={t("uploadForm.classDate")}
              value={draft.classDate}
              onChange={(event) => update("classDate", event.target.value)}
            />
            {errors.fields.classDate != null && (
              <span className="lowercase text-mint">{errors.fields.classDate}</span>
            )}
          </div>

          <div className="flex flex-col gap-1">
            <TimezoneSelect
              label={t("uploadForm.timezone")}
              value={draft.classTimezone}
              onChange={(event) => update("classTimezone", event.target.value)}
            />
            {errors.fields.classTimezone != null && (
              <span className="lowercase text-mint">{errors.fields.classTimezone}</span>
            )}
          </div>
        </div>
      </section>

      <section className="flex flex-col gap-3 py-5">
        <h3 className="text-lg text-mint">{t("uploadForm.secLanguage")}</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <LanguageSelect
              label={t("uploadForm.audioLanguage")}
              options={languages}
              value={draft.languageCode}
              onChange={(event) => update("languageCode", event.target.value)}
            />
            {errors.fields.languageCode != null && (
              <span className="lowercase text-mint">{errors.fields.languageCode}</span>
            )}
          </div>
        </div>
      </section>

      <section className="lowercase flex flex-col gap-2 py-5">
        <h3 className="text-lg text-mint">{t("uploadForm.secPrivacy")}</h3>
        <p className="text-base text-lavender">
          {/* A new tab keeps the in-progress draft: the form lives only in component state. */}
          <Link
            to="/privacidad"
            target="_blank"
            rel="noopener"
            className="text-mint hover:underline"
          >
            {t("uploadForm.privacyNotice")}
          </Link>{" "}
          · {t("uploadForm.version", { version: noticeVersion ?? t("uploadForm.notPublished") })}
        </p>
        <p className="text-base text-lavender/80">{t("uploadForm.cloudNote")}</p>
        <p className="text-base text-lavender/80">{t("uploadForm.thirdPartyConsent")}</p>

        <label className="flex min-h-8 items-start gap-2 text-base text-lavender">
          <input
            type="checkbox"
            checked={draft.cloudAccepted}
            onChange={(event) => update("cloudAccepted", event.target.checked)}
            className="mt-1 accent-mint"
          />
          <span>{t("uploadForm.cloudCheckbox")}</span>
        </label>

        <label className="flex min-h-8 items-start gap-2 text-base text-lavender">
          <input
            type="checkbox"
            checked={draft.voiceAcknowledged}
            onChange={(event) => update("voiceAcknowledged", event.target.checked)}
            className="mt-1 accent-mint"
          />
          <span>{t("uploadForm.voiceCheckbox")}</span>
        </label>

        {errors.consent != null && (
          <p role="alert" className="text-mint">
            &gt; {errors.consent}
          </p>
        )}

        <p className="text-base text-lavender/60">
          {t("uploadForm.notKeptForm")}
        </p>
      </section>

      <div className="flex flex-col gap-3 pt-5">
        {errors.form != null && (
          <p role="alert" className="lowercase text-mint">
            &gt; {errors.form}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={!canSubmit}>
            {t("uploadForm.reserve")}
          </Button>
          {!usable ? (
            <span className="lowercase text-base text-lavender/60">
              {t("uploadForm.waitingLimits")}
            </span>
          ) : (
            !consentReady && (
              <span className="lowercase text-base text-lavender/60">
                {t("uploadForm.acceptConsent")}
              </span>
            )
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title={t("uploadForm.variantTitle")}
        description={
          variant !== null ? (
            <span>{t("uploadForm.variantDescription", { id: variant.existingAudioId })}</span>
          ) : null
        }
        confirmLabel={t("uploadForm.variantConfirm")}
          cancelLabel={t("shared.cancel")}
        onConfirm={() => void handleConfirmVariant()}
        onCancel={() => {
          setConfirmOpen(false);
          setVariant(null);
        }}
      />
    </form>
  );
}
