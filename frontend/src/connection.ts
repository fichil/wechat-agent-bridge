// Transport failures are distinct from source/account state and authentication.
export class ConnectionFailure extends Error {
  constructor(public code: "backend_timeout" | "network_unavailable") {
    super(
      code === "backend_timeout"
        ? "连接中断：后台响应超时，请稍后重试。"
        : "连接中断：无法连接后台，请检查服务或网络。",
    );
  }
}

export function isConnectionFailure(cause: unknown): boolean {
  return cause instanceof ConnectionFailure;
}

export const recoveryDelays = [2000, 5000, 10000, 30000] as const;
