import type { Account, Snapshot, ScanTask } from "../src/api";

export const syntheticAccount: Account = {
  source_state: "online",
  revision: 0,
  sync_pending: false,
  sync_issues: [],
  account_id: "synthetic-account-1",
  wxid: "wxid_synthetic_owner",
  wechat_id: "synthetic_alias",
  nickname: "合成测试账号",
  status: "complete",
  reason: "",
  sources: {},
  avatar: null,
  avatar_status: "missing",
  avatar_reason: "",
  instances: [
    {
      pid: 100,
      created: "134000000000000001",
      session_id: 1,
      version: "4.1.15.56",
    },
  ],
};
export function snapshot(
  accounts: Account[] = [structuredClone(syntheticAccount)],
): Snapshot {
  return {
    scan_id: "synthetic-scan",
    scanned_at: "2026-10-28T05:00:00Z",
    schema_version: 2,
    scope: "current_windows_user_all_sessions",
    accounts,
    skipped_instances: [],
    client_instances: [],
    issues: [],
    summary: {
      confirmed_accounts: accounts.filter((a) => a.wxid).length,
      complete_accounts: accounts.filter((a) => a.status === "complete").length,
      avatar_available_accounts: 0,
      unresolved_instances: 0,
      coverage_complete: true,
    },
    exit_code: 0,
    observation: {},
  };
}
export function task(status: ScanTask["status"] = "completed"): ScanTask {
  return {
    scan_id: "synthetic-scan",
    status,
    started_at: "2026-10-28T05:00:00Z",
    exit_code: status === "failed" ? 1 : 0,
  };
}
