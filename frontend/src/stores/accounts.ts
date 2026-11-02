import { computed, ref, onScopeDispose } from "vue";
import { onChange, accountDeleted } from "../api/events";
import { defineStore } from "pinia";
import { api, ApiFailure, type Account, type Snapshot } from "../api";
import { reasonText } from "../reasons";

const selectionKey = "bridge.selected-account";
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export const useAccountsStore = defineStore("accounts", () => {
  const snapshot = ref<Snapshot | null>(null);
  const scanning = ref(false);
  const initialized = ref(false);
  const error = ref("");
  const notice = ref("");
  const connectionError = ref("");
  const lastCheckedAt = ref<string | null>(null);
  const selectedId = ref(sessionStorage.getItem(selectionKey));
  const accounts = computed(() => snapshot.value?.accounts ?? []);
  let pending: Promise<void> | null = null;
  let refreshGeneration = 0;
  let refreshPending: Promise<void> | null = null;
  const deletedIds = new Set<string>();
  async function refresh() {
    if (refreshPending) return refreshPending;
    const generation = ++refreshGeneration;
    refreshPending = (async () => {
      try {
        const result = await api.accounts();
        if (generation === refreshGeneration) {
          accept(result);
          connectionError.value = "";
          lastCheckedAt.value = new Date().toISOString();
        }
      } catch (cause) {
        if (generation === refreshGeneration)
          connectionError.value =
            cause instanceof Error
              ? cause.message
              : "连接中断，请检查后台服务。";
        throw cause;
      }
    })();
    try {
      await refreshPending;
    } finally {
      refreshPending = null;
    }
  }
  function accept(result: Snapshot) {
    const missing = accounts.value
      .filter(
        (a) =>
          a.account_id &&
          !deletedIds.has(a.account_id) &&
          !result.accounts.some((next) => next.account_id === a.account_id),
      )
      .map((a) => a.account_id!);
    result.accounts = result.accounts.filter(
      (a) => !a.account_id || !deletedIds.has(a.account_id),
    );
    snapshot.value = result;
    for (const id of missing) {
      deletedIds.add(id);
      accountDeleted(id);
    }
    if (
      selectedId.value &&
      !result.accounts.some((a) => a.account_id === selectedId.value)
    ) {
      select(null);
      notice.value = "上次选择的账号已失效，请重新选择。";
    }
  }
  async function remove(account: Account) {
    if (!account.account_id) return;
    error.value = "";
    try {
      await api.deleteAccount(account.account_id, account.revision ?? 0);
      refreshGeneration++;
      deletedIds.add(account.account_id);
      if (snapshot.value) accept(snapshot.value);
      accountDeleted(account.account_id);
      notice.value = "账号本地记录已删除。";
    } catch (cause) {
      error.value =
        cause instanceof Error ? cause.message : "删除失败，请刷新账号状态。";
    } finally {
      try {
        await refresh();
      } catch {
        /* Retain the deletion fence if refresh fails. */
      }
    }
  }

  function select(id: string | null) {
    selectedId.value = id;
    if (id) sessionStorage.setItem(selectionKey, id);
    else sessionStorage.removeItem(selectionKey);
  }

  function scan(): Promise<void> {
    if (pending) return pending;
    scanning.value = true;
    error.value = "";
    notice.value = "";
    pending = (async () => {
      try {
        let task = await api.startScan();
        while (task.status === "running") {
          await pause(2_000);
          task = await api.scan(task.scan_id);
        }
        // Another tab may have completed a newer scan. The latest snapshot is authoritative.
        await refresh();
        if (snapshot.value?.exit_code === 1)
          error.value = reasonText(snapshot.value?.error ?? "probe_failed");
        if (
          selectedId.value &&
          !accounts.value.some((a) => a.account_id === selectedId.value)
        ) {
          select(null);
          notice.value = "上次选择的账号已失效，请重新选择。";
        }
      } catch (cause) {
        if (cause instanceof ApiFailure) error.value = cause.message;
        else
          connectionError.value =
            "连接中断：无法连接本地服务，请检查服务后重新识别。";
      } finally {
        initialized.value = true;
        scanning.value = false;
        pending = null;
      }
    })();
    return pending;
  }

  async function initialize() {
    if (initialized.value) return;
    try {
      await refresh();
      initialized.value = true;
      if (!accounts.value.length) void scan();
    } catch {
      initialized.value = true;
    }
  }

  const polling = setInterval(() => {
    if (initialized.value && !scanning.value) void refresh().catch(() => {});
  }, 10_000);
  onScopeDispose(() => clearInterval(polling));

  onScopeDispose(
    onChange((event) => {
      if (event.resources?.includes("account_deleted") && event.account_id) {
        deletedIds.add(event.account_id);
        if (snapshot.value) accept(snapshot.value);
      }
      if (
        event.type === "ready" ||
        event.type === "resync" ||
        event.resources?.includes("accounts")
      ) {
        void refresh()
          .then(() => {
            initialized.value = true;
          })
          .catch(() => {});
      }
    }),
  );

  async function enter(id: string): Promise<Account | null> {
    try {
      const account = await api.account(id);
      if (!account.account_id || !account.wxid)
        throw new ApiFailure("account_not_found", "账号已失效，请重新识别。");
      select(id);
      return account;
    } catch (cause) {
      select(null);
      notice.value =
        cause instanceof ApiFailure
          ? cause.message
          : "本地服务不可用，请重新识别。";
      return null;
    }
  }

  return {
    snapshot,
    scanning,
    initialized,
    error,
    notice,
    connectionError,
    lastCheckedAt,
    selectedId,
    accounts,
    select,
    scan,
    initialize,
    enter,
    remove,
  };
});
