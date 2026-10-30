import { watch } from "vue";
import { authState, invalidateAuthentication } from "../auth";
export type ChangeEvent = {
  type: "ready" | "changed" | "resync" | "task";
  account_id?: string;
  resources?: string[];
  conversation_ids?: string[];
  revision?: number;
};
const listeners = new Set<(event: ChangeEvent) => void>();
let socket: WebSocket | null = null;
let retry = 1000;
let timer: ReturnType<typeof setTimeout> | undefined;

function connect() {
  if (
    socket ||
    !listeners.size ||
    !authState.authenticated ||
    authState.invalidating ||
    import.meta.env.MODE === "test"
  )
    return;
  socket = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/events`,
  );
  socket.onopen = () => {
    retry = 1000;
    socket?.send(JSON.stringify({ type: "subscribe", account_ids: [] }));
  };
  socket.onmessage = ({ data }) => {
    if (!authState.authenticated || authState.invalidating) return;
    try {
      const event = JSON.parse(String(data)) as ChangeEvent;
      if (!["ready", "changed", "resync", "task"].includes(event.type)) return;
      for (const listener of listeners) listener(event);
    } catch {
      /* Reconnect/ready performs an authoritative database query. */
    }
  };
  socket.onclose = (event) => {
    socket = null;
    if (event.code === 4401) {
      invalidateAuthentication();
      return;
    }
    if (listeners.size && authState.authenticated)
      timer = setTimeout(connect, retry);
    retry = Math.min(retry * 2, 30000);
  };
  socket.onerror = () => socket?.close();
}

watch(
  () => authState.authenticated,
  (loggedIn) => {
    if (loggedIn) connect();
    else {
      clearTimeout(timer);
      socket?.close();
      socket = null;
    }
  },
);

export function onChange(listener: (event: ChangeEvent) => void) {
  listeners.add(listener);
  connect();
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      clearTimeout(timer);
      socket?.close();
    }
  };
}
