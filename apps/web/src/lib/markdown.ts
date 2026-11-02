const AUDIO_PATH =
  /^\/biblioteca\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SEGMENT_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Only the reader URLs emitted by the validated citation registry are navigable.
 * Persisted history is still untrusted input, so anything outside the canonical
 * `/biblioteca/{audio_id}` and single `segment` query is rejected.
 */
export function safeCitationHref(url: string): string | null {
  if (!url.startsWith("/") || url.startsWith("//") || url.includes("#")) return null;
  const [path, query, ...extra] = url.split("?");
  if (extra.length > 0 || path === undefined || !AUDIO_PATH.test(path)) return null;
  if (query === undefined) return path;
  if (query === "") return null;
  const params = new URLSearchParams(query);
  const keys = [...params.keys()];
  if (keys.length !== 1 || keys[0] !== "segment") return null;
  const segments = params.getAll("segment");
  if (segments.length !== 1) return null;
  const segment = segments[0];
  if (segment === undefined || !SEGMENT_ID.test(segment)) return null;
  return `${path}?segment=${segment}`;
}
