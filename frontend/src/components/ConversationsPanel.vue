<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useConversationsStore } from "../stores/conversations";
import AccountAvatar from "./AccountAvatar.vue";
import MessageHistory from "./MessageHistory.vue";
import ConversationPreferencesPanel from "./ConversationPreferencesPanel.vue";
import { api, type Conversation, type ConversationPreferences } from "../api";
import { settingsError } from "../role-cards";
import { formatTime } from "../reasons";

const props = defineProps<{ accountId: string }>();
const store = useConversationsStore();
const settingsConversation = ref<Conversation | null>(null);
const preferencesPanel =
  ref<InstanceType<typeof ConversationPreferencesPanel>>();
const toggling = ref(new Set<string>());
const actionError = ref("");
let actionGeneration = 0;
watch(
  () => props.accountId,
  () => {
    actionGeneration++;
    settingsConversation.value = null;
    toggling.value = new Set();
    actionError.value = "";
  },
);
async function show(item: Conversation, settings = false) {
  const ticket = actionGeneration;
  if (preferencesPanel.value && !(await preferencesPanel.value.beforeLeave()))
    return;
  if (ticket !== actionGeneration) return;
  store.selectedId = item.conversation_id;
  settingsConversation.value = settings ? item : null;
}
async function back() {
  if (settingsConversation.value) await show(settingsConversation.value);
}
function saved(value: ConversationPreferences) {
  if (value.account_id !== props.accountId) return;
  const item = store.items.find(
    (c) => c.conversation_id === value.conversation_id,
  );
  if (item && (item.preferences_revision ?? 0) <= value.revision) {
    item.ai_enabled = value.ai_enabled;
    item.ai_configuration_ready = value.ai_enabled
      ? (value.effective?.ready ?? false)
      : null;
    item.preferences_revision = value.revision;
  }
}
async function toggle(item: Conversation, enabled: boolean) {
  if (toggling.value.has(item.conversation_id)) return;
  const ticket = actionGeneration;
  toggling.value.add(item.conversation_id);
  actionError.value = "";
  try {
    const result = await api.savePreferences({
      account_id: props.accountId,
      conversation_id: item.conversation_id,
      expected_revision: item.preferences_revision ?? 0,
      ai_enabled: enabled,
    });
    if (ticket === actionGeneration) saved(result);
  } catch (cause) {
    if (ticket === actionGeneration) {
      actionError.value = settingsError(cause);
      void store.refresh(false);
    }
  } finally {
    if (ticket === actionGeneration)
      toggling.value.delete(item.conversation_id);
  }
}
const selected = computed(() =>
  store.items.find((item) => item.conversation_id === store.selectedId),
);
const filteredEmpty = computed(
  () => store.page && store.page.total === 0 && store.page.unfiltered_total > 0,
);
function activate() {
  store.activate(props.accountId, !document.hidden);
}
watch(() => props.accountId, activate, { immediate: true });
document.addEventListener("visibilitychange", activate);
onBeforeUnmount(() => {
  actionGeneration++;
  document.removeEventListener("visibilitychange", activate);
  store.stop();
});
function messageTime(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  const today = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
  }).format(new Date());
  const day = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
  }).format(date);
  return today === day
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(date)
    : new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        month: "numeric",
        day: "numeric",
      }).format(date);
}
</script>

