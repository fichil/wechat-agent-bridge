<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, type Conversation, type RunList } from "../api";
import { onChange } from "../api/events";
import { settingsError } from "../role-cards";
const props = defineProps<{
  accountId: string;
  conversation: Pick<Conversation, "conversation_id" | "ai_enabled" | "name">;
}>();
const data = ref<RunList | null>(null);
const error = ref("");
const busy = ref(false);
const capability = ref<Awaited<ReturnType<typeof api.sendCapability>> | null>(
  null,
);
const deliveryNames: Record<string, string> = {
  not_required: "无需发送",
  queued: "等待发送",
  sending: "发送中",
  sent: "已发送",
  failed: "发送失败（明确未发出）",
  unknown: "发送结果不确定",
  cancelled: "已取消后续发送",
};
const deliveryReasons: Record<string, string> = {
  native_completed: "微信原生接口已确认完成",
  native_rejected: "微信原生接口拒绝了发送，明确未发出",
  native_result_uncertain: "提交后未取得确定结果，需要核对微信",
  send_interrupted: "服务在发送结果确认前中断，需要核对微信",
  ai_disabled: "会话自动回复已关闭",
  user_confirmed: "已由你核对确认",
  user_abandoned: "已由你放弃后续发送",
  source_offline: "微信离线，等待恢复",
  source_identity_changed: "账号身份变化，等待重新识别",
  native_busy: "等待上一项原生任务安全结束",
  native_acceptance_required: "本机发送验收尚未完成",
  native_version_unsupported: "当前微信构建尚未适配",
  native_unavailable: "发送通道暂不可用",
};
const active = computed(() =>
  data.value?.items.some(
    (r) =>
      r.status === "queued" ||
      r.status === "running" ||
      ["queued", "sending"].includes(r.delivery?.status ?? ""),
  ),
);
const failed = computed(() =>
  data.value?.items.find((r) =>
    ["failed", "conflicted", "cancelled"].includes(r.status),
  ),
);
const names: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  completed: "已纳入记忆",
  failed: "失败",
  conflicted: "记忆冲突",
  cancelled: "已取消",
  empty: "没有可处理消息",
};
let generation = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
async function load() {
  const ticket = generation;
  try {
    const result = await api.aiRuns(
      props.accountId,
      props.conversation.conversation_id,
    );
    if (ticket === generation) data.value = result;
    const status = await api.sendCapability(props.accountId).catch(() => null);
    if (ticket === generation) capability.value = status;
  } catch (cause) {
    if (ticket === generation) error.value = settingsError(cause);
  }
  if (ticket === generation && active.value) {
    clearTimeout(timer);
    timer = setTimeout(load, 1500);
  }
}
watch(
  () => [props.accountId, props.conversation.conversation_id],
  () => {
    generation++;
    data.value = null;
    capability.value = null;
    error.value = "";
    busy.value = false;
    clearTimeout(timer);
    void load();
  },
  { immediate: true },
);
async function start(retry = false) {
  const ticket = generation;
  busy.value = true;
  error.value = "";
  try {
    const id = crypto.randomUUID();
    if (retry && failed.value)
      await api.retryAi(
        props.accountId,
        props.conversation.conversation_id,
        failed.value.run_id,
        id,
      );
    else
      await api.startAi(
        props.accountId,
        props.conversation.conversation_id,
        id,
      );
    if (ticket === generation) await load();
  } catch (cause) {
    if (ticket === generation) error.value = settingsError(cause);
  } finally {
    if (ticket === generation) busy.value = false;
  }
}
async function deliver(
  runId: string,
  resolution?: "confirmed_sent" | "abandoned",
) {
  const ticket = generation;
  busy.value = true;
  error.value = "";
  try {
    if (resolution)
      await api.resolveDelivery(
        props.accountId,
        props.conversation.conversation_id,
        runId,
        resolution,
      );
    else
      await api.retryDelivery(
        props.accountId,
        props.conversation.conversation_id,
        runId,
      );
    if (ticket === generation) await load();
  } catch (cause) {
    if (ticket === generation) error.value = settingsError(cause);
  } finally {
    if (ticket === generation) busy.value = false;
  }
}
const unsubscribe = onChange((event) => {
  if (event.account_id && event.account_id !== props.accountId) return;
  if (
    event.conversation_ids?.length &&
    !event.conversation_ids.includes(props.conversation.conversation_id)
  )
    return;
  if (
    event.type === "resync" ||
    event.resources?.some((r) =>
      [
        "ai_runs",
        "messages",
        "conversation_preferences",
        "api_profiles",
      ].includes(r),
    )
  )
    void load();
});
onBeforeUnmount(() => {
  generation++;
  unsubscribe();
  clearTimeout(timer);
});
</script>
<template>
  <details class="ai-runs" :open="conversation.ai_enabled || undefined">
    <summary>
      AI 工作台 · {{ data?.pending_count ?? 0 }} 条待处理 ·
      {{ data?.waiting_count ?? 0 }} 条等待／暂不支持
    </summary>
    <div class="editor-actions">
      <ElButton
        :disabled="
          busy || active || !conversation.ai_enabled || !data?.pending_count
        "
        :loading="busy"
        @click="start()"
        >处理未处理消息</ElButton
      >
      <ElButton
        v-if="data?.paused && failed"
        :disabled="busy || active || !conversation.ai_enabled"
        @click="start(true)"
        >重试并恢复自动处理</ElButton
      >
      <span v-if="active" role="status">AI 自动回复处理中。</span>
      <span v-else-if="!conversation.ai_enabled">开启会话 AI 后可处理。</span>
    </div>
    <p
      v-if="conversation.ai_enabled && capability && !capability.ready"
      role="status"
      class="conversation-alert"
    >
      {{ capability.message }}
    </p>
    <p v-if="error" class="conversation-alert" role="alert">{{ error }}</p>
    <p v-if="data?.paused" class="conversation-alert">
      自动处理已暂停。{{ failed?.message }}
    </p>
    <div class="ai-results">
      <details
        v-for="(run, index) in data?.items"
        :key="run.run_id"
        :open="index === 0 || undefined"
      >
        <summary>
          {{ names[run.status] }} ·
          {{ run.historical ? "历史初始化批次" : "增量批次" }} ·
          {{ run.message_count }} 条 ·
          {{ new Date(run.started_at).toLocaleString("zh-CN") }}
        </summary>
        <p v-if="run.message">{{ run.message }}</p>
        <p v-if="run.reply" class="ai-reply">{{ run.reply }}</p>
        <small v-if="run.status === 'completed'"
          >记忆版本 {{ run.memory_revision }} ·
          {{ deliveryNames[run.delivery?.status ?? "not_required"] }}
          <template v-if="run.delivery?.total_parts">
            · {{ run.delivery.sent_parts }}/{{
              run.delivery.total_parts
            }}
            段</template
          ></small
        >
        <div v-if="run.delivery?.status === 'failed'" class="editor-actions">
          <ElButton
            :disabled="busy || !conversation.ai_enabled"
            @click="deliver(run.run_id)"
            >重试发送原回复</ElButton
          >
        </div>
        <div
          v-if="run.delivery?.status === 'unknown'"
          class="conversation-alert"
        >
          该回复可能已发出，已暂停此会话后续自动回复。请先在微信核对，避免重复发送。
          <div class="editor-actions">
            <ElButton
              :disabled="busy"
              @click="deliver(run.run_id, 'confirmed_sent')"
              >已核对：不确定的段已发出</ElButton
            >
            <ElButton :disabled="busy" @click="deliver(run.run_id, 'abandoned')"
              >放弃本条后续发送并继续</ElButton
            >
          </div>
        </div>
        <ul v-if="run.delivery && run.delivery.total_parts > 0">
          <li v-for="part in run.delivery.parts" :key="part.index">
            第 {{ part.index + 1 }} 段：{{ deliveryNames[part.status] }}
            <span v-if="part.code && deliveryReasons[part.code]">
              · {{ deliveryReasons[part.code] }}</span
            >
            <span v-if="part.archive_message_id"> · 已回读聊天记录</span>
            <span v-if="part.confirmation === 'user'"> · 手动确认</span>
          </li>
        </ul>
      </details>
    </div>
  </details>
</template>
