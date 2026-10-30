import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { DateInput } from "@/components/ui/DateInput";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { LanguageSelect } from "@/components/ui/LanguageSelect";
import { TimezoneSelect } from "@/components/ui/TimezoneSelect";
import { ApiHttpError, apiFetch } from "@/lib/api";
import { fetchCapabilities } from "@/lib/capabilities";
import type { IngestionCapabilities } from "@/lib/capabilities";
import { fetchSubjects } from "@/lib/subjects";
import type { Subject } from "@/lib/subjects";
import {
  buildAudioCreate,
  capabilitiesUsable,
  languageOptions,
  publishedLanguageCodes,
  uploadFormError,
  validateUpload,
} from "@/lib/upload";
import type { AudioCreated, UploadDraft, UploadErrors } from "@/lib/upload";

// D2.1 — Formulario de subida (M2). Reserva la clase con `POST /audios` antes de aceptar
// bytes: materia del tenant, fecha, zona, idioma SIN preselección (opciones publicadas por
// `/ingestion/capabilities`), título/profesor opcionales y consentimiento versionado con DOS
// acknowledgements obligatorios. El envío del binario es D2.2; aquí solo se crea la reserva.
const selectClass =
  "bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint focus:border-mint focus:outline-none";

function formatBytes(value: number | null): string {
  if (value === null) return "no publicado";
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KiB`;
  return `${value} B`;
}

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
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [capabilities, setCapabilities] = useState<IngestionCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<UploadDraft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<UploadErrors>({ fields: {} });
  const [busy, setBusy] = useState(false);
  const [reservation, setReservation] = useState<AudioCreated | null>(null);
  const [languageOverride, setLanguageOverride] = useState<string[] | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [nextSubjects, nextCapabilities] = await Promise.all([
        fetchSubjects(),
        fetchCapabilities(),
      ]);
      setSubjects(nextSubjects);
      setCapabilities(nextCapabilities);
    } catch (error) {
      setLoadError(
        error instanceof ApiHttpError
          ? error.message
          : "no se pudieron cargar los datos de subida; revisa tu conexión.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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

  function reset() {
    setDraft(EMPTY_DRAFT);
    setErrors({ fields: {} });
    setReservation(null);
    setLanguageOverride(null);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setErrors({ fields: {} });
    const validation = validateUpload(draft, capabilities);
    if (validation.form != null || validation.consent != null || Object.keys(validation.fields).length > 0) {
      setErrors(validation);
      return;
    }
    if (noticeVersion === null) return;

    setBusy(true);
    try {
      const res = await apiFetch("/audios", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(buildAudioCreate(draft, noticeVersion)),
      });
      setReservation((await res.json()) as AudioCreated);
    } catch (error) {
      if (!(error instanceof ApiHttpError)) {
        setErrors({ fields: {}, form: "no se pudo reservar la subida; revisa tu conexión." });
        return;
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
      } else {
        setErrors({ fields: {}, form: mapped.message });
      }
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <p className="lowercase text-lavender/60">&gt; cargando datos de subida…</p>;
  }

  if (loadError != null) {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="lowercase text-mint">
          &gt; {loadError}
        </p>
        <Button type="button" variant="secondary" onClick={() => void load()}>
          reintentar
        </Button>
      </div>
    );
  }

  if (reservation !== null) {
    return (
      <div className="lowercase flex flex-col gap-3">
        <p className="text-mint">&gt; reserva creada · la clase está lista para recibir el audio</p>
        <dl className="grid gap-1 text-lavender/70">
          <div className="flex gap-2">
            <dt className="text-lavender/50">clase</dt>
            <dd>{reservation.audio_id}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-lavender/50">intento</dt>
            <dd>{reservation.attempt_id}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-lavender/50">expira</dt>
            <dd>{new Date(reservation.upload_expires_at).toLocaleString()}</dd>
          </div>
        </dl>
        <p className="text-lavender/60">
          &gt; el envío del audio (streaming) llega en la siguiente entrega; aún no se ha
          transferido ningún byte.
        </p>
        <div>
          <Button type="button" variant="secondary" onClick={reset}>
            subir otra clase
          </Button>
        </div>
      </div>
    );
  }

  if (subjects.length === 0) {
    return (
      <EmptyState
        title="aún no tienes materias"
        description="crea la primera para poder reservar una clase."
        action={
          <Link
            to="/materias"
            className="lowercase font-pixel border-2 border-mint px-3 py-2 text-mint hover:bg-mint hover:text-on-accent"
          >
            crear materia
          </Link>
        }
      />
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
      <div className="lowercase flex flex-col gap-1 border-2 border-lavender/40 px-3 py-2 text-lavender/70">
        {usable ? (
          <p>
            &gt; límites publicados · máx {formatBytes(capabilities?.max_upload_bytes ?? null)} ·{" "}
            {capabilities?.max_duration_seconds ?? "?"} s ·{" "}
            {(capabilities?.accepted_input_formats ?? []).join(", ") || "sin formatos"} ·{" "}
            {capabilities?.active_slots ?? "?"} slot(s)
          </p>
        ) : (
          <p role="status" className="text-mint">
            &gt; sin límites publicados · la subida aún no está disponible; no se aceptarán
            archivos hasta que el spike publique sus límites.
          </p>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <label className="lowercase font-pixel flex flex-col gap-1">
            <span className="text-mint">&gt; materia</span>
            <select
              className={selectClass}
              value={draft.subjectId}
              onChange={(event) => update("subjectId", event.target.value)}
            >
              <option value="" disabled>
                selecciona materia
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

        <div className="flex flex-col gap-1">
          <DateInput
            label="fecha de clase"
            value={draft.classDate}
            onChange={(event) => update("classDate", event.target.value)}
          />
          {errors.fields.classDate != null && (
            <span className="lowercase text-mint">{errors.fields.classDate}</span>
          )}
        </div>

        <div className="flex flex-col gap-1">
          <TimezoneSelect
            label="zona horaria"
            value={draft.classTimezone}
            onChange={(event) => update("classTimezone", event.target.value)}
          />
          {errors.fields.classTimezone != null && (
            <span className="lowercase text-mint">{errors.fields.classTimezone}</span>
          )}
        </div>

        <div className="flex flex-col gap-1">
          <LanguageSelect
            label="idioma del audio"
            options={languages}
            value={draft.languageCode}
            onChange={(event) => update("languageCode", event.target.value)}
          />
          {errors.fields.languageCode != null && (
            <span className="lowercase text-mint">{errors.fields.languageCode}</span>
          )}
        </div>

        <Input
          label="título (opcional)"
          placeholder="clase 03 — integrales"
          value={draft.title}
          onChange={(event) => update("title", event.target.value)}
        />
        <Input
          label="profesor (opcional)"
          placeholder=""
          value={draft.teacher}
          onChange={(event) => update("teacher", event.target.value)}
        />
      </div>

      <div className="lowercase flex flex-col gap-2 border-2 border-mint px-4 py-3">
        <p className="text-mint">
          aviso de privacidad · versión {noticeVersion ?? "no publicada"}
        </p>
        <p className="text-lavender/70">
          el audio se envía a un proveedor en la nube para transcribirlo y se borra al terminar.
        </p>

        <label className="flex items-start gap-2 text-lavender">
          <input
            type="checkbox"
            checked={draft.cloudAccepted}
            onChange={(event) => update("cloudAccepted", event.target.checked)}
            className="mt-1 accent-mint"
          />
          <span>acepto el tratamiento en la nube para transcribir este audio</span>
        </label>

        <label className="flex items-start gap-2 text-lavender">
          <input
            type="checkbox"
            checked={draft.voiceAcknowledged}
            onChange={(event) => update("voiceAcknowledged", event.target.checked)}
            className="mt-1 accent-mint"
          />
          <span>reconozco que no habrá reproducción ni retranscripción del audio</span>
        </label>

        {errors.consent != null && (
          <p role="alert" className="text-mint">
            &gt; {errors.consent}
          </p>
        )}
      </div>

      <p className="lowercase text-lavender/60">
        &gt; audio no conservado · el audio se borra; solo se guarda el texto de la transcripción.
      </p>

      {errors.form != null && (
        <p role="alert" className="lowercase border-2 border-mint px-3 py-2 text-mint">
          &gt; {errors.form}
        </p>
      )}

      <div className="flex items-center gap-2">
        <Button type="submit" disabled={!canSubmit}>
          reservar subida
        </Button>
        {!usable ? (
          <span className="lowercase text-lavender/50">
            &gt; esperando límites publicados por el servidor
          </span>
        ) : (
          !consentReady && (
            <span className="lowercase text-lavender/50">
              &gt; acepta el consentimiento para continuar
            </span>
          )
        )}
      </div>
    </form>
  );
}
