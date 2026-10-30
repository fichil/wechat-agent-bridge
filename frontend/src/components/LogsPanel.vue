<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import "element-plus/es/components/message-box/style/css";
import {
  api,
  type LogSummary,
  type LogDetail,
  type LogQuery,
  type SessionSummary,
  type Message,
  type ApiProfile,
} from "../api";
import { onChange } from "../api/events";
import { settingsError } from "../role-cards";
import AiRunsPanel from "./AiRunsPanel.vue";
const props = defineProps<{ accountId: string }>();
const category = ref<"ai" | "source">("ai");
const scope = ref<"all" | "account" | "global">("all");
const conversation = ref("");
const profile = ref("");
const status = ref("");
const since = ref("");
const until = ref("");
const items = ref<LogSummary[]>([]);
const sessions = ref<SessionSummary[]>([]);
const profiles = ref<ApiProfile[]>([]);
const selected = computed(() =>
  sessions.value.find((s) => s.conversation_id === conversation.value),
);
const details = ref<Record<string, LogDetail>>({});
const expanded = ref<Record<string, boolean>>({});
const detailBusy = ref<Record<string, boolean>>({});
const pending = ref<Message[]>([]);
const messageCursor = ref<string | null>(null);
const cursor = ref<string | null>(null);
const loading = ref(false);
const error = ref("");
const notice = ref("");
const warning = ref("");
const size = ref(0);
const viewport = ref<HTMLElement>();
let generation = 0,
  listTicket = 0,
  messagesTicket = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
