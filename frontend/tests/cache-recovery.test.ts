import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia, disposePinia, type Pinia } from "pinia";
import { flushPromises } from "@vue/test-utils";
import {
  api,
  ApiFailure,
  type ConversationPage,
  type MessagePage,
} from "../src/api";
import { authState } from "../src/auth";
import { ConnectionFailure } from "../src/connection";
import { useConversationsStore } from "../src/stores/conversations";
import { useMessagesStore } from "../src/stores/messages";
import { defaultExclusions } from "../src/conversation-options";
import { messagePage } from "./message-fixtures";

vi.mock("../src/api", async (original) => ({
  ...(await original<typeof import("../src/api")>()),
  api: {
    conversations: vi.fn(),
    refreshConversations: vi.fn(),
    conversationRefresh: vi.fn(),
    messages: vi.fn(),
    readMessages: vi.fn(),
  },
}));
function conversations(account = "one", needs_sync = false): ConversationPage {
  return {
    account_id: account,
    source_state: "online",
    coverage: "complete",
    needs_sync,
    revision: 0,
    snapshot_id: "snapshot",
    observed_at: "2026-11-06T00:00:00Z",
    status: "complete",
    items: [],
    total: 0,
    unfiltered_total: 0,
    next_cursor: null,
    applied_exclusions: defaultExclusions(),
    issues: [],
    stale: false,
  };
}

describe("cache-only connection recovery", () => {
  let pinia: Pinia;
  beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers();
    pinia = createPinia();
    setActivePinia(pinia);
    Object.assign(authState, { authenticated: true, invalidating: false });
    vi.mocked(api.conversations).mockImplementation(async (id) =>
      conversations(id),
    );
    vi.mocked(api.messages).mockImplementation(async (id, chat) =>
      messagePage(id, chat),
    );
  });
  afterEach(() => {
    disposePinia(pinia);
    vi.useRealTimers();
  });

  it("backs off without notifications and recovers without starting source work", async () => {
    const store = useConversationsStore();
    store.activate("one");
    await flushPromises();
    const checked = store.lastCheckedAt;
    vi.mocked(api.conversations).mockRejectedValue(
      new ConnectionFailure("backend_timeout"),
    );
    await store.refresh(false);
    expect(store.page).not.toBeNull();
    expect(store.lastCheckedAt).toBe(checked);
    expect(store.error).toContain("后台响应超时");
    for (const delay of [2000, 5000, 10000, 30000, 30000]) {
      const count = vi.mocked(api.conversations).mock.calls.length;
      await vi.advanceTimersByTimeAsync(delay - 1);
      expect(api.conversations).toHaveBeenCalledTimes(count);
      await vi.advanceTimersByTimeAsync(1);
      expect(api.conversations).toHaveBeenCalledTimes(count + 1);
    }
    vi.mocked(api.conversations).mockResolvedValue(conversations("one", true));
    await vi.advanceTimersByTimeAsync(30000);
    expect(store.error).toBe("");
    expect(store.recovering).toBe(false);
    expect(store.lastCheckedAt).not.toBe(checked);
    expect(api.refreshConversations).not.toHaveBeenCalled();
    const count = vi.mocked(api.conversations).mock.calls.length;
    await vi.advanceTimersByTimeAsync(60000);
    expect(api.conversations).toHaveBeenCalledTimes(count);
  });

  it("restores messages after a network failure using only cache even if sync is needed", async () => {
    const store = useMessagesStore();
    store.activate("one", "chat");
    await flushPromises();
    const checked = store.lastCheckedAt;
    vi.mocked(api.messages).mockRejectedValueOnce(
      new ConnectionFailure("network_unavailable"),
    );
    await store.refresh();
    expect(store.items).toHaveLength(2);
    expect(store.lastCheckedAt).toBe(checked);
    expect(store.error).toContain("无法连接后台");
    vi.mocked(api.messages).mockResolvedValue({
      ...messagePage(),
      needs_sync: true,
    });
    await vi.advanceTimersByTimeAsync(2000);
    expect(store.error).toBe("");
    expect(store.recovering).toBe(false);
    expect(api.readMessages).not.toHaveBeenCalled();
  });

  it("preserves an explicit source refresh while automatic recovery is pending", async () => {
    const store = useConversationsStore();
    vi.mocked(api.conversations).mockRejectedValueOnce(
      new ConnectionFailure("backend_timeout"),
    );
    store.activate("one");
    await flushPromises();
    vi.mocked(api.refreshConversations).mockResolvedValue({
      account_id: "one",
      refresh_id: "manual",
      status: "completed",
      started_at: "2026-11-06T00:00:00Z",
    });
    await store.refresh();
    expect(api.refreshConversations).toHaveBeenCalledTimes(1);
    expect(store.error).toBe("");
    const count = vi.mocked(api.conversations).mock.calls.length;
    await vi.advanceTimersByTimeAsync(30000);
    expect(api.conversations).toHaveBeenCalledTimes(count);
  });

  it.each(["switch", "hide", "logout", "dispose"])(
    "cancels an in-flight recovery on %s",
    async (action) => {
      const store = useConversationsStore();
      vi.mocked(api.conversations).mockRejectedValueOnce(
        new ConnectionFailure("backend_timeout"),
      );
      store.activate("one");
      await flushPromises();
      let release!: (page: ConversationPage) => void;
      vi.mocked(api.conversations).mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            release = resolve;
          }),
      );
      await vi.advanceTimersByTimeAsync(2000);
      const signal = vi.mocked(api.conversations).mock.calls.at(-1)![2]!;
      expect(signal.aborted).toBe(false);
      if (action === "switch") store.activate("two");
      if (action === "hide") store.activate("one", false);
      if (action === "logout") authState.authenticated = false;
      if (action === "dispose") store.$dispose();
      expect(signal.aborted).toBe(true);
      await flushPromises();
      const count = vi.mocked(api.conversations).mock.calls.length;
      release(conversations("one"));
      await flushPromises();
      await vi.advanceTimersByTimeAsync(60000);
      expect(api.conversations).toHaveBeenCalledTimes(count);
      if (action === "switch") expect(store.page?.account_id).toBe("two");
      else expect(store.page).toBeNull();
    },
  );

  it("cancels message recovery on conversation change and ignores a late response", async () => {
    const store = useMessagesStore();
    vi.mocked(api.messages).mockRejectedValueOnce(
      new ConnectionFailure("network_unavailable"),
    );
    store.activate("one", "old");
    await flushPromises();
    let release!: (page: MessagePage) => void;
    vi.mocked(api.messages).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    await vi.advanceTimersByTimeAsync(2000);
    const signal = vi.mocked(api.messages).mock.calls.at(-1)![3]!;
    store.activate("one", "new");
    await flushPromises();
    expect(signal.aborted).toBe(true);
    release(messagePage("one", "old"));
    await flushPromises();
    expect(store.page?.conversation_id).toBe("new");
  });

  it.each(["source_offline", "account_not_found", "cursor_expired"])(
    "does not automatically retry %s",
    async (code) => {
      vi.mocked(api.messages).mockRejectedValue(
        new ApiFailure(code, "synthetic"),
      );
      const store = useMessagesStore();
      store.activate("one", "chat");
      await flushPromises();
      await vi.advanceTimersByTimeAsync(60000);
      expect(api.messages).toHaveBeenCalledTimes(1);
      expect(api.readMessages).not.toHaveBeenCalled();
    },
  );
});
