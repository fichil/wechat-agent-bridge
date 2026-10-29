import { ApiFailure, type Exclusions } from "./api";

export const exclusionOptions: {
  key: keyof Exclusions;
  label: string;
  description: string;
}[] = [
  {
    key: "service_accounts",
    label: "服务号",
    description: "隐藏服务号分类入口及其中的会话",
  },
  {
    key: "official_accounts",
    label: "公众号",
    description: "隐藏公众号分类入口及其中的会话",
  },
  {
    key: "service_notifications",
    label: "服务通知",
    description: "隐藏服务通知会话",
  },
  {
    key: "file_transfer_assistant",
    label: "文件传输助手",
    description: "隐藏文件传输助手会话",
  },
  {
    key: "folded_chats",
    label: "折叠群聊",
    description: "隐藏折叠的聊天入口及其中的群聊",
  },
  { key: "wechat_pay", label: "微信支付", description: "单独控制微信支付会话" },
  {
    key: "enterprise_contacts",
    label: "企业微信联系人",
    description: "隐藏名称后有企业标识的联系人，普通昵称中的 @ 不受影响",
  },
];

export function defaultExclusions(): Exclusions {
  return Object.fromEntries(
    exclusionOptions.map(({ key }) => [key, true]),
  ) as Exclusions;
}

const offlineCodes = new Set([
  "source_offline",
  "account_changed_during_read",
  "process_exited",
  "process_instance_changed",
  "process_owner_changed",
  "process_time_unreadable",
  "account_configuration_missing_or_ambiguous",
  "account_directory_missing_or_ambiguous",
]);

export function isAccountLost(cause: unknown) {
  // Losing a live process never invalidates a persistent archive identity.
  return cause instanceof ApiFailure && cause.code === "account_not_found";
}

export function conversationErrorText(cause: unknown): string {
  if (isAccountLost(cause)) return "微信账号已退出或切换，请重新识别。";
  if (!(cause instanceof ApiFailure))
    return "无法连接本地服务，请检查服务后重试。";
  if (offlineCodes.has(cause.code))
    return "微信已离线或切换，已缓存内容仍可查看。";
  if (
    cause.code === "conversation_timeout" ||
    cause.code === "instance_timeout"
  )
    return "读取会话超时，请稍后重试。";
  if (
    cause.code === "client_version_unsupported" ||
    cause.code === "conversation_schema_unsupported"
  )
    return "当前微信版本或数据格式尚不支持读取会话。";
  if (cause.code === "conversation_busy")
    return "正在读取其他会话，请稍后重试。";
  if (cause.code.startsWith("settings_"))
    return "设置读取或保存失败，原有设置未被覆盖。";
  return "会话数据暂未更新，请稍后重试。";
}
