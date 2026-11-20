// D2.3 — U-S2-DA-03: parseo y reducción del progreso honesto. Sin DOM se comprueba que el
// `snapshot` reemplaza estado (nunca suma), que los terminales congelan y que no existe
// ningún cálculo de porcentaje ni ETA.
import { describe, expect, it } from "vitest";
import {
  INITIAL_PROGRESS,
  fragmentLabel,
  isCleanupPending,
  parseProgressEvent,
  progressAggregate,
  reduceProgress,
  snapshotFromDetail,
} from "@/lib/progress";
import type { ProgressEvent, ProgressSnapshot } from "@/lib/progress";

const SNAPSHOT: ProgressSnapshot = {
  attempt: {
    status: "transcribing",
    received_bytes: 2048,
    fragments_done: 2,
    fragments_total: 5,
  },
  stages: { index: "pending", analyze: "not_requested" },
  aggregate: "processing",
};

function snapshotPayload(snapshot: ProgressSnapshot = SNAPSHOT): Record<string, unknown> {
  return {
    attempt: snapshot.attempt,
    stages: snapshot.stages,
    aggregate: snapshot.aggregate,
  };
}

describe("parseProgressEvent", () => {
  it("acepta los cuatro eventos del contrato", () => {
    expect(parseProgressEvent("snapshot", JSON.stringify(snapshotPayload()))).toEqual({
      type: "snapshot",
      snapshot: SNAPSHOT,
    });
    expect(
      parseProgressEvent(
        "progress",
        JSON.stringify({
          received_bytes: 4096,
          fragments_done: 3,
          fragments_total: 5,
        }),
      ),
    ).toEqual({
      type: "progress",
      tick: { received_bytes: 4096, fragments_done: 3, fragments_total: 5 },
    });
    expect(
      parseProgressEvent(
        "done",
        JSON.stringify({
          aggregate: "partial",
          stages: { index: "succeeded", analyze: "failed" },
        }),
      ),
    ).toEqual({
      type: "done",
      aggregate: "partial",
      stages: { index: "succeeded", analyze: "failed" },
    });
    expect(parseProgressEvent("error", JSON.stringify({ code: "aggregate_failed" }))).toEqual({
      type: "error",
      code: "aggregate_failed",
      detail: null,
    });
  });

  it("ignora nombres desconocidos y payloads malformados", () => {
    expect(parseProgressEvent("ping", "{}")).toBeNull();
    expect(parseProgressEvent("snapshot", "{")).toBeNull();
    expect(parseProgressEvent("snapshot", JSON.stringify({ aggregate: "processing" }))).toBeNull();
    expect(
      parseProgressEvent(
        "snapshot",
        JSON.stringify({
          ...snapshotPayload(),
          attempt: { ...SNAPSHOT.attempt, received_bytes: -1 },
        }),
      ),
    ).toBeNull();
    expect(
      parseProgressEvent("done", JSON.stringify({ aggregate: "invented", stages: SNAPSHOT.stages })),
    ).toBeNull();
    expect(parseProgressEvent("error", JSON.stringify({ detail: "sin código" }))).toBeNull();
  });
});

describe("reduceProgress", () => {
  it("un snapshot reemplaza el estado y nunca suma bytes", () => {
    const first = reduceProgress(INITIAL_PROGRESS, { type: "snapshot", snapshot: SNAPSHOT });
    const progressed = reduceProgress(first, {
      type: "progress",
      tick: { received_bytes: 9000, fragments_done: 4, fragments_total: 5 },
    });
    expect(progressed.snapshot?.attempt.received_bytes).toBe(9000);

    const fresh: ProgressEvent = {
      type: "snapshot",
      snapshot: {
        ...SNAPSHOT,
        attempt: { ...SNAPSHOT.attempt, received_bytes: 10, fragments_done: 0 },
      },
    };
    const reconnected = reduceProgress(progressed, fresh);
    expect(reconnected.snapshot?.attempt.received_bytes).toBe(10);
    expect(reconnected.snapshot?.attempt.fragments_done).toBe(0);
  });

  it("ignora progress antes del snapshot y eventos posteriores al terminal", () => {
    const noSnapshot = reduceProgress(INITIAL_PROGRESS, {
      type: "progress",
      tick: { received_bytes: 10, fragments_done: null, fragments_total: null },
    });
    expect(noSnapshot).toBe(INITIAL_PROGRESS);

    const withSnapshot = reduceProgress(INITIAL_PROGRESS, {
      type: "snapshot",
      snapshot: SNAPSHOT,
    });
    const done = reduceProgress(withSnapshot, {
      type: "done",
      aggregate: "ready",
      stages: { index: "succeeded", analyze: "not_requested" },
    });
    expect(done.terminal).toBe("done");
    expect(done.snapshot?.aggregate).toBe("ready");
    expect(done.snapshot?.stages.index).toBe("succeeded");

    const late = reduceProgress(done, {
      type: "progress",
      tick: { received_bytes: 1, fragments_done: 1, fragments_total: 1 },
    });
    expect(late).toBe(done);
  });

  it("un error es terminal y conserva el código y el detalle", () => {
    const failed = reduceProgress(INITIAL_PROGRESS, {
      type: "error",
      code: "aggregate_failed",
      detail: "requires_reupload",
    });
    expect(failed.terminal).toBe("error");
    expect(failed.error).toEqual({ code: "aggregate_failed", detail: "requires_reupload" });
  });
});

describe("snapshotFromDetail (fallback de sondeo)", () => {
  it("lee el detalle y usa el mismo intento vacío cuando no hay attempt", () => {
    expect(
      snapshotFromDetail({
        attempt: null,
        stages: { index: "not_requested", analyze: "not_requested" },
        aggregate_status: "failed",
      }),
    ).toEqual({
      attempt: {
        status: null,
        received_bytes: 0,
        fragments_done: null,
        fragments_total: null,
      },
      stages: { index: "not_requested", analyze: "not_requested" },
      aggregate: "failed",
    });
  });

  it("rechaza un detalle sin agregado o etapas válidas", () => {
    expect(snapshotFromDetail({})).toBeNull();
    expect(
      snapshotFromDetail({
        attempt: null,
        stages: { index: "pending" },
        aggregate_status: "processing",
      }),
    ).toBeNull();
  });
});

describe("presentación honesta", () => {
  it("no inventa fragmentos sin total ni porcentajes", () => {
    expect(fragmentLabel(2, 5)).toBe("2/5");
    expect(fragmentLabel(null, 5)).toBe("?/5");
    expect(fragmentLabel(2, null)).toBeNull();
  });

  it("detecta limpieza pendiente y expone el agregado terminal", () => {
    const cleanup = {
      ...SNAPSHOT,
      attempt: { ...SNAPSHOT.attempt, status: "transcript_committed_cleanup_pending" },
    };
    expect(isCleanupPending(cleanup)).toBe(true);
    expect(isCleanupPending(SNAPSHOT)).toBe(false);
    expect(isCleanupPending(null)).toBe(false);

    const done = reduceProgress(INITIAL_PROGRESS, {
      type: "done",
      aggregate: "partial",
      stages: { index: "succeeded", analyze: "failed" },
    });
    expect(progressAggregate(done)).toBe("partial");
  });
});
