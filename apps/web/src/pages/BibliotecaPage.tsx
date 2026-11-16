import { Link } from "react-router-dom";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { fetchAudios } from "@/lib/audios";
import type { AudioListItemOut } from "@/lib/audios";
import { useServerList } from "@/lib/useServerList";

function stageLabel(audio: AudioListItemOut, stage: string): string {
  return audio.stages[stage] ?? "sin etapa";
}

function AudioRow({ audio }: { audio: AudioListItemOut }) {
  const title = audio.title ?? "clase sin título";
  const fragments =
    audio.attempt?.fragments_total != null
      ? `${audio.attempt.fragments_done ?? 0}/${audio.attempt.fragments_total} fragmentos`
      : null;

  return (
    <li className="lowercase flex flex-col gap-2 border-2 border-pure-black px-3 py-2">
      <div className="flex items-center gap-3">
        <span
          aria-hidden="true"
          className="inline-block h-4 w-4 shrink-0 border border-pure-black"
          style={{ backgroundColor: audio.subject.color }}
        />
        <span className="flex-1 text-lavender">
          {audio.subject.name} · {title}
        </span>
        <StatusBadge status={audio.aggregate_status} />
      </div>
      <p className="text-lavender/60">
        {audio.class_date} · {audio.language_code}
        {audio.duration_seconds !== null ? ` · ${audio.duration_seconds} s` : ""}
      </p>
      <div className="flex flex-wrap items-center gap-2 text-lavender/60">
        <span>audio:</span>
        <StatusBadge status={audio.attempt?.status ?? "sin intento"} />
        <span>limpieza:</span>
        <StatusBadge status={audio.attempt?.cleanup_status ?? "sin intento"} />
        <span>index:</span>
        <StatusBadge status={stageLabel(audio, "index")} />
        <span>análisis:</span>
        <StatusBadge status={stageLabel(audio, "analyze")} />
      </div>
      {audio.attempt !== null && (
        <p className="text-lavender/60">
          {audio.attempt.received_bytes} B{fragments !== null ? ` · ${fragments}` : ""}
        </p>
      )}
      {audio.attempt?.status === "requires_reupload" && (
        <Link to="/gestion/subir" className="text-mint hover:text-lavender">
          volver a subir
        </Link>
      )}
    </li>
  );
}

// Library list from `GET /audios` (`AudioListItemOut`): subject, attempt, stages, aggregate.
export function BibliotecaPage() {
  const { items, loading, error } = useServerList(fetchAudios);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">biblioteca</h1>

      <Card title="tus clases">
        <p className="lowercase border-2 border-mint px-3 py-2 text-mint">
          &gt; audio no conservado · el audio se borra tras transcribir; solo se guarda el texto.
        </p>

        {loading ? (
          <p className="mt-3 lowercase text-lavender/60">&gt; cargando…</p>
        ) : error !== null ? (
          <p role="alert" className="mt-3 lowercase text-mint">
            &gt; {error}
          </p>
        ) : items.length === 0 ? (
          <div className="mt-3">
            <EmptyState
              title="aún no hay clases"
              description="reserva una clase para ver su estado aquí."
              action={
                <Link to="/gestion/subir" className="text-mint hover:text-lavender">
                  subir clase
                </Link>
              }
            />
          </div>
        ) : (
          <ul className="mt-3 flex flex-col gap-2">
            {items.map((audio) => (
              <AudioRow key={audio.id} audio={audio} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