const stateNames: Record<string, string> = {
  pending: "待处理",
  processing: "处理中",
  waiting_image: "等待图片",
  waiting_transcript: "等待转写",
  unsupported: "暂不支持",
  failed: "失败",
  completed: "完成",
  running: "执行中",
  queued: "排队中",
  interrupted: "执行中断",
  conflicted: "记忆冲突",
  cancelled: "已取消",
  empty: "没有可处理消息",
};
const operationNames: Record<string, string> = {
  conversation: "会话调用",
  test_text: "合成文本／JSON 测试",
  test_image: "合成图片测试",
  create: "新增 API 测试",
  update: "修改 API 测试",
  test: "API 测试",
  accounts: "账号读取",
  conversations: "会话读取",
  messages: "消息读取",
  image: "图片读取",
  changes: "增量同步",
};
const detailNames: Record<string, string> = {
  input_snapshot: "后端取值快照",
  request_json: "实际请求 JSON",
  response_json: "响应 JSON",
  response_text: "非 JSON 响应正文",
  parsed_result: "解析结果",
  application_result: "本地任务结果",
  input_parameters: "读取参数",
  result: "标准化读取结果",
  snapshot_versions: "快照版本",
  endpoint: "请求地址",
  note: "说明",
  response_truncated: "响应是否截断",
  usage: "服务商用量",
};
function time(value: string) {
  return new Date(value).toLocaleString("zh-CN");
}
function json(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
function query(next?: string | null): LogQuery {
  return {
    account_id: props.accountId,
    category: category.value,
    scope: scope.value,
    conversation_id: conversation.value || null,
    profile_id: profile.value || null,
    status: (status.value || null) as LogQuery["status"],
    since: since.value ? new Date(since.value).toISOString() : null,
    until: until.value ? new Date(until.value).toISOString() : null,
    cursor: next || null,
    page_size: 30,
  };
}
async function load(append = false, refresh = false) {
  const ticket = ++listTicket,
    version = generation;
  loading.value = true;
  try {
    const result = await api.logs(query(append ? cursor.value : null));
    // Refresh the loaded range authoritatively, including removals in other pages.
    if (refresh) {
      const pages = Math.ceil(items.value.length / 30);
      for (let i = 1; i < pages && result.next_cursor; i++) {
        if (version !== generation || ticket !== listTicket) return;
        const more = await api.logs(query(result.next_cursor));
        result.items.push(...more.items);
        result.next_cursor = more.next_cursor;
      }
    }
    if (version !== generation || ticket !== listTicket) return;
    const top = viewport.value?.getBoundingClientRect().top || 0;
    const anchor = [
      ...(viewport.value?.querySelectorAll<HTMLElement>(".log-entry") || []),
    ].find((node) => node.getBoundingClientRect().bottom > top);
    const anchorId = anchor?.dataset.logId;
    const offset = anchor ? anchor.getBoundingClientRect().top - top : 0;
    const previous = append ? items.value : [];
    const combined = new Map(previous.map((item) => [item.log_id, item]));
    for (const item of result.items) combined.set(item.log_id, item);
    items.value = [...combined.values()].sort(
      (a, b) =>
        b.updated_at.localeCompare(a.updated_at) ||
        b.log_id.localeCompare(a.log_id),
    );
    cursor.value = result.next_cursor ?? null;
    warning.value = result.warning || "";
    size.value = result.size_bytes;
    error.value = "";
    for (const item of result.items)
      if (
        expanded.value[item.log_id] &&
        details.value[item.log_id]?.updated_at !== item.updated_at
      )
        void detail(item.log_id);
    await nextTick();
    if (refresh && anchorId && viewport.value) {
      const node = viewport.value.querySelector<HTMLElement>(
        `[data-log-id="${CSS.escape(anchorId)}"]`,
      );
      if (node)
        viewport.value.scrollTop +=
          node.getBoundingClientRect().top - top - offset;
    }
  } catch (cause) {
    if (version === generation) error.value = settingsError(cause);
  } finally {
    if (version === generation && ticket === listTicket) loading.value = false;
  }
}
async function loadSessions() {
  const version = generation;
  try {
    const result = await api.aiSessions(props.accountId);
    if (version === generation) sessions.value = result.items;
  } catch (cause) {
    if (version === generation) error.value = settingsError(cause);
  }
}
async function loadProfiles() {
  const version = generation;
  try {
    const result = await api.apiProfiles();
    if (version === generation) profiles.value = result.items;
  } catch (cause) {
    if (version === generation) error.value = settingsError(cause);
  }
}
async function loadMessages(append = false) {
  const version = generation,
    ticket = ++messagesTicket,
    cid = conversation.value;
  if (!cid) {
    pending.value = [];
    messageCursor.value = null;
    return;
  }
  try {
    const result = await api.aiMessages({
      account_id: props.accountId,
      conversation_id: cid,
      cursor: append ? messageCursor.value : null,
      page_size: 30,
    });
    if (
      version !== generation ||
      ticket !== messagesTicket ||
      cid !== conversation.value
    )
      return;
    pending.value = append ? [...pending.value, ...result.items] : result.items;
    messageCursor.value = result.next_cursor ?? null;
  } catch (cause) {
    if (version === generation) error.value = settingsError(cause);
  }
}
async function detail(id: string) {
  const version = generation;
  if (detailBusy.value[id]) return;
  detailBusy.value[id] = true;
  try {
    const result = await api.logDetail(props.accountId, id);
    if (version === generation) details.value[id] = result;
  } catch (cause) {
    if (version === generation) error.value = settingsError(cause);
  } finally {
    if (version === generation) detailBusy.value[id] = false;
  }
}
function open(id: string, event: Event) {
  expanded.value[id] = (event.target as HTMLDetailsElement).open;
  if (expanded.value[id] && !details.value[id]) void detail(id);
}
async function copy(value: unknown) {
  try {
    await navigator.clipboard.writeText(json(value));
    notice.value = "已复制当前显示内容（图片为摘要，密钥已脱敏）。";
  } catch {
    error.value = "复制失败，请选择正文手动复制。";
  }
}
async function clear(target: "account" | "global") {
  try {
    await ElMessageBox.confirm(
      `清空${target === "account" ? "当前账号" : "全局测试及账号扫描摘要"}的已结束日志？聊天、记忆和运行中的任务会保留。`,
      "清空日志",
      { confirmButtonText: "清空", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  const version = generation;
  try {
    await api.clearLogs(props.accountId, target);
    if (version !== generation) return;
    items.value = [];
    details.value = {};
    expanded.value = {};
    await load();
  } catch (cause) {
    if (version === generation) error.value = settingsError(cause);
  }
}
watch(
  () => props.accountId,
  async () => {
    generation++;
    items.value = [];
    details.value = {};
    expanded.value = {};
    detailBusy.value = {};
    conversation.value = "";
    pending.value = [];
    cursor.value = null;
    error.value = notice.value = "";
    void load();
    void loadSessions();
    void loadProfiles();
  },
  { immediate: true },
);
watch([category, scope, conversation, profile, status, since, until], () => {
  items.value = [];
  cursor.value = null;
  expanded.value = {};
  details.value = {};
  void load();
  void loadMessages();
});
const unsubscribe = onChange((event) => {
  if (event.account_id && event.account_id !== props.accountId) return;
  if (event.type === "resync" || event.resources?.includes("api_profiles"))
    void loadProfiles();
  if (
    event.type !== "ready" &&
    event.type !== "resync" &&
    !event.resources?.some((r) =>
      ["logs", "ai_runs", "messages", "conversation_preferences"].includes(r),
    )
  )
    return;
  clearTimeout(timer);
  timer = setTimeout(() => {
    void load(false, true);
    void loadSessions();
    void loadMessages();
  }, 250);
});
onBeforeUnmount(() => {
  generation++;
  unsubscribe();
  clearTimeout(timer);
});
</script>

<template>
  <section class="logs-panel" aria-label="日志中心">
    <header class="role-toolbar">
      <div>
        <h2>日志</h2>
        <p class="settings-note">
          仅存本机，保留最近 7 天，上限 512 MiB。当前约
          {{ (size / 1024 / 1024).toFixed(2) }} MiB；超限提前清理最旧记录。
        </p>
      </div>
      <div class="editor-actions">
        <ElButton @click="load(false, true)">刷新日志</ElButton
        ><ElButton @click="clear('account')">清空当前账号日志</ElButton
        ><ElButton @click="clear('global')">清空全局日志</ElButton>
      </div>
    </header>
    <div class="settings-menu" role="tablist" aria-label="日志分类">
      <button
        role="tab"
        :aria-selected="category === 'ai'"
        @click="category = 'ai'"
      >
        AI 调用</button
      ><button
        role="tab"
        :aria-selected="category === 'source'"
        @click="category = 'source'"
      >
        后端读取
      </button>
    </div>
    <div class="log-filters">
      <label
        >日志范围<select
          v-model="scope"
          class="editor-input"
          aria-label="日志范围"
        >
          <option value="all">当前账号及全局</option>
          <option value="account">当前账号</option>
          <option value="global">全局测试／扫描摘要</option>
        </select></label
      >
      <label
        >会话<select
          v-model="conversation"
          class="editor-input"
          aria-label="会话"
        >
          <option value="">全部会话</option>
          <option
            v-for="session in sessions"
            :key="session.conversation_id"
            :value="session.conversation_id"
          >
            {{ session.name }}{{ session.ai_enabled ? " · AI 已开启" : "" }}
          </option>
        </select></label
      >
      <label
        >API 配置<select
          v-model="profile"
          class="editor-input"
          aria-label="API 配置"
        >
          <option value="">全部 API</option>
          <option
            v-for="item in profiles"
            :key="item.profile_id"
            :value="item.profile_id"
          >
            {{ item.name }} · {{ item.model }}
          </option>
        </select></label
      >
      <label
        >调用状态<select
          v-model="status"
          class="editor-input"
          aria-label="调用状态"
        >
          <option value="">全部状态</option>
          <option value="running">执行中</option>
          <option value="completed">完成</option>
          <option value="failed">失败</option>
          <option value="interrupted">执行中断</option>
        </select></label
      >
      <label
        >开始时间<input
          v-model="since"
          type="datetime-local"
          class="editor-input" /></label
      ><label
        >结束时间<input
          v-model="until"
          type="datetime-local"
          class="editor-input"
      /></label>
    </div>
    <p v-if="error || warning" role="alert" class="conversation-alert">
      {{ error || warning }}
    </p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="category === 'ai' && selected">
      <AiRunsPanel
        :key="selected.conversation_id"
        :account-id="accountId"
        :conversation="selected"
      />
      <details class="log-pending">
        <summary>待处理／等待素材／暂不支持的消息</summary>
        <p v-if="!pending.length" class="settings-note">没有未完成消息。</p>
        <article
          v-for="message in pending"
          :key="message.message_id"
          class="log-pending-item"
        >
          <strong>{{
            stateNames[message.ai_state?.status || "pending"]
          }}</strong>
          · {{ message.sent_at ? time(message.sent_at) : "时间未知" }} ·
          {{ message.type }}
          <p>
            {{
              message.content.transcript ||
              message.content.text ||
              message.content.filename ||
              "无可用文字内容"
            }}
          </p>
          <small
            >{{ message.ai_state?.reason }} · {{ message.message_id }}</small
          >
        </article>
        <ElButton v-if="messageCursor" @click="loadMessages(true)"
          >加载更多消息</ElButton
        >
      </details>
    </template>
    <p v-else-if="category === 'ai'" class="settings-note">
      选择会话可查看生成回复、消息处理状态，并手动处理或重试。
    </p>
    <div ref="viewport" class="log-list" aria-label="日志记录">
      <p v-if="!items.length">
        {{ loading ? "正在读取日志…" : "暂无符合条件的日志" }}
      </p>
      <details
        v-for="item in items"
        :key="item.log_id"
        :open="expanded[item.log_id] || undefined"
        class="log-entry"
        :data-log-id="item.log_id"
        @toggle="open(item.log_id, $event)"
      >
        <summary>
          <strong
            >{{ operationNames[item.operation] || item.operation }} ·
            {{ stateNames[item.status] || item.status }}</strong
          ><span
            >{{
              item.profile_name ||
              item.conversation_name ||
              (item.account_id ? "当前账号" : "全局")
            }}{{ item.model ? " · " + item.model : "" }}</span
          ><small
            >{{ time(item.started_at)
            }}{{ item.count > 1 ? " · 合并 " + item.count + " 次" : "" }} ·
            {{
              item.duration_ms === null || item.duration_ms === undefined
                ? "耗时未记录"
                : item.duration_ms + " ms"
            }}</small
          >
        </summary>
        <p v-if="item.category === 'ai'">
          服务商调用：{{ stateNames[item.status] || item.status }}
        </p>
        <p v-else>
          读取来源：{{
            item.source === "cache" ? "本机缓存" : "微信本地读取"
          }}
          · {{ item.item_count ?? 0 }} 条
        </p>
        <p v-if="item.application_status">
          本地应用：{{
            stateNames[item.application_status] || item.application_status
          }}
        </p>
        <p v-if="item.message" class="conversation-alert">{{ item.message }}</p>
        <p v-if="item.round_id" class="settings-note">
          {{ item.trigger_reason === "mention" ? "@ 触发" : "累计条数触发" }} ·
          第 {{ item.batch_index }} 批 · 已处理
          {{ item.round_completed_count }}/{{ item.round_message_count }} 条 ·
          {{ item.round_final ? "最终批次" : "分批记忆处理" }}
          <span v-if="item.chose_silence"> · AI 选择不发言</span>
        </p>
        <p v-if="item.legacy" class="settings-note">
          历史任务：原始请求与响应未记录。
        </p>
        <p v-if="detailBusy[item.log_id]" role="status">正在读取详情…</p>
        <template v-if="details[item.log_id]"
          ><p class="settings-note">
            {{ item.provider }} · {{ item.protocol }} · 配置版本
            {{ item.profile_revision ?? "未记录" }} · HTTP
            {{ item.http_status ?? "未记录" }} · 最近
            {{ time(item.updated_at) }} · 日志 {{ item.log_id }}
          </p>
          <p class="settings-note">
            触发：{{
              item.trigger === "auto"
                ? "自动"
                : item.trigger === "retry"
                  ? "重试"
                  : item.trigger === "manual"
                    ? "手动"
                    : item.trigger || "未记录"
            }}
            · 任务 {{ item.task_id || "未记录" }} · 请求
            {{ item.request_id || "未记录" }} · 服务商请求
            {{ item.provider_request_id || "未记录" }}
          </p>
          <details
            v-for="(value, key) in details[item.log_id]!.details"
            :key="key"
            class="log-json"
          >
            <summary>{{ detailNames[key] || key }}</summary>
            <ElButton size="small" @click="copy(value)"
              >复制 {{ detailNames[key] || key }}</ElButton
            >
            <pre>{{ value === null ? "未记录" : json(value) }}</pre>
          </details>
        </template>
      </details>
      <ElButton v-if="cursor" :loading="loading" @click="load(true)"
        >加载更多日志</ElButton
      >
    </div>
  </section>
</template>
