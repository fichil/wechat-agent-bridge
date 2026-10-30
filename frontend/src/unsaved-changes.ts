import { onBeforeUnmount, type Ref } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate } from "vue-router";
import { ElMessageBox } from "element-plus";
import "element-plus/es/components/message-box/style/css";
import { authState } from "./auth";

export function useUnsavedChanges(
  dirty: Ref<boolean>,
  save: () => Promise<boolean>,
  busy: Ref<boolean>,
) {
  let pending: Promise<boolean> | null = null;
  async function decide() {
    if (authState.invalidating) return true;
    if (busy.value) return false;
    if (!dirty.value) return true;
    try {
      await ElMessageBox.confirm(
        "当前修改尚未保存。保存后离开，或放弃本次修改？",
        "未保存的修改",
        {
          confirmButtonText: "保存并离开",
          cancelButtonText: "放弃修改",
          distinguishCancelAndClose: true,
          closeOnClickModal: false,
        },
      );
      return await save();
    } catch (action) {
      return action === "cancel";
    }
  }
  function beforeLeave() {
    if (!pending)
      pending = decide().finally(() => {
        pending = null;
      });
    return pending;
  }
  function unload(event: BeforeUnloadEvent) {
    if (authState.invalidating) return;
    if (dirty.value || busy.value) {
      event.preventDefault();
      event.returnValue = "";
    }
  }
  window.addEventListener("beforeunload", unload);
  onBeforeUnmount(() => window.removeEventListener("beforeunload", unload));
  onBeforeRouteLeave(beforeLeave);
  onBeforeRouteUpdate(beforeLeave);
  return beforeLeave;
}
