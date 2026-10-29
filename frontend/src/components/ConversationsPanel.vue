<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from "vue";
import { useConversationsStore } from "../stores/conversations";
import AccountAvatar from "./AccountAvatar.vue";
import MessageHistory from "./MessageHistory.vue";
import { formatTime } from "../reasons";

const props = defineProps<{ accountId: string }>();
const store = useConversationsStore();
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
      <button
        v-for="item in store.items"
        :key="item.conversation_id"
        type="button"
        class="conversation-row"
        :class="{ selected: store.selectedId === item.conversation_id }"
        :aria-pressed="store.selectedId === item.conversation_id"
        @click="store.selectedId = item.conversation_id"
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
          <span v-if="item.is_container" class="container-label">分类入口</span>
        </span>
      </button>
      <ElButton
        v-if="store.page?.next_cursor"
        class="load-more"
        :loading="store.loadingMore"
        :disabled="store.refreshing"
        @click="store.loadMore"
        >加载更多</ElButton
      >
    </section>
    <MessageHistory :account-id="accountId" :conversation="selected" />
  </div>
</template>
