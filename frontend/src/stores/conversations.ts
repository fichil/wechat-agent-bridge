import { ref, onScopeDispose } from "vue";
import { onChange } from "../api/events";
import { defineStore } from "pinia";
import {
  api,
  ApiFailure,
  type Conversation,
  type ConversationPage,
} from "../api";
import { conversationErrorText, isAccountLost } from "../conversation-options";

export const useConversationsStore = defineStore("conversations", () => {
  const accountId = ref<string | null>(null);
  const items = ref<Conversation[]>([]);
  const page = ref<ConversationPage | null>(null);
  const selectedId = ref<string | null>(null);
  const refreshing = ref(false);
  const loadingMore = ref(false);
  const error = ref("");
  const accountLost = ref(false);
  let generation = 0;
  let enabled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pending: Promise<void> | null = null;
  let dirty = false;
  let wakePoll: (() => void) | null = null;

  function stop() {
    enabled = false;
    generation++;
    clearTimeout(timer);
    wakePoll?.();
    wakePoll = null;
    pending = null;
    refreshing.value = false;
    loadingMore.value = false;
  }

  function valid(ticket: number, id: string) {
    return enabled && generation === ticket && accountId.value === id;
  }

  function pollPause() {
    return new Promise<void>((resolve) => {
      const timeout = setTimeout(() => {
        wakePoll = null;
        resolve();
      }, 2000);
      wakePoll = () => {
        clearTimeout(timeout);
        resolve();
      };
    });
  }

  function fail(cause: unknown) {
    error.value = conversationErrorText(cause);
    if (isAccountLost(cause)) {
      accountLost.value = true;
      items.value = [];
      page.value = null;
      selectedId.value = null;
      enabled = false;
    }
  }

  function refresh(force = true): Promise<void> {
    if (pending) return pending;
    if (!enabled || !accountId.value || loadingMore.value)
      return Promise.resolve();
    clearTimeout(timer);
    dirty = false;
    const id = accountId.value;
    const ticket = generation;
    const previousCount = items.value.length;
    refreshing.value = true;
    pending = (async () => {
      try {
        let result = await api.conversations(id);
        if (!valid(ticket, id)) return;
        if (!items.value.length) {
          items.value = [...result.items];
          page.value = result;
        }
        if ((force || result.needs_sync) && result.source_state === "online") {
          let task = await api.refreshConversations(id);
          if (!valid(ticket, id)) return;
          while (task.status === "queued" || task.status === "running") {
            await pollPause();
            if (!valid(ticket, id)) return;
            task = await api.conversationRefresh(id, task.refresh_id);
            if (!valid(ticket, id)) return;
          }
          if (task.status === "failed")
            throw new ApiFailure(task.error || "conversation_read_failed", "");
          result = await api.conversations(id);
        }
        if (!valid(ticket, id)) return;
        let loaded = [...result.items];
        while (result.next_cursor && loaded.length < previousCount) {
          result = await api.conversations(id, result.next_cursor);
          if (!valid(ticket, id)) return;
          loaded.push(...result.items);
        }
        items.value = loaded;
        page.value = result;
        error.value = "";
        accountLost.value = false;
        if (
          selectedId.value &&
          !loaded.some((item) => item.conversation_id === selectedId.value)
        )
          selectedId.value = null;
      } catch (cause) {
        if (valid(ticket, id)) fail(cause);
      } finally {
        if (generation === ticket) {
          refreshing.value = false;
          pending = null;
          if (enabled && dirty)
            timer = setTimeout(() => void refresh(false), 150);
        }
      }
    })();
    return pending;
  }

  function activate(id: string, visible = true) {
    stop();
    if (accountId.value !== id) {
      items.value = [];
      page.value = null;
      selectedId.value = null;
      error.value = "";
      accountLost.value = false;
    }
    accountId.value = id;
    enabled = visible;
    if (enabled) void refresh(false);
  }

  async function loadMore() {
    if (
      !enabled ||
      !accountId.value ||
      !page.value?.next_cursor ||
      refreshing.value ||
      loadingMore.value
    )
      return;
    const ticket = generation;
    const id = accountId.value;
    loadingMore.value = true;
    clearTimeout(timer);
    let expired = false;
    try {
      const result = await api.conversations(id, page.value.next_cursor);
      if (!valid(ticket, id)) return;
      items.value.push(...result.items);
      page.value = result;
      error.value = "";
    } catch (cause) {
      if (!valid(ticket, id)) return;
      if (cause instanceof ApiFailure && cause.code === "cursor_expired")
        expired = true;
      else fail(cause);
    } finally {
      if (generation === ticket) {
        loadingMore.value = false;
        if (enabled) {
          if (expired) void refresh(false);
          else if (dirty) timer = setTimeout(() => void refresh(false), 150);
        }
      }
    }
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
        event.resources?.includes("conversations")
      ) {
        dirty = true;
        clearTimeout(timer);
        timer = setTimeout(() => void refresh(false), 150);
      }
    }),
  );

  return {
    accountId,
    items,
    page,
    selectedId,
    refreshing,
    loadingMore,
    error,
    accountLost,
    activate,
    stop,
    refresh,
    loadMore,
  };
});
