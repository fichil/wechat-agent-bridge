import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { flushPromises } from "@vue/test-utils";
import { api, ApiFailure } from "../src/api";
import { useAccountsStore } from "../src/stores/accounts";
import { snapshot, syntheticAccount, task } from "./fixtures";

vi.mock("../src/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/api")>();
  return {
    ...actual,
    api: {
      startScan: vi.fn(),
      scan: vi.fn(),
      accounts: vi.fn(),
      account: vi.fn(),
    },
  };
});

describe("account lifecycle", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    sessionStorage.clear();
    setActivePinia(createPinia());
    vi.mocked(api.startScan).mockResolvedValue(task());
    vi.mocked(api.accounts).mockResolvedValue(snapshot());
    vi.mocked(api.account).mockResolvedValue(syntheticAccount);
  });
  afterEach(() => vi.useRealTimers());

  it("initializes from database without rescanning on route remount", async () => {
    const store = useAccountsStore();
    await store.initialize();
    await store.initialize();
    expect(api.startScan).not.toHaveBeenCalled();
    expect(store.accounts[0]?.wxid).toBe("wxid_synthetic_owner");
  });

  it("shares concurrent requests and polls every two seconds", async () => {
    vi.useFakeTimers();
    vi.mocked(api.startScan).mockResolvedValue(task("running"));
    vi.mocked(api.scan).mockResolvedValue(task());
    const store = useAccountsStore();
    const one = store.scan();
    const two = store.scan();
    await flushPromises();
    expect(store.scanning).toBe(true);
    await vi.advanceTimersByTimeAsync(1999);
    expect(api.scan).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await Promise.all([one, two]);
    expect(api.startScan).toHaveBeenCalledTimes(1);
    expect(store.scanning).toBe(false);
  });

  it("supports empty success, network failure and retry", async () => {
    vi.mocked(api.startScan).mockRejectedValueOnce(new Error("network"));
    const store = useAccountsStore();
    vi.mocked(api.accounts).mockRejectedValueOnce(new Error("offline"));
    await store.initialize();
    expect(store.error).toContain("无法连接");
    vi.mocked(api.accounts).mockResolvedValue(snapshot([]));
    await store.scan();
    expect(store.error).toBe("");
    expect(store.snapshot?.summary.coverage_complete).toBe(true);
    expect(store.accounts).toEqual([]);
  });

  it("preserves partial results and distinguishes module failure", async () => {
    const partial = snapshot();
    partial.exit_code = 2;
    partial.summary.coverage_complete = false;
    vi.mocked(api.accounts).mockResolvedValueOnce(partial);
    const store = useAccountsStore();
    await store.scan();
    expect(store.accounts).toHaveLength(1);
    expect(store.error).toBe("");
    vi.mocked(api.accounts).mockResolvedValue({
      ...snapshot([]),
      exit_code: 1,
      error: "probe_failed",
    });
    vi.mocked(api.startScan).mockResolvedValue(task("failed"));
    await store.scan();
    expect(store.error).toContain("识别失败");
  });

  it("keeps only the opaque selection in this tab and invalidates missing accounts", async () => {
    const store = useAccountsStore();
    await store.initialize();
    await store.enter("synthetic-account-1");
    expect(sessionStorage.getItem("bridge.selected-account")).toBe(
      "synthetic-account-1",
    );
    expect(localStorage.length).toBe(0);
    vi.mocked(api.accounts).mockResolvedValue(snapshot([]));
    await store.scan();
    expect(store.selectedId).toBeNull();
    expect(store.notice).toContain("已失效");
  });

  it("rejects a selection that disappeared on the backend", async () => {
    vi.mocked(api.account).mockRejectedValue(
      new ApiFailure("account_not_found", "账号已失效"),
    );
    const store = useAccountsStore();
    expect(await store.enter("old-account")).toBeNull();
    expect(store.notice).toContain("失效");
  });
});
