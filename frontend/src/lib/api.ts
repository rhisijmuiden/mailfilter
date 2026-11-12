// Small fetch wrapper. Every state-changing request carries X-Requested-With,
// which the backend requires (CSRF protection on top of SameSite cookies).
export class ApiError extends Error {
  // info: the structured detail some endpoints return, e.g. { code, problems: [...] }
  constructor(public status: number, public code: string, public info?: any) {
    super(code);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      "X-Requested-With": "mailfilter",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const structured = data?.detail && typeof data.detail === "object" && typeof data.detail.code === "string" ? data.detail : undefined;
    const detail = typeof data?.detail === "string" ? data.detail : structured ? structured.code : res.status === 422 ? "invalid_input" : "error";
    if (res.status === 401 && path !== "/auth/login" && path !== "/auth/me") window.dispatchEvent(new Event("mf:logged-out"));
    throw new ApiError(res.status, detail, structured);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}
