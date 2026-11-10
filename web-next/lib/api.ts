// In production the static export is served by the Python server itself (same origin).
// Under `next dev` (port 3000) the API lives on 127.0.0.1:8800.
const DEV = typeof window !== "undefined" && window.location.port === "3000";
export const API_BASE = DEV ? "http://127.0.0.1:8800" : "";
export const WS_URL = (): string =>
  DEV ? "ws://127.0.0.1:8800/ws" : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws`;

export async function api<T = any>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(API_BASE + path, { cache: "no-store", ...opts });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error || r.statusText);
  }
  return (r.headers.get("content-type")?.includes("json") ? r.json() : r.text()) as Promise<T>;
}

export const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
