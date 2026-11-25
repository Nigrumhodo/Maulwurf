import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { formatHitTimestamp, searchHitHref, searchTranscripts } from "@/lib/search";
import type { SearchHit } from "@/lib/search";
import type esCommon from "@/locales/es/common.json";

// D4.3 — Global Ctrl+K / Cmd+K Command Palette with quick routes + hybrid transcript search.

type RouteKey = keyof (typeof esCommon)["palette"]["routes"];

interface QuickRouteItem {
  id: string;
  label: string;
  to: string;
  hint: string;
}

const QUICK_ROUTES: ReadonlyArray<{ key: RouteKey; to: string }> = [
  { key: "upload", to: "/gestion/subir" },
  { key: "home", to: "/" },
  { key: "gestion", to: "/gestion" },
  { key: "library", to: "/gestion/biblioteca" },
  { key: "chat", to: "/gestion/chat" },
  { key: "subjects", to: "/gestion/materias" },
  { key: "tasks", to: "/gestion/tareas" },
  { key: "agenda", to: "/gestion/agenda" },
  { key: "settings", to: "/settings" },
];

type PaletteOption =
  | { kind: "route"; item: QuickRouteItem }
  | { kind: "hit"; hit: SearchHit };

export interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  // A flag, not a message: the copy is resolved at render time so it follows the UI language.
  const [failed, setFailed] = useState(false);
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
      setFailed(false);
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
      setFailed(false);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true);
      setFailed(false);
      searchTranscripts(trimmed, { limit: 8, signal: controller.signal })
        .then((response) => {
          setHits(response.results);
          setLoading(false);
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          if (err instanceof DOMException && err.name === "AbortError") return;
          setHits([]);
          setFailed(true);
          setLoading(false);
        });
    }, 180);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [open, trimmed]);

  // Labels and hints are matched in the active UI language, so the filter follows the switch.
  const quickRoutes = useMemo<QuickRouteItem[]>(
    () =>
      QUICK_ROUTES.map(({ key, to }) => ({
        id: `route-${key}`,
        to,
        label: t(`palette.routes.${key}.label`),
        hint: t(`palette.routes.${key}.hint`),
      })),
    [t],
  );

  const filteredRoutes = useMemo(() => {
    const lower = trimmed.toLowerCase();
    if (lower === "") return quickRoutes;
    return quickRoutes.filter(
      (route) =>
        route.label.toLowerCase().includes(lower) || route.hint.toLowerCase().includes(lower),
    );
  }, [trimmed, quickRoutes]);

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
      aria-label={t("palette.dialog")}
      onClick={(event) => {
        if (event.target === event.currentTarget) onOpenChange(false);
      }}
    >
      <div className="lowercase font-pixel flex w-full max-w-2xl flex-col border-2 border-pure-black bg-surface shadow-block">
        <header className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-3 py-2 text-on-accent">
          <span className="capitalize font-bold">› {t("palette.title")}</span>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label={t("palette.close")}
            className="inline-flex min-h-8 min-w-8 items-center justify-center border-2 border-on-accent/40 px-1.5 leading-none hover:bg-on-accent hover:text-mint"
          >
            {t("palette.closeKey")}
          </button>
        </header>

        <div className="border-b-2 border-pure-black p-3">
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={handleInputKeyDown}
            placeholder={t("palette.placeholder")}
            aria-label={t("palette.input")}
            className="w-full border-2 border-lavender bg-dark px-3 py-2 text-lg text-lavender caret-mint focus:border-mint focus:outline-none"
          />
        </div>

        <div className="max-h-96 overflow-y-auto p-3">
          {loading && <p className="mb-2 text-lavender/60">&gt; {t("palette.searching")}</p>}
          {failed && (
            <p role="alert" className="mb-2 text-mint">
              &gt; {t("palette.error")}
            </p>
          )}
          {trimmed.length >= 2 && !loading && !failed && hits.length === 0 && (
            <p className="mb-2 text-lavender/60">{t("palette.noHits", { query: trimmed })}</p>
          )}

          {options.length === 0 ? (
            <p className="text-lavender/60">{t("palette.noResults")}</p>
          ) : (
            <ul className="flex flex-col gap-1.5" role="listbox" aria-label={t("palette.results")}>
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
                      <span className="text-base text-lavender/60">{item.hint}</span>
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
