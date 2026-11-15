/** A JSON object, excluding arrays (arrays are objects in JavaScript). */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Read a JSON body. Throws if the payload is missing or not JSON. */
export async function readJson(res: Response): Promise<unknown> {
  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    throw new Error("La respuesta no es JSON.");
  }
  try {
    const payload: unknown = await res.json();
    return payload;
  } catch {
    throw new Error("La respuesta JSON no se pudo leer.");
  }
}
