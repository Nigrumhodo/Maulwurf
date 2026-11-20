// D2.3 — Progreso honesto (U-S2-DA-03).
//
// El servidor A2.9 emite ESTADO, no incrementos: cada conexión/reconexión recibe un
// `snapshot` fresco desde la BD, luego `progress` solo con bytes y fragmentos medidos y,
// al final, exactamente un `done` o `error`. Aquí no se calcula ningún porcentaje ni ETA:
// solo se valida/normaliza el payload y se reduce a lo que la UI puede afirmar.

export type ProgressAggregate = "processing" | "ready" | "partial" | "failed";

export interface ProgressAttempt {
  status: string | null;
  received_bytes: number;
  fragments_done: number | null;
  fragments_total: number | null;
}

export interface ProgressStages {
  index: string;
  analyze: string;
}

export interface ProgressSnapshot {
  attempt: ProgressAttempt;
  stages: ProgressStages;
  aggregate: ProgressAggregate;
}

export interface ProgressTick {
  received_bytes: number;
  fragments_done: number | null;
  fragments_total: number | null;
}

export type ProgressEvent =
  | { type: "snapshot"; snapshot: ProgressSnapshot }
  | { type: "progress"; tick: ProgressTick }
  | { type: "done"; aggregate: ProgressAggregate; stages: ProgressStages }
  | { type: "error"; code: string; detail: string | null };

export interface ProgressError {
  code: string;
  detail: string | null;
}

export interface ProgressState {
  snapshot: ProgressSnapshot | null;
  terminal: "done" | "error" | null;
  /** Agregado del frame `done` cuando aún no había `snapshot` (no debería ocurrir). */
  terminalAggregate: ProgressAggregate | null;
  error: ProgressError | null;
}

export const INITIAL_PROGRESS: ProgressState = {
  snapshot: null,
  terminal: null,
  terminalAggregate: null,
  error: null,
};

const AGGREGATES: readonly ProgressAggregate[] = ["processing", "ready", "partial", "failed"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function aggregateOf(value: unknown): ProgressAggregate | null {
  return typeof value === "string" && (AGGREGATES as readonly string[]).includes(value)
    ? (value as ProgressAggregate)
    : null;
}

function nonNegativeInt(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function countOrNull(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  return nonNegativeInt(value);
}

function stagesOf(value: unknown): ProgressStages | null {
  if (!isRecord(value)) return null;
  const index = value.index;
  const analyze = value.analyze;
  if (typeof index !== "string" || typeof analyze !== "string") return null;
  return { index, analyze };
}

function attemptOf(value: unknown): ProgressAttempt | null {
  if (!isRecord(value)) return null;
  const status = value.status;
  if (status !== null && typeof status !== "string") return null;
  const received = nonNegativeInt(value.received_bytes);
  if (received === null) return null;
  return {
    status,
    received_bytes: received,
    fragments_done: countOrNull(value.fragments_done),
    fragments_total: countOrNull(value.fragments_total),
  };
}

function emptyAttempt(): ProgressAttempt {
  // Mismo valor que el backend cuando la clase aún no tiene intento (progress_snapshot).
  return { status: null, received_bytes: 0, fragments_done: null, fragments_total: null };
}

function parseJson(data: string): unknown {
  try {
    return JSON.parse(data) as unknown;
  } catch {
    return null;
  }
}

/** Valida un frame SSE nombrado; un payload malformado se ignora en vez de inventar estado. */
export function parseProgressEvent(name: string, data: string): ProgressEvent | null {
  // Un latido/comentario no llega como evento nombrado, pero se tolera cualquier nombre ajeno.
  if (!["snapshot", "progress", "done", "error"].includes(name)) return null;
  const payload = parseJson(data);
  if (!isRecord(payload)) return null;

  if (name === "snapshot") {
    const attempt = attemptOf(payload.attempt);
    const stages = stagesOf(payload.stages);
    const aggregate = aggregateOf(payload.aggregate);
    if (attempt === null || stages === null || aggregate === null) return null;
    return { type: "snapshot", snapshot: { attempt, stages, aggregate } };
  }

  if (name === "progress") {
    const received = nonNegativeInt(payload.received_bytes);
    if (received === null) return null;
    return {
      type: "progress",
      tick: {
        received_bytes: received,
        fragments_done: countOrNull(payload.fragments_done),
        fragments_total: countOrNull(payload.fragments_total),
      },
    };
  }

  if (name === "done") {
    const stages = stagesOf(payload.stages);
    const aggregate = aggregateOf(payload.aggregate);
    // `done` solo cierra con ready|partial; `failed` siempre viaja como `error`.
    if (stages === null || (aggregate !== "ready" && aggregate !== "partial")) return null;
    return { type: "done", aggregate, stages };
  }

  const code = payload.code;
  if (typeof code !== "string" || code === "") return null;
  const detail = typeof payload.detail === "string" ? payload.detail : null;
  return { type: "error", code, detail };
}

/** `GET /audios/{id}` es el fallback de sondeo; `attempt` puede ser nulo. */
export function snapshotFromDetail(value: unknown): ProgressSnapshot | null {
  if (!isRecord(value)) return null;
  const stages = stagesOf(value.stages);
  const aggregate = aggregateOf(value.aggregate_status);
  if (stages === null || aggregate === null) return null;
  const attempt = value.attempt === null ? emptyAttempt() : attemptOf(value.attempt);
  if (attempt === null) return null;
  return { attempt, stages, aggregate };
}

/**
 * Reduce eventos a estado visible. Un `snapshot` reemplaza lo anterior (nunca suma), un
 * terminal congela el estado y `progress` solo actualiza valores medidos.
 */
export function reduceProgress(state: ProgressState, event: ProgressEvent): ProgressState {
  if (state.terminal !== null) return state;

  switch (event.type) {
    case "snapshot":
      return {
        snapshot: event.snapshot,
        terminal: null,
        terminalAggregate: null,
        error: null,
      };
    case "progress": {
      if (state.snapshot === null) return state;
      return {
        ...state,
        snapshot: {
          ...state.snapshot,
          attempt: { ...state.snapshot.attempt, ...event.tick },
        },
      };
    }
    case "done":
      return {
        snapshot:
          state.snapshot === null
            ? null
            : { ...state.snapshot, stages: event.stages, aggregate: event.aggregate },
        terminal: "done",
        terminalAggregate: event.aggregate,
        error: null,
      };
    case "error":
      return {
        ...state,
        terminal: "error",
        error: { code: event.code, detail: event.detail },
      };
  }
}

/** Fragmentos completados/total; `null` si el servidor no publicó el total. */
export function fragmentLabel(done: number | null, total: number | null): string | null {
  if (total === null) return null;
  return `${done === null ? "?" : done}/${total}`;
}

/** La limpieza pendiente es un estado de trabajo, jamás un éxito. */
export function isCleanupPending(snapshot: ProgressSnapshot | null): boolean {
  return snapshot?.attempt.status === "transcript_committed_cleanup_pending";
}

export function progressAggregate(state: ProgressState): ProgressAggregate | null {
  return state.snapshot?.aggregate ?? state.terminalAggregate;
}
