// D2.3 — Transporte de progreso: SSE con `EventSource` y sondeo como fallback acotado.
//
// El backend emite estado (snapshot + bytes/fragmentos + done|error). `EventSource` gestiona
// reconexión y manda `Last-Event-ID`, pero también puede quedarse reintentando sin fin: tras
// un número acotado de fallos se cierra y se activa el sondeo de `GET /audios/{id}`. Nunca
// hay dos transportes simultáneos, ni bucles sin límite, ni cancelación de la ingesta al
// cerrar la vista.

import { apiFetch } from "./api";
import { parseProgressEvent, snapshotFromDetail } from "./progress";
import type { ProgressEvent, ProgressSnapshot } from "./progress";

export type ProgressTransport = "sse" | "polling";

export interface ProgressSourceHandlers {
  onEvent: (event: ProgressEvent) => void;
  onTransport?: (transport: ProgressTransport) => void;
  /** El sondeo agotó sus reintentos: la UI debe ofrecer un reintento explícito. */
  onTransportError?: (message: string) => void;
}

/** Superficie mínima usada de `EventSource`, inyectable para pruebas deterministas. */
export interface ProgressEventSource {
  addEventListener(type: string, listener: (event: Event) => void): void;
  close(): void;
}

export interface ProgressSourceOptions {
  pollIntervalMs?: number;
  maxSseFailures?: number;
  maxPollFailures?: number;
  createEventSource?: (url: string) => ProgressEventSource;
  fetchSnapshot?: (audioId: string, signal: AbortSignal) => Promise<ProgressSnapshot | null>;
  setTimer?: (handler: () => void, ms: number) => ReturnType<typeof setTimeout>;
  clearTimer?: (handle: ReturnType<typeof setTimeout>) => void;
  random?: () => number;
}

export interface ProgressSource {
  close(): void;
  /** Activa el sondeo manualmente (fallback explícito de la UI). */
  usePolling(): void;
}

const DEFAULT_POLL_INTERVAL_MS = 2000;
const DEFAULT_MAX_SSE_FAILURES = 2;
const DEFAULT_MAX_POLL_FAILURES = 5;
const MAX_POLL_BACKOFF_MS = 30_000;
const MAX_JITTER_MS = 250;

export function progressStreamUrl(audioId: string): string {
  return `/audios/${encodeURIComponent(audioId)}/progress`;
}

async function fetchDetailSnapshot(
  audioId: string,
  signal: AbortSignal,
): Promise<ProgressSnapshot | null> {
  const res = await apiFetch(`/audios/${encodeURIComponent(audioId)}`, {
    cache: "no-store",
    signal,
  });
  return snapshotFromDetail((await res.json()) as unknown);
}

export function createProgressSource(
  audioId: string,
  handlers: ProgressSourceHandlers,
  options: ProgressSourceOptions = {},
): ProgressSource {
  const pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const maxSseFailures = options.maxSseFailures ?? DEFAULT_MAX_SSE_FAILURES;
  const maxPollFailures = options.maxPollFailures ?? DEFAULT_MAX_POLL_FAILURES;
  const fetchSnapshot = options.fetchSnapshot ?? fetchDetailSnapshot;
  const setTimer = options.setTimer ?? ((handler, ms) => setTimeout(handler, ms));
  const clearTimer = options.clearTimer ?? ((handle) => clearTimeout(handle));
  const random = options.random ?? Math.random;

  let closed = false;
  let terminal = false;
  let mode: ProgressTransport = "sse";
  let sseFailures = 0;
  let pollFailures = 0;
  let source: ProgressEventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let controller: AbortController | null = null;

  function stopTimer(): void {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
  }

  function close(): void {
    if (closed) return;
    closed = true;
    stopTimer();
    controller?.abort();
    controller = null;
    source?.close();
    source = null;
  }

  function emit(event: ProgressEvent): void {
    if (closed || terminal) return;
    if (event.type === "done" || event.type === "error") {
      terminal = true;
      handlers.onEvent(event);
      close();
      return;
    }
    handlers.onEvent(event);
  }

  function schedulePoll(): void {
    stopTimer();
    const exponent = Math.min(pollFailures, 4);
    const delay =
      Math.min(pollIntervalMs * 2 ** exponent, MAX_POLL_BACKOFF_MS) +
      Math.floor(random() * MAX_JITTER_MS);
    timer = setTimer(() => {
      timer = null;
      void poll();
    }, delay);
  }

  async function poll(): Promise<void> {
    if (closed || terminal || mode !== "polling") return;
    const current = new AbortController();
    controller = current;
    let snapshot: ProgressSnapshot | null = null;
    try {
      snapshot = await fetchSnapshot(audioId, current.signal);
    } catch {
      if (current.signal.aborted || closed) return;
      snapshot = null;
    }
    if (controller === current) controller = null;
    if (closed || terminal) return;

    if (snapshot === null) {
      pollFailures += 1;
      if (pollFailures >= maxPollFailures) {
        handlers.onTransportError?.(
          "no se pudo consultar el progreso; reintenta cuando recuperes la conexión.",
        );
        return;
      }
      schedulePoll();
      return;
    }

    pollFailures = 0;
    emit({ type: "snapshot", snapshot });
    if (closed || terminal) return;

    if (snapshot.aggregate === "failed") {
      emit({
        type: "error",
        code: "aggregate_failed",
        detail: snapshot.attempt.status,
      });
      return;
    }
    if (snapshot.aggregate === "ready" || snapshot.aggregate === "partial") {
      emit({
        type: "done",
        aggregate: snapshot.aggregate,
        stages: snapshot.stages,
      });
      return;
    }
    schedulePoll();
  }

  function startPolling(): void {
    if (closed || terminal || mode === "polling") return;
    mode = "polling";
    source?.close();
    source = null;
    stopTimer();
    handlers.onTransport?.("polling");
    void poll();
  }

  function listen(name: string): void {
    source?.addEventListener(name, (raw) => {
      if (closed || terminal || mode !== "sse") return;
      const event = parseProgressEvent(name, (raw as MessageEvent<string>).data);
      if (event === null) return;
      if (name === "snapshot" || name === "progress") sseFailures = 0;
      emit(event);
    });
  }

  function startSse(): void {
    if (closed || terminal) return;
    const create =
      options.createEventSource ??
      ((url: string) => new EventSource(url, { withCredentials: true }));
    if (typeof EventSource === "undefined" && options.createEventSource === undefined) {
      startPolling();
      return;
    }
    try {
      source = create(progressStreamUrl(audioId));
    } catch {
      startPolling();
      return;
    }
    mode = "sse";
    handlers.onTransport?.("sse");
    source.addEventListener("open", () => {
      if (closed || terminal || mode !== "sse") return;
      sseFailures = 0;
    });
    for (const name of ["snapshot", "progress", "done", "error"]) listen(name);
    source.addEventListener("error", () => {
      if (closed || terminal || mode !== "sse") return;
      sseFailures += 1;
      if (sseFailures >= maxSseFailures) startPolling();
    });
  }

  startSse();

  return {
    close,
    usePolling: startPolling,
  };
}
