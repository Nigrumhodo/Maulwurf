import { describe, expect, it } from "vitest";
import { createProgressSource } from "@/lib/sse";
import type { ProgressEventSource } from "@/lib/sse";
import type { ProgressEvent } from "@/lib/progress";

class ClosedAfterErrorSource implements ProgressEventSource {
  readyState = 2;
  closed = false;
  readonly listeners = new Map<string, Array<(event: Event) => void>>();

  addEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
  }
}

describe("createProgressSource with an HTTP error", () => {
  it("starts polling after one error when the EventSource is CLOSED", async () => {
    const fake = new ClosedAfterErrorSource();
    const events: ProgressEvent[] = [];
    const transports: string[] = [];
    const source = createProgressSource(
      "a1",
      { onEvent: (event) => events.push(event), onTransport: (t) => transports.push(t) },
      {
        createEventSource: () => fake,
        fetchSnapshot: async () => ({
          attempt: { status: "succeeded", received_bytes: 1, fragments_done: 1, fragments_total: 1 },
          stages: { index: "succeeded", analyze: "not_requested" },
          aggregate: "ready",
        }),
        pollIntervalMs: 0,
        random: () => 0,
      },
    );
    for (const listener of fake.listeners.get("error") ?? []) listener(new Event("error"));

    expect(transports).toEqual(["sse", "polling"]);
    expect(fake.closed).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(events.map((event) => event.type)).toContain("done");
    source.close();
  });
});
