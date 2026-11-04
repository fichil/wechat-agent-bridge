import { computed, onBeforeUnmount, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type LogClearTask } from "./api";
import { onChange } from "./api/events";
import { settingsError } from "./role-cards";

type Scope = "account" | "global";
export const clearScopeName = (scope: Scope) =>
  scope === "account" ? "当前账号日志" : "全局测试／扫描摘要";
export const clearActive = (task?: LogClearTask | null) =>
  task?.status === "queued" || task?.status === "running";

export function useLogClear(
  account: () => string,
  onFinished: () => Promise<void>,
) {
  const tasks = ref<Partial<Record<Scope, LogClearTask | null>>>({});
  const starting = ref(false);
  const error = ref("");
  const queryError = ref("");
  const busy = computed(
    () => starting.value || Object.values(tasks.value).some(clearActive),
  );
  let generation = 0,
    ticket = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let queryingVersion: number | null = null;
  let uncertain: { scope: Scope; previousId?: string } | null = null;
  const finished = new Set<string>();

  function accept(scope: Scope, task: LogClearTask | null) {
    tasks.value[scope] = task;
    if (task && !clearActive(task) && !finished.has(task.task_id)) {
      finished.add(task.task_id);
      void onFinished();
    }
  }
  function schedule() {
    if (!timer)
      timer = setTimeout(() => {
        timer = undefined;
        void query();
      }, 1000);
  }
  async function query() {
    if (queryingVersion === generation) {
      schedule();
      return;
    }
    const version = generation,
      request = ++ticket,
      aid = account();
    queryingVersion = version;
    const results = await Promise.allSettled(
      (["account", "global"] as const).map(async (scope) => ({
        scope,
        result: await api.logClearTask(aid, scope),
      })),
    );
    if (version !== generation) return;
    queryingVersion = null;
    if (request !== ticket) {
      schedule();
      return;
    }
    queryError.value = "";
    for (const result of results) {
      if (result.status === "fulfilled") {
        if (
          uncertain?.scope === result.value.scope &&
          result.value.result.task &&
          result.value.result.task.task_id !== uncertain.previousId
        ) {
          uncertain = null;
          error.value = "";
        }
        accept(result.value.scope, result.value.result.task ?? null);
      } else
        queryError.value =
          "清理进度暂时无法查询，后台任务可能仍在执行，正在重试。";
    }
    if (queryError.value || Object.values(tasks.value).some(clearActive))
      schedule();
  }
  async function clear(scope: Scope) {
    if (busy.value || queryError.value) return;
    const version = generation,
      aid = account();
    starting.value = true;
    error.value = "";
    uncertain = null;
    const previousId = tasks.value[scope]?.task_id;
    try {
      await ElMessageBox.confirm(
        `清空${clearScopeName(scope)}中的已结束日志？后台将分批清理，切换页面后仍会继续；聊天、记忆、运行中的任务及清理期间新增或更新的日志会保留。`,
        "清空日志",
        {
          confirmButtonText: "清空",
          cancelButtonText: "取消",
          type: "warning",
        },
      );
      if (version !== generation || aid !== account()) return;
      ++ticket; // An earlier discovery response cannot replace this task.
      clearTimeout(timer);
      timer = undefined;
      try {
        const task = await api.startLogClear(aid, scope);
        if (version !== generation) return;
        ++ticket;
        accept(scope, task);
        schedule();
      } catch (cause) {
        if (version !== generation) return;
        error.value = "未能确认清理是否已启动：" + settingsError(cause);
        uncertain = { scope, previousId };
        queryError.value = "正在查询后台清理结果，请勿重复提交。";
        schedule();
      }
    } catch (cause) {
      if (version === generation && cause !== "cancel" && cause !== "close")
        error.value = settingsError(cause);
    } finally {
      if (version === generation) starting.value = false;
    }
  }
  watch(
    account,
    () => {
      generation++;
      ticket++;
      clearTimeout(timer);
      timer = undefined;
      tasks.value = {};
      uncertain = null;
      finished.clear();
      starting.value = false;
      error.value = queryError.value = "";
      void query();
    },
    { immediate: true },
  );
  const unsubscribe = onChange((event) => {
    if (event.account_id && event.account_id !== account()) return;
    if (
      event.type === "ready" ||
      event.type === "resync" ||
      event.resources?.includes("log_clear_tasks")
    )
      schedule();
  });
  onBeforeUnmount(() => {
    generation++;
    ticket++;
    clearTimeout(timer);
    unsubscribe();
  });
  return { tasks, busy, error, queryError, clear };
}
