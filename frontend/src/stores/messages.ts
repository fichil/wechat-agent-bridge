import { ref, onScopeDispose } from "vue";
import { onChange } from "../api/events";
import { defineStore } from "pinia";
import {
  api,
  ApiFailure,
  type Message,
  type MessagePage,
  type MessageImage,
  type MessageReadTask,
} from "../api";
import { isAccountLost } from "../conversation-options";

export function messageError(cause: unknown): string {
  const code = cause instanceof ApiFailure ? cause.code : "";
  if (code === "cursor_expired" || code === "message_task_expired")
    return "历史读取已过期，请刷新聊天记录。";
  if (isAccountLost(cause)) return "账号已退出或切换，请重新识别。";
  if (code === "conversation_not_found") return "会话已失效，请刷新左侧列表。";
  return "聊天记录暂未更新，请稍后重试。";
}

export const useMessagesStore = defineStore("messages", () => {
  const accountId = ref<string | null>(null);
  const conversationId = ref<string | null>(null);
  const items = ref<Message[]>([]);
  const page = ref<MessagePage | null>(null);
  const images = ref<Record<string, MessageImage>>({});
  const loadingImages = ref<Record<string, boolean>>({});
  const loading = ref(false);
  const loadingMore = ref(false);
  const error = ref("");
  const accountLost = ref(false);
  const arrivalCount = ref(0);
  const olderCursor = ref<string | null>(null);
  let newerCursor: string | null = null;
  let generation = 0;
  let enabled = false;
  let replaceOnRefresh = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pending: Promise<void> | null = null;
  let dirty = false;
  let imageQueue: Promise<void> = Promise.resolve();
  const pauses = new Set<() => void>();

  function valid(ticket: number) {
    return enabled && ticket === generation;
  }
  function stop() {
    enabled = false;
    generation++;
    clearTimeout(timer);
    for (const wake of pauses) wake();
    pauses.clear();
    pending = null;
    imageQueue = Promise.resolve();
    loading.value = loadingMore.value = false;
    loadingImages.value = {};
  }
  function pause() {
    return new Promise<void>((resolve) => {
      const wake = () => {
        clearTimeout(id);
        pauses.delete(wake);
        resolve();
      };
      const id = setTimeout(wake, 1000);
      pauses.add(wake);
    });
  }
  function fail(cause: unknown) {
    error.value = messageError(cause);
    if (isAccountLost(cause)) {
      stop();
      accountLost.value = true;
      items.value = [];
      page.value = null;
      images.value = {};
      olderCursor.value = newerCursor = null;
    }
  }
  async function finish(
    task: MessageReadTask,
    ticket: number,
  ): Promise<MessageReadTask | null> {
    const deadline = Date.now() + 300_000;
    while (valid(ticket)) {
      // Task queries read persisted results, including jobs completed by another tab.
      try {
        task = await api.messageTask(task.account_id, task.task_id);
      } catch (cause) {
        if (
          !(cause instanceof ApiFailure) ||
          cause.code !== "conversation_busy"
        )
          throw cause;
        if (Date.now() >= deadline) throw cause;
        await pause();
        continue;
      }
      if (!valid(ticket)) return null;
      if (task.status === "failed")
        throw new ApiFailure(task.error || "message_read_failed", "");
      if (task.status === "completed") return task;
      if (Date.now() >= deadline) throw new ApiFailure("message_timeout", "");
      await pause();
    }
    return null;
  }
  async function read(cursor: string | null, ticket: number, force = false) {
    const cached = await api.messages(
      accountId.value!,
      conversationId.value!,
      cursor,
    );
    if (!valid(ticket)) return null;
    if ((!cached.needs_sync && !force) || cached.source_state !== "online")
      return cached;
    if (!cursor) merge(cached);
    const task = await api.readMessages(
      accountId.value!,
      conversationId.value!,
      cursor,
      force,
    );
    if (!valid(ticket)) return null;
    return (await finish(task, ticket))?.page || null;
  }
  function merge(result: MessagePage, prepend = false, latestWindow = false) {
    const replacements = new Map(
      result.items.map((item) => [item.message_id, item]),
    );
    const known = new Set(items.value.map((item) => item.message_id));
    const existing = items.value.map(
      (item) => replacements.get(item.message_id) || item,
    );
    const added = result.items.filter((item) => !known.has(item.message_id));
    if (page.value && !prepend) arrivalCount.value += added.length;
    items.value = latestWindow
      ? [
          ...existing.filter((item) => !replacements.has(item.message_id)),
          ...result.items,
        ]
      : prepend
        ? [...added, ...existing]
        : [...existing, ...added];
    if (items.value.every((item) => item.sort_key))
      items.value.sort((a, b) =>
        a.sort_key! < b.sort_key! ? -1 : a.sort_key! > b.sort_key! ? 1 : 0,
      );
    page.value = result;
  }
  function schedule() {
    // Updates are driven by committed database notifications, not source polling.
    if (enabled && dirty) timer = setTimeout(() => void refresh(), 150);
  }
  function refresh(force = false): Promise<void> {
    if (pending) return pending;
    if (!enabled || !conversationId.value || loadingMore.value)
      return Promise.resolve();
    clearTimeout(timer);
    dirty = false;
    const ticket = generation;
    loading.value = true;
    pending = (async () => {
      try {
        if (newerCursor && !force) {
          let more = true;
          while (more && valid(ticket)) {
            const delta = await api.messages(
              accountId.value!,
              conversationId.value!,
              newerCursor,
            );
            if (!valid(ticket)) return;
            merge(delta);
            newerCursor = delta.newer_cursor;
            more = delta.has_more;
          }
        }
        const latest = await read(null, ticket, force);
        if (!latest || !valid(ticket)) return;
        if (replaceOnRefresh) {
          items.value = [];
          page.value = null;
          images.value = {};
          arrivalCount.value = 0;
          replaceOnRefresh = false;
        }
        const initial = !page.value;
        merge(latest, false, true);
        if (initial || !olderCursor.value)
          olderCursor.value = latest.older_cursor;
        if (!newerCursor) newerCursor = latest.newer_cursor;
        error.value = "";
      } catch (cause) {
        if (!valid(ticket)) return;
        if (cause instanceof ApiFailure && cause.code === "cursor_expired") {
          // Never silently join two windows across an unqueried gap.
          stop();
          error.value = "历史游标已过期。请点击刷新，重新加载最新记录。";
        } else fail(cause);
      } finally {
        if (generation === ticket) {
          loading.value = false;
          pending = null;
          schedule();
        }
      }
    })();
    return pending;
  }
  function activate(
    account: string,
    conversation: string | null,
    visible = true,
  ) {
    const changed =
      accountId.value !== account || conversationId.value !== conversation;
    stop();
    accountId.value = account;
    conversationId.value = conversation;
    if (changed) {
      replaceOnRefresh = false;
      items.value = [];
      page.value = null;
      images.value = {};
      olderCursor.value = newerCursor = null;
      error.value = "";
      accountLost.value = false;
      arrivalCount.value = 0;
    }
    enabled = visible && !!conversation && !accountLost.value;
    if (enabled) void refresh();
  }
  async function reload() {
    const account = accountId.value,
      conversation = conversationId.value;
    if (!account || !conversation) return;
    stop();
    replaceOnRefresh = true;
    newerCursor = null;
    error.value = "";
    accountLost.value = false;
    activate(account, conversation, !document.hidden);
    // Explicit refresh can reconcile a cached recent window.
    await pending;
    if (enabled) await refresh(true);
  }
  async function loadOlder() {
    if (!enabled || !olderCursor.value || loading.value || loadingMore.value)
      return;
    clearTimeout(timer);
    const ticket = generation;
    loadingMore.value = true;
    try {
      const result = await read(olderCursor.value, ticket);
      if (!result || !valid(ticket)) return;
      merge(result, true);
      olderCursor.value = result.older_cursor;
      error.value = "";
    } catch (cause) {
      if (valid(ticket)) {
        fail(cause);
        if (cause instanceof ApiFailure && cause.code === "cursor_expired")
          stop();
      }
    } finally {
      if (generation === ticket) {
        loadingMore.value = false;
        schedule();
      }
    }
  }
  function loadImage(messageId: string, retry = false, isVisible = () => true) {
    if (
      !enabled ||
      !isVisible() ||
      loadingImages.value[messageId] ||
      (!retry && images.value[messageId])
    )
      return;
    const ticket = generation,
      account = accountId.value!,
      conversation = conversationId.value!;
    loadingImages.value[messageId] = true;
    imageQueue = imageQueue.then(async () => {
      try {
        if (!valid(ticket) || !isVisible()) return;
        const cached = await api.cachedImage(account, conversation, messageId);
        if (!valid(ticket)) return;
        let result = cached;
        if (cached.needs_sync) {
          if (!isVisible()) return;
          const task = await api.readMessageImage(
            account,
            conversation,
            messageId,
          );
          if (!valid(ticket)) return;
          const loaded = (await finish(task, ticket))?.image;
          if (!loaded || !valid(ticket)) return;
          result = loaded;
        }
        const next = { ...images.value, [messageId]: result };
        let bytes = Object.values(next).reduce(
          (n, item) => n + (item.data_base64?.length || 0),
          0,
        );
        for (const key of Object.keys(next)) {
          if (bytes <= 32 * 1024 * 1024 && Object.keys(next).length <= 64)
            break;
          if (key !== messageId) {
            bytes -= next[key]?.data_base64?.length || 0;
            delete next[key];
          }
        }
        images.value = next;
      } catch (cause) {
        if (!valid(ticket)) return;
        if (isAccountLost(cause)) fail(cause);
        else
          images.value[messageId] = {
            message_id: messageId,
            status: "unavailable",
            reason:
              cause instanceof ApiFailure ? cause.code : "image_read_failed",
            needs_sync: true,
          };
      } finally {
        if (valid(ticket)) delete loadingImages.value[messageId];
      }
    });
  }
  onScopeDispose(
    onChange((event) => {
      if (
        !enabled ||
        (event.account_id && event.account_id !== accountId.value)
      )
        return;
      if (
        event.type === "ready" ||
        event.type === "resync" ||
        event.resources?.includes("messages")
      ) {
        dirty = true;
        clearTimeout(timer);
        timer = setTimeout(() => void refresh(), 150);
      }
    }),
  );

  return {
    accountId,
    conversationId,
    items,
    page,
    images,
    loadingImages,
    loading,
    loadingMore,
    error,
    accountLost,
    arrivalCount,
    olderCursor,
    activate,
    stop,
    refresh,
    reload,
    loadOlder,
    loadImage,
  };
});
