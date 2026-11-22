import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { formatHitTimestamp, searchHitHref, searchTranscripts } from "@/lib/search";
import type { SearchHit } from "@/lib/search";

// D4.3 — Global Ctrl+K / Cmd+K Command Palette with quick routes + hybrid transcript search.

interface QuickRouteItem {
  id: string;
  label: string;
  to: string;
  hint: string;
}

const QUICK_ROUTES: QuickRouteItem[] = [
  { id: "route-home", label: "inicio · dashboard", to: "/", hint: "resumen y estado" },
  { id: "route-upload", label: "subir clase", to: "/gestion/subir", hint: "ingesta efímera" },
  { id: "route-library", label: "biblioteca de clases", to: "/gestion/biblioteca", hint: "transcripciones" },
  { id: "route-chat", label: "chat rag citado", to: "/gestion/chat", hint: "preguntas con evidencia" },
  { id: "route-subjects", label: "materias", to: "/gestion/materias", hint: "crud y estadísticas" },
  { id: "route-tasks", label: "tareas por confirmar", to: "/gestion/tareas", hint: "bandeja m4" },
  { id: "route-agenda", label: "agenda", to: "/gestion/agenda", hint: "calendario integrado" },
  { id: "route-settings", label: "ajustes", to: "/settings", hint: "zona horaria, gmail y cuenta" },
];

type PaletteOption =
  | { kind: "route"; item: QuickRouteItem }
  | { kind: "hit"; hit: SearchHit };

export interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    function onWindowKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onOpenChange(!open);
      } else if (event.key === "Escape" && open) {
        event.preventDefault();
        onOpenChange(false);
      }
    }
    window.addEventListener("keydown", onWindowKeyDown);
    return () => window.removeEventListener("keydown", onWindowKeyDown);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setHits([]);
      setError(null);
      setActiveIndex(0);
      return;
    }
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  const trimmed = query.trim();

  useEffect(() => {
    if (!open || trimmed.length < 2) {
      setHits([]);
      setLoading(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true);
      setError(null);
      searchTranscripts(trimmed, { limit: 8, signal: controller.signal })
        .then((response) => {
          setHits(response.results);
          setLoading(false);
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          if (err instanceof DOMException && err.name === "AbortError") return;
          setHits([]);
          setError("no se pudo completar la búsqueda.");
          setLoading(false);
        });
    }, 180);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [open, trimmed]);

  const filteredRoutes = useMemo(() => {
    const lower = trimmed.toLowerCase();
    if (lower === "") return QUICK_ROUTES;
    return QUICK_ROUTES.filter(
      (route) =>
        route.label.toLowerCase().includes(lower) || route.hint.toLowerCase().includes(lower),
    );
  }, [trimmed]);

  const options = useMemo<PaletteOption[]>(() => {
    const list: PaletteOption[] = hits.map((hit) => ({ kind: "hit", hit }));
    for (const item of filteredRoutes) {
      list.push({ kind: "route", item });
    }
    return list;
  }, [hits, filteredRoutes]);

  useEffect(() => {
    setActiveIndex(0);
  }, [options.length]);

  function selectOption(option: PaletteOption): void {
    onOpenChange(false);
    if (option.kind === "route") {
      navigate(option.item.to);
    } else {
      navigate(searchHitHref(option.hit));
    }
  }

  function handleInputKeyDown(event: React.KeyboardEvent<HTMLInputElement>): void {
    if (options.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((current) => (current + 1) % options.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) => (current - 1 + options.length) % options.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const selected = options[activeIndex];
      if (selected !== undefined) selectOption(selected);
    }
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[90] flex items-start justify-center bg-black/60 p-4 pt-16"
      role="dialog"
      aria-modal="true"
      aria-label="paleta de comandos y búsqueda global"
      onClick={(event) => {
        if (event.target === event.currentTarget) onOpenChange(false);
      }}
    >
      <div className="lowercase font-pixel flex w-full max-w-2xl flex-col border-2 border-pure-black bg-surface shadow-block">
        <header className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-3 py-2 text-on-accent">
          <span className="capitalize font-bold">› búsqueda rápida (ctrl+k)</span>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label="cerrar paleta"
            className="inline-flex min-h-8 min-w-8 items-center justify-center border-2 border-on-accent/40 px-1.5 leading-none hover:bg-on-accent hover:text-mint"
          >
            esc
          </button>
        </header>

        <div className="border-b-2 border-pure-black p-3">
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={handleInputKeyDown}
            placeholder="escribe para buscar en transcripciones o ir a una sección…"
            aria-label="buscar en maulwurf"
            className="w-full border-2 border-lavender bg-dark px-3 py-2 text-lg text-lavender caret-mint focus:border-mint focus:outline-none"
          />
        </div>

        <div className="max-h-96 overflow-y-auto p-3">
          {loading && <p className="mb-2 text-lavender/60">&gt; buscando en tus clases…</p>}
          {error !== null && (
            <p role="alert" className="mb-2 text-mint">
              &gt; {error}
            </p>
          )}
          {trimmed.length >= 2 && !loading && error === null && hits.length === 0 && (
            <p className="mb-2 text-lavender/60">sin coincidencias en transcripciones para «{trimmed}».</p>
          )}

          {options.length === 0 ? (
            <p className="text-lavender/60">sin resultados.</p>
          ) : (
            <ul className="flex flex-col gap-1.5" role="listbox" aria-label="resultados">
              {options.map((option, index) => {
                const selected = index === activeIndex;
                if (option.kind === "hit") {
                  const { hit } = option;
                  return (
                    <li key={`hit-${hit.chunk_id}`} role="option" aria-selected={selected}>
                      <button
                        type="button"
                        onClick={() => selectOption(option)}
                        className={`flex w-full flex-col gap-1 border-2 px-3 py-2 text-left ${
                          selected
                            ? "border-mint bg-mint/15 text-mint"
                            : "border-pure-black bg-dark text-lavender hover:border-mint"
                        }`}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                          <span className="inline-flex items-center gap-1.5 text-mint">
                            <span
                              aria-hidden="true"
                              className="inline-block h-2.5 w-2.5"
                              style={{ backgroundColor: hit.subject.color }}
                            />
                            {hit.subject.name} · {hit.class_date}
                          </span>
                          <span className="text-lavender/60">{formatHitTimestamp(hit.t_start)}</span>
                        </div>
                        <p className="line-clamp-2 text-sm text-lavender">{hit.snippet}</p>
                      </button>
                    </li>
                  );
                }
                const { item } = option;
                return (
                  <li key={item.id} role="option" aria-selected={selected}>
                    <button
                      type="button"
                      onClick={() => selectOption(option)}
                      className={`flex w-full items-center justify-between border-2 px-3 py-1.5 text-left ${
                        selected
                          ? "border-mint bg-mint/15 text-mint"
                          : "border-pure-black bg-dark text-lavender hover:border-mint"
                      }`}
                    >
                      <span>› {item.label}</span>
                      <span className="text-xs text-lavender/60">{item.hint}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
