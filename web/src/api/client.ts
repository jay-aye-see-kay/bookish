export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type FetchOpts = {
  method?: string;
  username: string;
  body?: unknown;
  signal?: AbortSignal;
};

/**
 * Fetch wrapper: base `/api`, injects `X-User`, throws `ApiError` on non-2xx.
 * `path` must start with `/` (e.g. `/health`, `/preferences/works/OL64365W`).
 */
export async function apiFetch<T>(path: string, opts: FetchOpts): Promise<T> {
  const { method = "GET", username, body, signal } = opts;
  const headers: Record<string, string> = { "X-User": username };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });

  if (!res.ok) {
    let message = res.statusText;
    try {
      const text = await res.text();
      if (text) message = text;
    } catch {
      // ignore body-read failures
    }
    throw new ApiError(res.status, message);
  }

  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) return undefined as T;
  return (await res.json()) as T;
}
