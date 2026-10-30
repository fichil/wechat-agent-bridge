import { reactive } from "vue";
import type { components } from "./api/schema";

type Session = components["schemas"]["AuthSession"];
export const authState = reactive({
  ready: false,
  authenticated: false,
  initialized: true,
  username: "",
  csrf: "",
  expiresAt: "",
  invalidating: false,
});
let bootstrap: Promise<void> | null = null;
let generation = 0;
let expiryTimer: ReturnType<typeof setTimeout> | undefined;
const requests = new Set<AbortController>();
const logoutKey = "bridge.auth-logout";
const channel =
  typeof window !== "undefined" && typeof BroadcastChannel !== "undefined"
    ? new BroadcastChannel("bridge-auth")
    : null;

export class AuthFailure extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export function safeReturn(value: unknown): string {
  return typeof value === "string" &&
    /^\/(accounts|workspace)(\/|\?|$)/.test(value) &&
    !value.includes("\\")
    ? value
    : "/accounts";
}

function clearSession() {
  generation++;
  clearTimeout(expiryTimer);
  for (const request of requests) request.abort();
  requests.clear();
  Object.assign(authState, {
    authenticated: false,
    username: "",
    csrf: "",
    expiresAt: "",
  });
  try {
    sessionStorage.removeItem("bridge.selected-account");
  } catch {
    /* Storage can be disabled. */
  }
}

export function invalidateAuthentication(broadcast = true) {
  if (authState.invalidating) return;
  authState.invalidating = true;
  clearSession();
  if (broadcast) {
    channel?.postMessage("logout");
    try {
      localStorage.setItem(logoutKey, String(Date.now()));
    } catch {
      /* Storage can be disabled. */
    }
  }
  // A new document also destroys Pinia caches, component drafts and polling loops.
  // The dirty-form guard deliberately permits this authentication boundary.
  const next = safeReturn(location.pathname + location.search);
  location.replace(`/login?next=${encodeURIComponent(next)}`);
}

channel?.addEventListener("message", (event) => {
  if (event.data === "logout") invalidateAuthentication(false);
});
if (typeof window !== "undefined")
  window.addEventListener("storage", (event) => {
    if (event.key === logoutKey && event.newValue)
      invalidateAuthentication(false);
  });

function acceptSession(session: Session) {
  if (authState.invalidating) return;
  clearTimeout(expiryTimer);
  Object.assign(authState, {
    ready: true,
    authenticated: session.authenticated,
    initialized: session.initialized,
    username: session.username ?? "",
    csrf: session.csrf_token ?? "",
    expiresAt: session.expires_at ?? "",
    invalidating: false,
  });
  if (session.authenticated && session.expires_at) {
    expiryTimer = setTimeout(
      () => invalidateAuthentication(),
      Math.max(0, Date.parse(session.expires_at) - Date.now()),
    );
  }
}

async function authRequest<T>(action: string, body: object = {}): Promise<T> {
  const response = await fetch(`/api/v1/auth/${action}`, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(authState.csrf ? { "X-CSRF-Token": authState.csrf } : {}),
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15_000),
  }).catch(() => {
    throw new AuthFailure(0, "无法连接后台，请检查网络后重试。");
  });
  const data = await response.json();
  if (!response.ok)
    throw new AuthFailure(
      response.status,
      data.message ?? "请求失败，请稍后重试。",
    );
  return data as T;
}

export function ensureSession(): Promise<void> {
  if (authState.ready) return Promise.resolve();
  if (!bootstrap)
    bootstrap = authRequest<Session>("session")
      .then(acceptSession)
      .finally(() => {
        bootstrap = null;
      });
  return bootstrap;
}

export async function login(username: string, password: string) {
  acceptSession(await authRequest<Session>("login", { username, password }));
}

export async function logout() {
  try {
    await authRequest("logout");
  } catch (error) {
    if (!(error instanceof AuthFailure && error.status === 401)) throw error;
  }
  invalidateAuthentication();
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
) {
  await authRequest("change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
  invalidateAuthentication();
}

export async function authenticatedFetch(request: Request): Promise<Response> {
  if (!authState.authenticated || authState.invalidating)
    throw new AuthFailure(401, "请先登录后台。");
  const ticket = generation;
  const controller = new AbortController();
  requests.add(controller);
  const timeout = setTimeout(() => controller.abort(), 15_000);
  const abort = () => controller.abort();
  request.signal.addEventListener("abort", abort, { once: true });
  if (request.signal.aborted) controller.abort();
  const headers = new Headers(request.headers);
  headers.set("X-CSRF-Token", authState.csrf);
  try {
    const response = await fetch(request, {
      headers,
      credentials: "same-origin",
      signal: controller.signal,
      cache: "no-store",
    });
    // Consume the body within the generation guard, not later in openapi-fetch.
    const bytes = await response.arrayBuffer();
    if (ticket !== generation || authState.invalidating)
      throw new AuthFailure(401, "登录已失效。");
    if (response.status === 401) {
      invalidateAuthentication();
      throw new AuthFailure(401, "登录已失效，请重新登录。");
    }
    return new Response(bytes, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  } finally {
    clearTimeout(timeout);
    request.signal.removeEventListener("abort", abort);
    requests.delete(controller);
  }
}
