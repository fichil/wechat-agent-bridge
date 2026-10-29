<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import type { Conversation } from "../api";
import { useMessagesStore } from "../stores/messages";
import MessageImage from "./MessageImage.vue";
const props = defineProps<{ accountId: string; conversation?: Conversation }>();
const store = useMessagesStore();
const viewport = ref<HTMLElement>();
const atBottom = ref(true);
const unseen = ref(0);
let resize: ResizeObserver | undefined;
function activate() {
  store.activate(
    props.accountId,
    props.conversation && !props.conversation.is_container
      ? props.conversation.conversation_id
      : null,
    !document.hidden,
  );
}
watch(
  () => [props.accountId, props.conversation?.conversation_id],
  () => {
    unseen.value = 0;
    atBottom.value = true;
    activate();
  },
  { immediate: true },
);
document.addEventListener("visibilitychange", activate);
function bottom() {
  if (viewport.value) viewport.value.scrollTop = viewport.value.scrollHeight;
  unseen.value = 0;
  atBottom.value = true;
}
function onScroll() {
  const el = viewport.value;
  if (el) {
    atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (atBottom.value) unseen.value = 0;
  }
}
watch(
  () => store.arrivalCount,
  (count, previous) => {
    if (!atBottom.value && count > previous) unseen.value += count - previous;
  },
);
watch(
  () => store.items,
  async () => {
    const follow = atBottom.value && !store.loadingMore;
    await nextTick();
    if (follow) bottom();
  },
);
watch(viewport, (el) => {
  resize?.disconnect();
  if (el) {
    resize = new ResizeObserver(() => {
      if (atBottom.value && !store.loadingMore) bottom();
    });
    if (el.firstElementChild) resize.observe(el.firstElementChild);
  }
});
async function older() {
  const el = viewport.value;
  if (!el) return;
  const height = el.scrollHeight,
    top = el.scrollTop;
  await store.loadOlder();
  await nextTick();
  if (viewport.value === el) el.scrollTop = top + el.scrollHeight - height;
}
function time(value: string | null) {
  return value
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date(value))
    : "时间未知";
}
onBeforeUnmount(() => {
  document.removeEventListener("visibilitychange", activate);
  resize?.disconnect();
  store.stop();
});
</script>
<template>
  <section class="message-history" aria-label="聊天历史">
    <div
      v-if="!conversation || conversation.is_container"
      class="message-empty"
    >
      <span aria-hidden="true" class="placeholder-icon">◌</span>
      <h3>{{ conversation?.name || "选择一个会话" }}</h3>
      <p>
        {{
          conversation?.is_container
            ? "分类入口暂不展开。"
            : "点击左侧会话查看本机聊天记录。"
        }}
      </p>
    </div>
    <template v-else>
      <header class="message-heading">
        <strong>{{ conversation.name }}</strong
        ><ElButton
          size="small"
          :loading="store.loading"
          :disabled="store.loadingMore || store.accountLost"
          @click="store.reload"
          >刷新</ElButton
        >
      </header>
      <div v-if="store.error" role="alert" class="message-alert">
        {{ store.error
        }}<RouterLink v-if="store.accountLost" to="/accounts"
          >重新选择账号</RouterLink
        >
      </div>
      <div
        v-if="store.page?.status === 'partial'"
        role="status"
        class="message-alert"
      >
        部分消息或发送者资料暂不可用，已保留可读取的记录。
      </div>
      <p v-if="store.page" class="subtle" role="status">
        {{
          store.page.source_state === "online"
            ? "后台自动同步"
            : "当前离线，显示已保存历史"
        }}
        ·
        {{
          store.page.last_synced_at
            ? time(store.page.last_synced_at)
            : "尚未同步"
        }}
        <span v-if="store.page.coverage !== 'complete'">
          · 历史尚未全部缓存，向前翻页时按需补齐</span
        >
      </p>
      <div ref="viewport" class="message-scroll" @scroll="onScroll">
        <div class="message-list">
          <button
            v-if="store.olderCursor"
            class="message-older"
            :disabled="store.loading || store.loadingMore"
            @click="older"
          >
            {{ store.loadingMore ? "正在读取…" : "加载更早记录" }}
          </button>
          <p v-if="!store.items.length" class="message-empty" role="status">
            {{
              store.loading
                ? "正在后台读取聊天记录，可继续使用电脑…"
                : store.page
                  ? store.page.coverage === "complete"
                    ? "本机暂无聊天记录"
                    : "此范围尚未缓存，微信在线时可补录"
                  : "聊天记录尚未加载"
            }}
          </p>
          <article
            v-for="message in store.items"
            :key="message.message_id"
            :data-message-id="message.message_id"
            class="message-row"
            :class="
              message.type === 'system' || message.is_self === null
                ? 'message-neutral'
                : message.is_self
                  ? 'message-self'
                  : 'message-other'
            "
          >
            <div class="message-meta">
              <span
                v-if="message.type !== 'system'"
                :title="
                  message.sender.wxid || message.sender.wechat_id || '身份未知'
                "
                >{{
                  message.sender.nickname ||
                  (message.sender.wxid ? "昵称未知" : "发送者未知")
                }}</span
              ><time :datetime="message.sent_at || undefined">{{
                time(message.sent_at)
              }}</time>
            </div>
            <div class="message-bubble">
              <MessageImage
                v-if="message.type === 'image' || message.type === 'sticker'"
                :message-id="message.message_id"
                :sticker="message.type === 'sticker'"
              />
              <template v-else-if="message.type === 'voice'"
                ><small
                  >语音{{
                    message.content.duration_ms
                      ? ` · ${Math.round(message.content.duration_ms / 1000)} 秒`
                      : ""
                  }}</small
                >
                <p>{{ message.content.transcript || "暂无微信转写文字" }}</p>
                <small v-if="message.content.transcript"
                  >微信已有转写</small
                ></template
              >
              <template
                v-else-if="message.type === 'text' || message.type === 'system'"
                ><p>
                  {{
                    message.content.text ||
                    (message.issues?.length ? "消息内容暂不可用" : "")
                  }}
                </p></template
              >
              <template v-else
                ><small
                  >{{ message.content.description
                  }}{{
                    message.content.format ? ` · ${message.content.format}` : ""
                  }}</small
                >
                <p>
                  {{
                    message.content.filename ||
                    message.content.text ||
                    "暂无可显示内容"
                  }}
                </p></template
              >
            </div>
          </article>
        </div>
      </div>
      <button v-if="unseen" type="button" class="message-new" @click="bottom">
        {{ unseen }} 条新消息 ↓
      </button>
    </template>
  </section>
</template>
