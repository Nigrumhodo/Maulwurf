import { Link } from "react-router-dom";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { fetchAudios } from "@/lib/audios";
import type { AudioListItemOut } from "@/lib/audios";
import { useServerList } from "@/lib/useServerList";

function AudioRow({ audio }: { audio: AudioListItemOut }) {
  const title = audio.title ?? "clase sin título";
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3">
      <div className="lowercase flex min-w-0 flex-col">
        <span className="flex items-center gap-2 text-lavender">
          <span
            aria-hidden="true"
            className="inline-block h-3 w-3 shrink-0 border border-pure-black"
            style={{ backgroundColor: audio.subject.color }}
          />
          <span>{audio.subject.name}</span>
        </span>
        <h3 className="text-lg text-lavender">{title}</h3>
        <p className="text-base text-lavender/60">
          {audio.class_date} · {audio.language_code}
          {audio.duration_seconds !== null ? ` · ${audio.duration_seconds} s` : ""}
        </p>
      </div>
      <div className="flex items-center gap-3">
        <StatusBadge status={audio.aggregate_status} />
        <Link
          to="/gestion/biblioteca"
          className={buttonClasses("ghost", "sm", "min-h-8 border-lavender/30")}
        >
          ver en la biblioteca
        </Link>
      </div>
    </li>
  );
}

// Recent classes from `GET /audios`. No invented percentages: the list has no progress ratio.
export function ActivityFeed() {
  const { items, loading, error } = useServerList(fetchAudios);

  return (
    <div className="flex flex-col gap-3">
      {loading ? (
        <p className="lowercase text-base text-lavender/60">&gt; cargando…</p>
      ) : error !== null ? (
        <p role="alert" className="lowercase text-base text-mint">
          &gt; {error}
        </p>
      ) : items.length === 0 ? (
        <p className="lowercase border-2 border-dashed border-lavender/50 px-3 py-6 text-center text-base text-lavender">
          aún no hay clases
        </p>
      ) : (
        <ul className="divide-y-2 divide-lavender/20 border-y-2 border-lavender/20">
          {items.map((audio) => (
            <AudioRow key={audio.id} audio={audio} />
          ))}
        </ul>
      )}

      <Link
        to="/gestion/biblioteca"
        className={buttonClasses("secondary", "sm", "min-h-10 self-start")}
      >
        ir a la biblioteca →
      </Link>
    </div>
  );
}
