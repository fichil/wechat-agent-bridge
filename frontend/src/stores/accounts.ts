import { computed, ref, onScopeDispose } from "vue";
import { onChange } from "../api/events";
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
  const selectedId = ref(sessionStorage.getItem(selectionKey));
  const accounts = computed(() => snapshot.value?.accounts ?? []);
  let pending: Promise<void> | null = null;

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
        snapshot.value = await api.accounts();
        if (snapshot.value.exit_code === 1)
          error.value = reasonText(snapshot.value.error ?? "probe_failed");
        if (
          selectedId.value &&
          !accounts.value.some((a) => a.account_id === selectedId.value)
        ) {
          select(null);
          notice.value = "上次选择的账号已失效，请重新选择。";
        }
      } catch (cause) {
        error.value =
          cause instanceof ApiFailure
            ? cause.message
            : "无法连接本地服务，请检查服务后重新识别。";
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
      snapshot.value = await api.accounts();
      initialized.value = true;
      if (!accounts.value.length) void scan();
    } catch {
      await scan();
    }
  }

  onScopeDispose(
    onChange((event) => {
      if (
        event.type === "ready" ||
        event.type === "resync" ||
        event.resources?.includes("accounts")
      ) {
        void api
          .accounts()
          .then((result) => {
            snapshot.value = result;
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
    selectedId,
    accounts,
    select,
    scan,
    initialize,
    enter,
  };
});
