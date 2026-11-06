import { onScopeDispose, ref, watch } from "vue";
import { ApiFailure } from "./api";
import { authState } from "./auth";
import { isConnectionFailure, recoveryDelays } from "./connection";

// Only query callers use this helper. It never retries a write or source job.
export function useCacheRecovery(retry: () => void, stop: () => void) {
  const recovering = ref(false);
  const lastCheckedAt = ref<string | null>(null);
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;

  function cancel() {
    clearTimeout(timer);
    timer = undefined;
    controller?.abort();
    controller = undefined;
    attempt = 0;
  }
  function reset() {
    cancel();
    recovering.value = false;
    lastCheckedAt.value = null;
  }
  function begin() {
    clearTimeout(timer);
    timer = undefined;
    controller = new AbortController();
    return controller.signal;
  }
  function failed(cause: unknown) {
    recovering.value =
      isConnectionFailure(cause) ||
      (cause instanceof ApiFailure &&
        ["storage_unavailable", "service_stopping", "update_draining"].includes(
          cause.code,
        ));
    if (!recovering.value) cancel();
  }
  function success() {
    cancel();
    recovering.value = false;
    lastCheckedAt.value = new Date().toISOString();
  }
  function schedule() {
    if (
      !recovering.value ||
      timer ||
      document.hidden ||
      !authState.authenticated ||
      authState.invalidating
    )
      return;
    const delay =
      recoveryDelays[Math.min(attempt++, recoveryDelays.length - 1)]!;
    timer = setTimeout(() => {
      timer = undefined;
      if (
        !document.hidden &&
        authState.authenticated &&
        !authState.invalidating
      )
        retry();
    }, delay);
  }
  watch(
    () => authState.authenticated && !authState.invalidating,
    (allowed) => {
      if (!allowed) stop();
    },
    { flush: "sync" },
  );
  onScopeDispose(stop);
  return {
    recovering,
    lastCheckedAt,
    cancel,
    reset,
    begin,
    failed,
    success,
    schedule,
  };
}
