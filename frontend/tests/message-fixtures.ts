import type { Message, MessagePage, MessageReadTask } from "../src/api";
export function message(index: number, own = false): Message {
  return {
    mention_status: "unknown",
    mentions_all: false,
    message_id: `message-${index}`,
    sort_key: String(index + 1000).padStart(8, "0"),
    type: "text",
    sent_at: "2026-10-28T06:00:00Z",
    content: { text: `合成消息 ${index}` },
    is_self: own,
    sender: {
      nickname: own ? "合成自己" : "合成好友",
      remark: null,
      wxid: own ? "wxid_self" : "wxid_friend",
      wechat_id: null,
      status: "complete",
    },
    issues: [],
  };
}
export function messagePage(
  account = "one",
  conversation = "chat",
  items = [message(2), message(3, true)],
): MessagePage {
  return {
    account_id: account,
    conversation_id: conversation,
    source_state: "online",
    coverage: "complete",
    needs_sync: false,
    revision: 0,
    snapshot_id: "snapshot",
    observed_at: "2026-10-28T06:00:00Z",
    status: "complete",
    items,
    older_cursor: "older",
    newer_cursor: "newer",
    has_more: false,
    issues: [],
  };
}
export function messageTask(
  page = messagePage(),
  status: MessageReadTask["status"] = "completed",
): MessageReadTask {
  return {
    account_id: page.account_id,
    conversation_id: page.conversation_id,
    task_id: "task",
    kind: "history",
    status,
    started_at: "2026-10-28T06:00:00Z",
    page,
  };
}
