// Thin fetch wrapper. Session cookie + CSRF header; the API and the app share one origin.

export class ApiError extends Error {
  status: number;
  fields: Record<string, string>;
  code?: string;
  body: unknown; // the full answer, e.g. a restricted deal's placeholder

  constructor(status: number, body: unknown) {
    const { message, fields, code } = parseErrorBody(body);
    super(message);
    this.body = body;
    this.status = status;
    this.fields = fields;
    this.code = code;
  }
}

function parseErrorBody(body: unknown): { message: string; fields: Record<string, string>; code?: string } {
  const fields: Record<string, string> = {};
  let message = "Something went wrong. Try again.";
  let code: string | undefined;
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    for (const [k, v] of Object.entries(b)) {
      const text = Array.isArray(v) ? v.join(" ") : typeof v === "string" ? v : "";
      if (k === "detail") {
        if (typeof v === "object" && v && "detail" in (v as object)) {
          message = String((v as Record<string, unknown>).detail);
          code = String((v as Record<string, unknown>).code ?? "");
        } else if (text) message = text;
      } else if (k === "code") {
        code = String(v);
      } else if (text) {
        fields[k] = text;
      }
    }
    if (!("detail" in b) && Object.keys(fields).length) {
      message = Object.keys(fields).length === 1 ? Object.values(fields)[0] : "Please fix the highlighted fields.";
    }
  }
  return { message, fields, code };
}

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

type Options = { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal };

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const method = opts.method ?? (opts.body !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-CSRFToken"] = csrfToken();
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(`/api/${path.replace(/^\//, "")}`, {
    method,
    headers,
    body,
    credentials: "same-origin",
    signal: opts.signal,
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = undefined;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    data = { detail: text.slice(0, 200) };
  }
  if (!res.ok) {
    const err = new ApiError(res.status, data);
    if (err.code === "password_change_required") window.dispatchEvent(new Event("dealflow:password-change"));
    if (err.code?.startsWith("vault_")) window.dispatchEvent(new Event("dealflow:vault-locked"));
    if (res.status === 403 && /credentials were not provided|not authenticated/i.test(err.message)) {
      window.dispatchEvent(new Event("dealflow:signed-out"));
    }
    throw err;
  }
  return data as T;
}

/** POST and save the file that comes back (e.g. the audit log, which needs a password in the body). */
export async function postDownload(path: string, body: unknown): Promise<Response> {
  const res = await fetch(`/api/${path.replace(/^\//, "")}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
    body: JSON.stringify(body),
    credentials: "same-origin",
  });
  if (!res.ok) {
    const text = await res.text();
    let data: unknown;
    try { data = JSON.parse(text); } catch { data = { detail: text.slice(0, 200) }; }
    throw new ApiError(res.status, data);
  }
  const blob = await res.blob();
  const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? "download";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return res;
}

/** Download a file from the API (export, backups) through a hidden link. */
export function download(path: string) {
  const a = document.createElement("a");
  a.href = `/api/${path.replace(/^\//, "")}`;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function qs(params: Record<string, string | number | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}
