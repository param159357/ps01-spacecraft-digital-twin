// Typed API client.
//
// The frontend always calls /api on its own origin: through the Next dev proxy in
// development, through Caddy in deployment. The session cookie is HttpOnly and is
// never read here; mutations echo the readable CSRF cookie back in a header.

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly phase?: number;

  constructor(status: number, code: string, message: string, phase?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.phase = phase;
  }

  /** The endpoint exists and is authorized, but its phase has not been built yet. */
  get isNotImplementedYet(): boolean {
    return this.code === "NOT_IMPLEMENTED_YET";
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }
}

const CSRF_COOKIE = "ps01_csrf";

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

type Method = "GET" | "POST" | "DELETE";

interface RequestOptions {
  method?: Method;
  body?: unknown;
  signal?: AbortSignal;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (method !== "GET") {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  const response = await fetch(`/api${path}`, {
    method,
    headers,
    // Session cookie must accompany every call.
    credentials: "include",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });

  if (response.status === 204) {
    return undefined as T;
  }

  let payload: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const detail = (payload ?? {}) as { error?: string; message?: string; phase?: number };
    throw new ApiError(
      response.status,
      detail.error ?? "REQUEST_FAILED",
      detail.message ?? `Request failed with status ${response.status}.`,
      detail.phase,
    );
  }

  return payload as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => apiRequest<T>(path, { method: "GET", signal }),
  post: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "POST", body }),
};
