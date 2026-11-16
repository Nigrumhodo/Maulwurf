import { Link } from "react-router-dom";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { fetchAudios } from "@/lib/audios";
import type { AudioListItemOut } from "@/lib/audios";
import { useServerList } from "@/lib/useServerList";

function AudioCard({ audio }: { audio: AudioListItemOut }) {
  const title = audio.title ?? "clase sin título";
  return (
    <article className="flex flex-col gap-2 border-2 border-pure-black bg-surface p-3 shadow-block hover:border-mint">
      <div className="flex items-center justify-between border-b-2 border-lavender/15 pb-2">
        <div className="lowercase flex items-center gap-2 text-lavender">
          <span
            aria-hidden="true"
            className="inline-block h-4 w-4 border border-pure-black"
            style={{ backgroundColor: audio.subject.color }}
          />
          <span>{audio.subject.name}</span>
        </div>
        <StatusBadge status={audio.aggregate_status} />
      </div>
      <div>
        <h3 className="lowercase text-lavender">{title}</h3>
        <p className="lowercase mt-0.5 text-lavender/50">
          {audio.class_date} · {audio.language_code}
          {audio.duration_seconds !== null ? ` · ${audio.duration_seconds} s` : ""}
        </p>
      </div>
      <div className="flex items-center justify-between pt-1">
        <Link to="/gestion/biblioteca" className={buttonClasses("secondary", "sm")}>
          ver en la biblioteca
        </Link>
      </div>
    </article>
  );
}

// Recent classes from `GET /audios`. No invented percentages: the list has no progress ratio.
export function ActivityFeed() {
  const { items, loading, error } = useServerList(fetchAudios);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between border-2 border-pure-black bg-surface px-3 py-2 shadow-block">
        <div className="lowercase flex items-center gap-2 text-lavender">
          <span className="h-2.5 w-2.5 border border-pure-black bg-mint" aria-hidden="true" />
          <span className="font-bold tracking-wider">02 · radar de actividad // clases recientes</span>
        </div>
      </div>

      {loading ? (
        <p className="lowercase text-lavender/60">&gt; cargando…</p>
      ) : error !== null ? (
        <p role="alert" className="lowercase text-mint">
          &gt; {error}
        </p>
      ) : items.length === 0 ? (
        <p className="lowercase border-2 border-dashed border-lavender/50 px-3 py-6 text-center text-lavender">
          aún no hay clases
        </p>
      ) : (
        items.map((audio) => <AudioCard key={audio.id} audio={audio} />)
      )}

      <Link
        to="/gestion/biblioteca"
        className="lowercase flex items-center justify-center gap-2 border-2 border-pure-black bg-mint p-3 text-on-accent shadow-block hover:bg-surface hover:text-mint"
      >
        ir a la biblioteca →
      </Link>
    </section>
  );
}
