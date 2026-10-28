import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/StatusBadge";

// D1.7 — Borrador estático de la biblioteca (sin wiring) para validar UX temprano
// y alimentar D2.4: listado por materia con estado agregado + estados por etapa,
// aviso permanente «audio no conservado» y botones diferenciados («volver a
// subir» ≠ «reintentar análisis/índice»). Los datos son de ejemplo.
interface MockAudio {
  id: string;
  subject: string;
  title: string;
  aggregate: string;
  attempt: string;
  index: string;
  analyze: string;
  hasTranscript: boolean;
}

const MOCK: MockAudio[] = [
  {
    id: "a1",
    subject: "cálculo i",
    title: "clase 03 — integrales",
    aggregate: "ready",
    attempt: "succeeded",
    index: "succeeded",
    analyze: "not_requested",
    hasTranscript: true,
  },
  {
    id: "a2",
    subject: "física ii",
    title: "clase 07 — termodinámica",
    aggregate: "processing",
    attempt: "transcribing",
    index: "running",
    analyze: "not_requested",
    hasTranscript: true,
  },
  {
    id: "a3",
    subject: "química",
    title: "clase 02 — enlaces",
    aggregate: "partial",
    attempt: "succeeded",
    index: "succeeded",
    analyze: "failed",
    hasTranscript: true,
  },
  {
    id: "a4",
    subject: "historia",
    title: "clase 01 — revolución industrial",
    aggregate: "failed",
    attempt: "requires_reupload",
    index: "failed",
    analyze: "not_requested",
    hasTranscript: false,
  },
  {
    id: "a5",
    subject: "álgebra",
    title: "clase 05 — espacios vectoriales",
    aggregate: "processing",
    attempt: "transcript_committed_cleanup_pending",
    index: "not_requested",
    analyze: "not_requested",
    hasTranscript: true,
  },
];

export function BibliotecaPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">biblioteca</h1>

      <Card title="tus clases">
        <p className="lowercase border-2 border-mint px-3 py-2 text-mint">
          &gt; audio no conservado · el audio se borra tras transcribir; solo se guarda el texto.
        </p>

        <p className="mt-3 lowercase text-lavender/50">
          &gt; maqueta estática (d1.7) · datos de ejemplo
        </p>

        <ul className="mt-3 flex flex-col gap-2">
          {MOCK.map((audio) => (
            <li key={audio.id} className="lowercase flex flex-col gap-2 border-2 border-pure-black px-3 py-2">
              <div className="flex items-center gap-3">
                <span className="flex-1 text-lavender">
                  {audio.subject} · {audio.title}
                </span>
                <StatusBadge status={audio.aggregate} />
              </div>
              <div className="flex items-center gap-2 text-lavender/60">
                <span>audio:</span>
                <StatusBadge status={audio.attempt} />
                <span>index:</span>
                <StatusBadge status={audio.index} />
                <span>análisis:</span>
                <StatusBadge status={audio.analyze} />
              </div>
              <div className="flex items-center gap-2">
                {audio.attempt === "transcript_committed_cleanup_pending" ? (
                  <>
                    <Button size="sm" variant="secondary" disabled>
                      abrir transcripción
                    </Button>
                    <span className="text-lavender/60">
                      limpieza pendiente · indexación bloqueada hasta verificar el borrado
                    </span>
                  </>
                ) : audio.hasTranscript ? (
                  <>
                    <Button size="sm" variant="secondary" disabled>
                      abrir transcripción
                    </Button>
                    <Button size="sm" variant="secondary" disabled>
                      reintentar análisis/índice
                    </Button>
                  </>
                ) : (
                  <Button size="sm" variant="secondary" disabled>
                    volver a subir
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
