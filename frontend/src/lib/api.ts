import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Relative, not "/api/...": under Home Assistant the app lives at
  // /api/hassio_ingress/<token>/, and the page's <base> points there.
  const response = await fetch(path.replace(/^\/+/, ""), {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* response wasn't JSON — keep the status line */
    }
    throw new ApiError(detail, response.status);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T,>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T,>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T,>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
};

/** Fetch-on-mount with a manual refresh handle; `deps` re-runs the request. */
export function useApi<T>(path: string | null, deps: unknown[] = []) {
  // Hooks need a dependency list of fixed length, so the variable-length
  // `deps` (always plain values) are folded into one key.
  const key = JSON.stringify([path, deps]);
  const [result, setResult] = useState<{ key: string; data: T | null; error: string | null } | null>(null);
  // The newest request: an answer to an older one that arrives late is dropped.
  const latest = useRef(key);

  // State is only set once an answer arrives, never while asking.
  const settle = useCallback(
    (request: Promise<T>) =>
      request.then(
        (data) => {
          if (latest.current === key) setResult({ key, data, error: null });
        },
        (err) => {
          const error = err instanceof Error ? err.message : String(err);
          if (latest.current === key) setResult((prev) => ({ key, data: prev?.data ?? null, error }));
        },
      ),
    [key],
  );

  useEffect(() => {
    latest.current = key;
    if (path) void settle(api.get<T>(path));
  }, [key, path, settle]);

  const reload = useCallback(() => (path ? settle(api.get<T>(path)) : Promise.resolve()), [path, settle]);

  const fresh = result?.key === key;
  return {
    // The previous answer stays on screen while a new one loads.
    data: result?.data ?? null,
    error: fresh ? result.error : null,
    loading: Boolean(path) && !fresh,
    reload,
  };
}
