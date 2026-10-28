import { useState } from "react";
import type { FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { DateInput } from "@/components/ui/DateInput";
import { Input } from "@/components/ui/Input";
import { LanguageSelect } from "@/components/ui/LanguageSelect";
import { TimezoneSelect } from "@/components/ui/TimezoneSelect";

// D1.7 — Borrador estático del formulario de subida (sin wiring) para validar UX
// temprano y alimentar D2.1: materia, fecha de clase, zona horaria e idioma SIN
// preselección, título/profesor opcionales, consentimiento versionado visible con
// DOS acknowledgements obligatorios y el aviso permanente «audio no conservado».
// No envía bytes; el alta real (`POST /audios` + `PUT`) llega en D2.1/D2.2.
const PRIVACY_NOTICE_VERSION = "2026-09-19";

const selectClass =
  "bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint focus:border-mint focus:outline-none";

export function UploadForm() {
  const [cloudAccepted, setCloudAccepted] = useState(false);
  const [voiceAcknowledged, setVoiceAcknowledged] = useState(false);
  const [showError, setShowError] = useState(false);
  const consentReady = cloudAccepted && voiceAcknowledged;

  function handleSubmit(event: FormEvent) {
    // Sin wiring en D1.7: solo se impide el envío nativo; el alta real es D2.1.
    event.preventDefault();
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="lowercase font-pixel flex flex-col gap-1">
          <span className="text-mint">&gt; materia</span>
          <select className={selectClass} defaultValue="" disabled>
            <option value="" disabled>
              crea una materia primero
            </option>
          </select>
        </label>
        <DateInput label="fecha de clase" />
        <TimezoneSelect label="zona horaria" />
        <LanguageSelect label="idioma del audio" />
        <Input label="título (opcional)" placeholder="clase 03 — integrales" />
        <Input label="profesor (opcional)" placeholder="" />
      </div>

      <div className="lowercase flex flex-col gap-2 border-2 border-mint px-4 py-3">
        <p className="text-mint">aviso de privacidad · versión {PRIVACY_NOTICE_VERSION}</p>
        <p className="text-lavender/70">
          el audio se envía a un proveedor en la nube para transcribirlo y se borra al terminar.
        </p>

        <label className="flex items-start gap-2 text-lavender">
          <input
            type="checkbox"
            checked={cloudAccepted}
            onChange={(event) => setCloudAccepted(event.target.checked)}
            className="mt-1 accent-mint"
          />
          <span>acepto el tratamiento en la nube para transcribir este audio</span>
        </label>

        <label className="flex items-start gap-2 text-lavender">
          <input
            type="checkbox"
            checked={voiceAcknowledged}
            onChange={(event) => setVoiceAcknowledged(event.target.checked)}
            className="mt-1 accent-mint"
          />
          <span>reconozco que no habrá reproducción ni retranscripción del audio</span>
        </label>
      </div>

      <p className="lowercase text-lavender/60">
        &gt; audio no conservado · el audio se borra; solo se guarda el texto de la transcripción.
      </p>

      <div className="lowercase flex flex-col items-start gap-2">
        <button
          type="button"
          onClick={() => setShowError((value) => !value)}
          className="text-lavender/60 underline hover:text-mint"
        >
          {showError ? "ocultar estado de error (maqueta)" : "ver estado de error (maqueta)"}
        </button>
        {showError && (
          <div role="alert" className="flex flex-col gap-1 border-2 border-mint px-3 py-2 text-mint">
            <span>&gt; archivo rechazado · formato no admitido</span>
            <span className="text-lavender/60">
              sube un wav, mp3, ogg, opus, flac o webm; la app no guarda el archivo
            </span>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button type="submit" disabled={!consentReady}>
          continuar
        </Button>
        {!consentReady && (
          <span className="lowercase text-lavender/50">
            &gt; acepta el consentimiento para continuar
          </span>
        )}
      </div>
    </form>
  );
}
