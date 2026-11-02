import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import {
  createPinia,
  setActivePinia,
  disposePinia,
  getActivePinia,
} from "pinia";
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
      deleteAccount: vi.fn(),
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
  afterEach(() => {
    disposePinia(getActivePinia()!);
    vi.useRealTimers();
  });

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
    const store = useAccountsStore();
    vi.mocked(api.accounts).mockRejectedValueOnce(new Error("offline"));
    await store.initialize();
    expect(store.connectionError).toContain("offline");
    vi.mocked(api.accounts).mockResolvedValue(snapshot([]));
    await store.scan();
    expect(store.error).toBe("");
    expect(store.snapshot?.summary.coverage_complete).toBe(true);
    expect(store.accounts).toEqual([]);
    expect(store.connectionError).toBe("");
  });

  it("retains historical accounts on disconnection and recovers on bounded polling", async () => {
    vi.useFakeTimers();
    const store = useAccountsStore();
    await store.initialize();
    const checked = store.lastCheckedAt;
    vi.mocked(api.accounts).mockRejectedValueOnce(new Error("连接中断"));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(store.connectionError).toBe("连接中断");
    expect(store.accounts).toHaveLength(1);
    expect(store.lastCheckedAt).toBe(checked);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(store.connectionError).toBe("");
    expect(store.lastCheckedAt).not.toBe(checked);
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

  it("deletes an offline account and rejects stale cache responses", async () => {
    const store = useAccountsStore();
    await store.initialize();
    await store.enter("synthetic-account-1");
    vi.mocked(api.deleteAccount).mockResolvedValue({
      account_id: "synthetic-account-1",
      deleted: true,
    });
    await store.remove({ ...syntheticAccount, source_state: "offline" });
    expect(api.deleteAccount).toHaveBeenCalledWith(
      "synthetic-account-1",
      syntheticAccount.revision ?? 0,
    );
    expect(store.accounts).toEqual([]);
    expect(store.selectedId).toBeNull();
    expect(sessionStorage.getItem("bridge.selected-account")).toBeNull();
    await store.scan();
    expect(store.accounts).toEqual([]);
  });
});
