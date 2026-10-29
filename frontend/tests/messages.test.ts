import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { flushPromises } from "@vue/test-utils";
import { api, ApiFailure, type MessagePage } from "../src/api";
import { useMessagesStore } from "../src/stores/messages";
import { message, messagePage, messageTask } from "./message-fixtures";
vi.mock("../src/api", async (original) => ({
  ...(await original<typeof import("../src/api")>()),
  api: {
    messages: vi.fn(),
    cachedImage: vi.fn(),
    readMessages: vi.fn(),
    readMessageImage: vi.fn(),
    messageTask: vi.fn(),
  },
}));
describe("persistent history lifecycle", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers();
    setActivePinia(createPinia());
    vi.mocked(api.messages).mockImplementation(
      async (account, chat, cursor) => ({
        ...messagePage(account, chat),
        has_more: false,
        ...(cursor === "newer" ? { items: [] } : {}),
      }),
    );
    vi.mocked(api.readMessages).mockImplementation(async (account, chat) =>
      messageTask(messagePage(account, chat)),
    );
    vi.mocked(api.messageTask).mockImplementation(async (account) =>
      messageTask(messagePage(account)),
    );
    vi.mocked(api.cachedImage).mockResolvedValue({
      message_id: "message-2",
      status: "unavailable",
      reason: "image_not_cached",
      needs_sync: true,
    });
  });
  afterEach(() => {
    useMessagesStore().stop();
    vi.useRealTimers();
  });
  it("serves cached history without starting source work or periodic reads", async () => {
    const store = useMessagesStore();
    store.activate("one", "chat");
    await flushPromises();
    expect(store.items).toHaveLength(2);
    expect(api.readMessages).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(40000);
    expect(api.messages).toHaveBeenCalledTimes(1);
  });
  it("keeps visible messages when WeChat exits during an explicit refresh", async () => {
    const store = useMessagesStore();
    store.activate("one", "chat");
    await flushPromises();
    vi.mocked(api.messageTask).mockResolvedValueOnce({
      ...messageTask(messagePage()),
      status: "failed",
      error: "process_exited",
    });
    await store.reload();
    expect(store.items).toHaveLength(2);
    expect(store.accountLost).toBe(false);
    expect(store.error).toBeTruthy();
  });
  it("loads missing history once, then deduplicates an older database page", async () => {
    vi.mocked(api.messages).mockResolvedValueOnce({
      ...messagePage(),
      items: [],
      needs_sync: true,
      coverage: "not_synced",
    });
    const store = useMessagesStore();
    store.activate("one", "chat");
    await flushPromises();
    expect(api.readMessages).toHaveBeenCalledTimes(1);
    vi.mocked(api.messages).mockResolvedValueOnce(
      messagePage("one", "chat", [message(1), message(2)]),
    );
    await store.loadOlder();
    expect(store.items.map((m) => m.message_id)).toEqual([
      "message-1",
      "message-2",
      "message-3",
    ]);
  });
  it("keeps offline cached history without trying source reads", async () => {
    vi.mocked(api.messages).mockResolvedValue({
      ...messagePage(),
      source_state: "offline",
      needs_sync: true,
    });
    const store = useMessagesStore();
    store.activate("one", "chat");
    await flushPromises();
    expect(store.items).toHaveLength(2);
    expect(api.readMessages).not.toHaveBeenCalled();
  });
  it("ignores delayed results after account switching", async () => {
    let resolve!: (page: MessagePage) => void;
    vi.mocked(api.messages).mockReturnValueOnce(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const store = useMessagesStore();
    store.activate("one", "old");
    store.activate("two", "new");
    await flushPromises();
    resolve(messagePage("one", "old"));
    await flushPromises();
    expect(store.page?.account_id).toBe("two");
  });
  it("drains database changes including low-sort late arrivals before latest window", async () => {
    const store = useMessagesStore();
    store.activate("one", "chat");
    await flushPromises();
    vi.mocked(api.messages)
      .mockResolvedValueOnce({
        ...messagePage("one", "chat", [message(1)]),
        has_more: true,
      })
      .mockResolvedValueOnce({
        ...messagePage("one", "chat", [message(4)]),
        has_more: false,
      })
      .mockResolvedValueOnce(
        messagePage("one", "chat", [message(2), message(3), message(4)]),
      );
    await store.refresh();
    expect(store.items.map((m) => m.message_id)).toEqual([
      "message-1",
      "message-2",
      "message-3",
      "message-4",
    ]);
    expect(store.arrivalCount).toBe(2);
    expect(api.readMessages).not.toHaveBeenCalled();
  });
  it("preserves displayed rows after a query failure", async () => {
    const store = useMessagesStore();
    store.activate("one", "chat");
    await flushPromises();
    vi.mocked(api.messages).mockRejectedValueOnce(
      new ApiFailure("storage_unavailable", ""),
    );
    await store.refresh();
    expect(store.items).toHaveLength(2);
    expect(store.error).not.toBe("");
  });
  it("returns an offline cached image without source task", async () => {
    vi.mocked(api.cachedImage).mockResolvedValue({
      message_id: "message-2",
      status: "available",
      data_base64: "eA==",
      mime_type: "image/png",
      needs_sync: false,
    });
    const store = useMessagesStore();
    store.activate("one", "chat");
    await flushPromises();
    store.loadImage("message-2");
    await flushPromises();
    expect(store.images["message-2"]?.status).toBe("available");
    expect(api.readMessageImage).not.toHaveBeenCalled();
  });
  it("preserves content when an older cursor expires", async () => {
    const store = useMessagesStore();
    store.activate("one", "chat");
    await flushPromises();
    vi.mocked(api.messages).mockRejectedValueOnce(
      new ApiFailure("cursor_expired", ""),
    );
    await store.loadOlder();
    expect(store.items).toHaveLength(2);
    expect(store.error).toContain("过期");
  });
});
