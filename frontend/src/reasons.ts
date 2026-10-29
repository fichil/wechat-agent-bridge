const reasons: Record<string, string> = {
  process_metadata_access_denied:
    "没有权限读取该微信实例，请在同一 Windows 用户下运行。",
  process_access_denied: "没有权限读取该微信实例。",
  client_version_unsupported: "当前微信版本暂不支持。",
  instance_timeout: "此实例识别超时，请重试。",
  total_timeout: "本轮识别已达到时间预算。",
  login_state_uncertain: "暂时无法确认登录状态。",
  account_changed_during_read: "识别期间账号发生变化，请重新识别。",
  process_set_changed_during_scan: "识别期间微信实例发生变化，请重新识别。",
  self_contact_fields_missing: "账号已确认，部分资料暂未取得。",
  cross_instance_fields_conflict: "同一账号的不同实例资料不一致。",
  probe_failed: "识别失败，请重试或查看本地服务是否正常。",
  probe_dependency_missing: "后端缺少识别依赖，请重新安装后端依赖。",
  windows_x64_required: "账号识别需要 Windows x64 环境。",
  scan_expired: "识别任务已失效，请重新识别。",
  avatar_instances_conflict: "不同实例的头像不一致。",
  avatar_not_found: "暂无本地缓存头像。",
};

export function reasonText(code?: string | null): string {
  if (!code) return "";
  if (reasons[code]) return reasons[code];
  if (code.includes("access_denied"))
    return "读取权限不足，暂时无法识别该实例。";
  return `暂未取得完整信息（${code}）`;
}

export function formatTime(value?: string | null) {
  return value
    ? new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "medium",
        timeStyle: "medium",
        timeZone: "Asia/Shanghai",
      }).format(new Date(value))
    : "尚未识别";
}