<template>
  <div class="conversation-toolbar">
    <div>
      <p class="intro-copy">
        {{ store.page ? `${store.page.total} 个会话` : "本机微信会话" }}
      </p>
      <small v-if="store.page"
        >更新于 {{ formatTime(store.page.observed_at) }}</small
      >
      <small v-else>首次读取需要一些时间，可继续使用电脑。</small>
    </div>
    <ElButton
      :loading="store.refreshing"
      :disabled="store.loadingMore || store.accountLost"
      @click="store.refresh"
      >刷新会话</ElButton
    >
  </div>
  <p class="ai-placeholder-note">
    开启 AI 后自动回复微信并更新记忆；群聊默认仅响应
    @，可在会话设置中开启按消息条数主动调用。
  </p>
  <p v-if="actionError" class="conversation-alert" role="alert">
    {{ actionError }}
    <RouterLink :to="`/workspace/${accountId}/role-cards`"
      >管理角色卡</RouterLink
    >
    ·
    <RouterLink :to="`/workspace/${accountId}/agents`"
      >管理 API 配置</RouterLink
    >
  </p>
  <div v-if="store.error" class="conversation-alert" role="alert">
    {{ store.error }}<span v-if="store.page"> 当前显示上次读取结果。</span>
    <RouterLink v-if="store.accountLost" to="/accounts"
      >重新选择账号</RouterLink
    >
  </div>
  <p
    v-if="store.page?.status === 'partial'"
    class="conversation-alert"
    role="status"
  >
    部分会话信息暂未取得，列表可能不完整。
  </p>
  <p v-else-if="store.page?.stale && !store.error" class="conversation-alert">
    当前显示较早的读取结果，请刷新。
  </p>
  <div class="conversation-layout">
    <section
      class="conversation-list"
      aria-label="会话列表"
      :aria-busy="store.refreshing"
    >
      <div
        v-if="!store.page && store.refreshing"
        class="conversation-empty"
        role="status"
      >
        正在读取会话…
      </div>
      <div v-else-if="!store.items.length" class="conversation-empty">
        <template v-if="filteredEmpty"
          ><h3>会话已全部过滤</h3>
          <p>可在设置中调整排除选项。</p>
          <RouterLink :to="`/workspace/${accountId}/settings`"
            >前往会话过滤</RouterLink
          ></template
        >
        <template v-else-if="store.page"
          ><h3>还没有会话</h3>
          <p>此账号的会话列表目前为空。</p></template
        >
        <template v-else
          ><h3>{{ store.accountLost ? "账号已失效" : "会话尚未加载" }}</h3>
          <ElButton v-if="!store.accountLost" @click="store.refresh"
            >重试</ElButton
          ></template
        >
      </div>
      <div
        v-for="item in store.items"
        :key="item.conversation_id"
        class="conversation-row"
        :class="{ selected: store.selectedId === item.conversation_id }"
      >
        <button
          type="button"
          class="conversation-open"
          :aria-pressed="store.selectedId === item.conversation_id"
          @click="show(item)"
        >
          <AccountAvatar
            :account="{ nickname: item.name, avatar: item.avatar }"
            :size="42"
          />
          <span class="conversation-copy"
            ><span class="conversation-top"
              ><strong>{{ item.name }}</strong>
              <time :datetime="item.last_message_at || undefined">{{
                messageTime(item.last_message_at)
              }}</time></span
            >
            <span class="conversation-bottom"
              ><span>{{ item.summary || "暂无消息摘要" }}</span>
              <span
                v-if="item.unread_count === null"
                class="unread-badge"
                aria-label="未读数未知"
                >?</span
              >
              <span
                v-else-if="item.unread_count > 0"
                class="unread-badge"
                :aria-label="`${item.unread_count} 条未读`"
                >{{ item.unread_count > 99 ? "99+" : item.unread_count }}</span
              >
            </span>
            <span v-if="item.is_container" class="container-label"
              >分类入口</span
            >
          </span>
        </button>
        <div class="conversation-controls">
          <ElSwitch
            :model-value="item.ai_enabled ?? false"
            :loading="toggling.has(item.conversation_id)"
            :aria-label="`${item.name} AI 接入`"
            @update:model-value="toggle(item, $event === true)"
          />
          <small>{{
            item.ai_enabled
              ? item.ai_configuration_ready === false
                ? "已开启 · 配置缺失"
                : "自动回复微信"
              : "自动回复已关闭"
          }}</small>
          <button
            type="button"
            class="conversation-settings-button"
            :aria-label="`会话设置：${item.name}`"
            title="会话设置"
            @click="show(item, true)"
          >
            <span aria-hidden="true">⚙</span><span>设置</span>
          </button>
        </div>
      </div>
      <ElButton
        v-if="store.page?.next_cursor"
        class="load-more"
        :loading="store.loadingMore"
        :disabled="store.refreshing"
        @click="store.loadMore"
        >加载更多</ElButton
      >
    </section>
    <ConversationPreferencesPanel
      v-if="settingsConversation"
      ref="preferencesPanel"
      :account-id="accountId"
      :conversation="settingsConversation"
      @back="back"
      @saved="saved"
    />
    <MessageHistory v-else :account-id="accountId" :conversation="selected" />
  </div>
</template>
