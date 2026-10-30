// D1.4 — Unitarias del cliente HTTP (U-S1-DA-01).
//
// Bootstrap (GET /me no-store), rotación/invalidez del token, inyección de
// X-CSRF-Token en mutaciones y los tres interceptores (401→login, 403→mensaje,
// 429→reintento diferido respetando Retry-After). Sin DOM: `fetch` se sustituye
// con `vi.stubGlobal` y los handlers se configuran por prueba.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiHttpError,
  apiFetch,
  bootstrapCsrf,
  configureApiHandlers,
  parseRetryAfter,
} from "@/lib/api";
import { clearCsrfToken, getCsrfToken, setCsrfToken } from "@/lib/csrf";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

const ME = {
  id: "u-1",
  email: "estudiante@lab.test",
  display_name: null,
  timezone: "America/Bogota",
  csrf_token: "tok-a",
};

beforeEach(() => {
  clearCsrfToken();
  configureApiHandlers({});
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("bootstrapCsrf (GET /me no-store)", () => {
  it("guarda el token en memoria y devuelve el perfil", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, ME));
    vi.stubGlobal("fetch", fetchMock);

    const me = await bootstrapCsrf();

    expect(me).toEqual(ME);
    expect(getCsrfToken()).toBe("tok-a");
    expect(fetchMock).toHaveBeenCalledWith("/me", { cache: "no-store", credentials: "include" });
  });

  it("rota el token al volver a hacer bootstrap", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, ME))
      .mockResolvedValueOnce(jsonResponse(200, { ...ME, csrf_token: "tok-b" }));
    vi.stubGlobal("fetch", fetchMock);

    await bootstrapCsrf();
    await bootstrapCsrf();

    expect(getCsrfToken()).toBe("tok-b");
  });

  it("limpia el token ante 401 (sin sesión)", async () => {
    setCsrfToken("tok-a");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, { error: { code: "auth_required" } }));
    vi.stubGlobal("fetch", fetchMock);

    const me = await bootstrapCsrf();

    expect(me).toBeNull();
    expect(getCsrfToken()).toBeNull();
  });

  it("limpia el token ante fallo de red", async () => {
    setCsrfToken("tok-a");
    const fetchMock = vi.fn().mockRejectedValue(new Error("red caída"));
    vi.stubGlobal("fetch", fetchMock);

    const me = await bootstrapCsrf();

    expect(me).toBeNull();
    expect(getCsrfToken()).toBeNull();
  });
});

describe("inyección de X-CSRF-Token", () => {
  it("inyecta la cabecera en mutaciones con token presente", async () => {
    setCsrfToken("tok-a");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/subjects", { method: "POST" });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Headers).get("x-csrf-token")).toBe("tok-a");
  });

  it("no inyecta la cabecera en lecturas (GET)", async () => {
    setCsrfToken("tok-a");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/subjects");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Headers).get("x-csrf-token")).toBeNull();
  });

  it("no inventa cabecera si no hay token en memoria", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/subjects", { method: "DELETE" });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Headers).get("x-csrf-token")).toBeNull();
  });
});

describe("interceptor 401", () => {
  it("limpia el token y avisa (onUnauthorized)", async () => {
    setCsrfToken("tok-a");
    const onUnauthorized = vi.fn();
    configureApiHandlers({ onUnauthorized });
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(401, { error: { code: "auth_required", message: "Inicia sesión." } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("/subjects", { method: "DELETE" })).rejects.toMatchObject({
      status: 401,
      code: "auth_required",
    });
    expect(onUnauthorized).toHaveBeenCalledOnce();
    expect(getCsrfToken()).toBeNull();
  });
});

describe("interceptor 403", () => {
  it("avisa con el mensaje de permisos y lanza ApiHttpError", async () => {
    const onForbidden = vi.fn();
    configureApiHandlers({ onForbidden });
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(403, { error: { code: "csrf_invalid", message: "Solicitud rechazada." } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("/subjects", { method: "POST" })).rejects.toMatchObject({
      status: 403,
      code: "csrf_invalid",
    });
    expect(onForbidden).toHaveBeenCalledOnce();
    expect(onForbidden.mock.calls[0][0]).toBeInstanceOf(ApiHttpError);
  });
});

describe("interceptor 429", () => {
  it("reintenta una lectura tras Retry-After (segundos) y resuelve", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(429, {}, { "retry-after": "2" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const promise = apiFetch("/subjects");
    // The delay adds up to 20% jitter on top of Retry-After.
    await vi.advanceTimersByTimeAsync(2600);
    const res = await promise;

    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("no reintenta una mutación aunque haya Retry-After", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(429, { error: { code: "rate_limited" } }, { "retry-after": "1" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("/audios", { method: "POST" })).rejects.toMatchObject({ status: 429 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reintenta una mutación solo con idempotent explícito", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(429, {}, { "retry-after": "1" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const promise = apiFetch("/audios", { method: "POST", idempotent: true });
    await vi.advanceTimersByTimeAsync(1500);
    const res = await promise;

    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("no reintenta si no hay Retry-After", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(429, { error: { code: "rate_limited" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("/subjects")).rejects.toMatchObject({
      status: 429,
      code: "rate_limited",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("agota los reintentos y lanza el 429", async () => {
    const fetchMock = vi
      .fn()
      // Fresh Response per call: the retry path cancels the first body on purpose.
      .mockImplementation(() =>
        Promise.resolve(
          jsonResponse(429, { error: { code: "rate_limited" } }, { "retry-after": "1" }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("/subjects", { retries: 1 })).rejects.toMatchObject({
      status: 429,
      code: "rate_limited",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("parseRetryAfter", () => {
  it("acepta segundos", () => {
    expect(parseRetryAfter("3")).toBe(3000);
  });

  it("acepta una fecha HTTP", () => {
    const inTwoSeconds = new Date(Date.now() + 2000).toUTCString();
    const ms = parseRetryAfter(inTwoSeconds);
    expect(ms).not.toBeNull();
    expect(ms as number).toBeGreaterThan(0);
    expect(ms as number).toBeLessThanOrEqual(2000);
  });

  it("devuelve null sin valor", () => {
    expect(parseRetryAfter(null)).toBeNull();
    expect(parseRetryAfter("")).toBeNull();
  });
});
