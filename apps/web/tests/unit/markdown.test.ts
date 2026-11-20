import { describe, expect, it } from "vitest";
import { safeCitationHref } from "@/lib/markdown";

const AUDIO = "123e4567-e89b-12d3-a456-426614174000";
const SEGMENT = "223e4567-e89b-12d3-a456-426614174000";

describe("safeCitationHref", () => {
  it("accepts the canonical reader paths", () => {
    expect(safeCitationHref(`/biblioteca/${AUDIO}`)).toBe(`/biblioteca/${AUDIO}`);
    expect(safeCitationHref(`/biblioteca/${AUDIO}?segment=${SEGMENT}`)).toBe(
      `/biblioteca/${AUDIO}?segment=${SEGMENT}`,
    );
  });

  it("rejects absolute, protocol-relative and non-reader URLs", () => {
    expect(safeCitationHref(`https://evil.example/biblioteca/${AUDIO}`)).toBeNull();
    expect(safeCitationHref(`//evil.example/biblioteca/${AUDIO}`)).toBeNull();
    expect(safeCitationHref(`javascript:alert(1)`)).toBeNull();
    expect(safeCitationHref(`/chat/${AUDIO}`)).toBeNull();
    expect(safeCitationHref(`/biblioteca/../../${AUDIO}`)).toBeNull();
  });

  it("rejects malformed ids and extra query parameters", () => {
    expect(safeCitationHref("/biblioteca/not-a-uuid")).toBeNull();
    expect(safeCitationHref(`/biblioteca/${AUDIO}?segment=not-a-uuid`)).toBeNull();
    expect(safeCitationHref(`/biblioteca/${AUDIO}?segment=${SEGMENT}&next=1`)).toBeNull();
    expect(
      safeCitationHref(`/biblioteca/${AUDIO}?segment=${SEGMENT}&segment=${SEGMENT}`),
    ).toBeNull();
    expect(safeCitationHref(`/biblioteca/${AUDIO}?segment=${SEGMENT}#fragment`)).toBeNull();
    expect(safeCitationHref(`/biblioteca/${AUDIO}?`)).toBeNull();
  });
});
