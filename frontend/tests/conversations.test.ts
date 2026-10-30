import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { flushPromises } from "@vue/test-utils";
import {
  api,
  ApiFailure,
  type ConversationPage,
  type ConversationRefresh,
} from "../src/api";
import { useConversationsStore } from "../src/stores/conversations";
import { defaultExclusions } from "../src/conversation-options";

vi.mock("../src/api", async (original) => ({
  ...(await original<typeof import("../src/api")>()),
  api: {
    refreshConversations: vi.fn(),
    conversationRefresh: vi.fn(),
    conversations: vi.fn(),
  },
}));

function task(
  id = "one",
  status: ConversationRefresh["status"] = "completed",
): ConversationRefresh {
  return {
    account_id: id,
    refresh_id: "refresh-" + id,
    status,
    started_at: "2026-10-28T00:00:00Z",
  };
}
function page(id = "one", next: string | null = null): ConversationPage {
  return {
    account_id: id,
    source_state: "online",
    coverage: "complete",
    needs_sync: false,
    revision: 0,
    snapshot_id: "snapshot-" + id,
    observed_at: "2026-10-28T00:00:00Z",
    status: "complete",
    items: [
      {
        conversation_id: "conversation-" + id,
        ai_enabled: false,
        ai_pending_count: 0,
        ai_waiting_count: 0,
        ai_paused: false,
        memory_revision: 0,
        preferences_revision: 0,
        name: "合成好友",
        category: "contact",
        is_container: false,
        summary: "合成消息",
        last_message_at: null,
        unread_count: 3,
        avatar: null,
      },
    ],
    total: 1,
    unfiltered_total: 1,
    next_cursor: next,
    applied_exclusions: defaultExclusions(),
    issues: [],
    stale: false,
  };
}

describe("conversation lifecycle", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers();
    setActivePinia(createPinia());
    vi.mocked(api.refreshConversations).mockImplementation(async (id) =>
      task(id),
    );
    vi.mocked(api.conversationRefresh).mockImplementation(async (id) =>
      task(id),
    );
    vi.mocked(api.conversations).mockImplementation(async (id) => page(id));
  });
  afterEach(() => {
    useConversationsStore().stop();
    vi.useRealTimers();
  });

  it("polls explicit work but never schedules periodic source refreshes", async () => {
    vi.mocked(api.refreshConversations).mockResolvedValueOnce(
      task("one", "running"),
    );
    const store = useConversationsStore();
    store.activate("one");
    await flushPromises();
    const shared = store.refresh();
    await vi.advanceTimersByTimeAsync(1999);
    expect(api.conversationRefresh).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await shared;
    expect(store.items).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(9999);
    expect(api.refreshConversations).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(api.refreshConversations).toHaveBeenCalledTimes(1);
  });

  it("queries cache on visibility restoration without source polling", async () => {
    const store = useConversationsStore();
    store.activate("one");
    await flushPromises();
    store.activate("one", false);
    await vi.advanceTimersByTimeAsync(40000);
    expect(api.refreshConversations).not.toHaveBeenCalled();
    store.activate("one", true);
    await flushPromises();
    expect(store.items).toHaveLength(1);
    expect(api.conversations).toHaveBeenCalledTimes(2);
  });

  it("discards a delayed old-account response without changing the new account", async () => {
    let release!: (result: ConversationPage) => void;
    vi.mocked(api.conversations).mockImplementation((id) =>
      id === "one"
        ? new Promise((resolve) => {
            release = resolve;
          })
        : Promise.resolve(page(id)),
    );
    const store = useConversationsStore();
    store.activate("one");
    await flushPromises();
    store.activate("two");
    await flushPromises();
    release(page("one"));
    await flushPromises();
    expect(store.items[0]?.conversation_id).toBe("conversation-two");
    expect(store.accountId).toBe("two");
  });

  it("preserves the archive on network failure and live account switch", async () => {
    const store = useConversationsStore();
    store.activate("one");
    await flushPromises();
    vi.mocked(api.refreshConversations).mockRejectedValueOnce(
      new Error("network"),
    );
    await store.refresh();
    expect(store.items).toHaveLength(1);
    expect(store.error).toContain("无法连接");
    vi.mocked(api.refreshConversations).mockResolvedValueOnce({
      ...task(),
      status: "failed",
      error: "account_changed_during_read",
    });
    await store.refresh();
    expect(store.items).toHaveLength(1);
    expect(store.accountLost).toBe(false);
    expect(store.error).toContain("已缓存内容仍可查看");
    await vi.advanceTimersByTimeAsync(40000);
    expect(api.refreshConversations).toHaveBeenCalledTimes(2);
  });

  it("loads another immutable page and restarts on expired cursor", async () => {
    vi.mocked(api.conversations)
      .mockResolvedValueOnce(page("one", "next"))
      .mockResolvedValueOnce(page("two", "expired"));
    const store = useConversationsStore();
    store.activate("one");
    await flushPromises();
    await store.loadMore();
    expect(store.items).toHaveLength(2);
    expect(api.conversations).toHaveBeenLastCalledWith("one", "next");
    vi.mocked(api.conversations).mockRejectedValueOnce(
      new ApiFailure("cursor_expired", ""),
    );
    await store.loadMore();
    await flushPromises();
    expect(api.refreshConversations).not.toHaveBeenCalled();
    expect(store.items).toHaveLength(1);
  });
});
