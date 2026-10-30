import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  authenticatedFetch,
  authState,
  safeReturn,
  invalidateAuthentication,
  ensureSession,
} from "../src/auth";

describe("authentication transport boundary", () => {
  beforeEach(() => {
    vi.stubGlobal("location", {
      pathname: "/accounts",
      search: "",
      replace: vi.fn(),
    });
    Object.assign(authState, {
      ready: false,
      authenticated: true,
      invalidating: false,
      csrf: "synthetic-csrf",
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });
  it("rejects an unauthenticated request before network access", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    authState.authenticated = false;
    await expect(
      authenticatedFetch(new Request("http://localhost/api/v1/accounts/query")),
    ).rejects.toThrow("请先登录");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("adds CSRF and same-origin credentials", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response('{"ok":true}', {
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetch);
    const response = await authenticatedFetch(
      new Request("http://localhost/api/v1/accounts/query", { method: "POST" }),
    );
    expect(await response.json()).toEqual({ ok: true });
    expect(fetch.mock.calls[0]![1].headers.get("X-CSRF-Token")).toBe(
      "synthetic-csrf",
    );
    expect(fetch.mock.calls[0]![1].credentials).toBe("same-origin");
  });
  it("rejects an in-flight response after authentication invalidation", async () => {
    let resolve!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        () =>
          new Promise<Response>((done) => {
            resolve = done;
          }),
      ),
    );
    const pending = authenticatedFetch(
      new Request("http://localhost/api/v1/accounts/query"),
    );
    invalidateAuthentication(false);
    // Even if a newer login has started, this prior generation must stay rejected.
    authState.invalidating = false;
    authState.authenticated = true;
    resolve(new Response('{"private_chat":"must not escape"}'));
    await expect(pending).rejects.toThrow("登录已失效");
  });
  it("redirects and clears credentials on business 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 401 })),
    );
    sessionStorage.setItem("bridge.selected-account", "synthetic-account");
    await expect(
      authenticatedFetch(new Request("http://localhost/api/v1/accounts/query")),
    ).rejects.toThrow("登录已失效");
    expect(authState.authenticated).toBeFalsy();
    expect(authState.csrf).toBe("");
    expect(sessionStorage.getItem("bridge.selected-account")).toBeNull();
    expect(location.replace).toHaveBeenCalledWith("/login?next=%2Faccounts");
  });
  it("ends the session at its fixed deadline without another business request", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            authenticated: true,
            initialized: true,
            username: "admin",
            csrf_token: "test",
            expires_at: new Date(Date.now() + 1000).toISOString(),
          }),
        ),
      ),
    );
    await ensureSession();
    await vi.advanceTimersByTimeAsync(1000);
    expect(authState.authenticated).toBeFalsy();
    expect(location.replace).toHaveBeenCalled();
  });
  it("allows only supported site-relative return paths", () => {
    expect(safeReturn("/workspace/abc/logs")).toBe("/workspace/abc/logs");
    for (const path of [
      "https://evil.example",
      "//evil.example",
      "/login",
      "/workspace\\evil",
      "/accounts-evil",
    ])
      expect(safeReturn(path)).toBe("/accounts");
  });
});
