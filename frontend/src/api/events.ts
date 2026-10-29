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
  if (socket || !listeners.size || import.meta.env.MODE === "test") return;
  socket = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/events`,
  );
  socket.onopen = () => {
    retry = 1000;
    socket?.send(JSON.stringify({ type: "subscribe", account_ids: [] }));
  };
  socket.onmessage = ({ data }) => {
    try {
      const event = JSON.parse(String(data)) as ChangeEvent;
      if (!["ready", "changed", "resync", "task"].includes(event.type)) return;
      for (const listener of listeners) listener(event);
    } catch {
      /* Reconnect/ready performs an authoritative database query. */
    }
  };
  socket.onclose = () => {
    socket = null;
    if (listeners.size) timer = setTimeout(connect, retry);
    retry = Math.min(retry * 2, 30000);
  };
  socket.onerror = () => socket?.close();
}

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
