<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useMessagesStore } from "../stores/messages";
const props = defineProps<{ messageId: string; sticker?: boolean }>();
const store = useMessagesStore();
const target = ref<HTMLElement>();
const visible = ref(false);
const broken = ref(false);
const pageVisible = ref(!document.hidden);
const dimensions = ref({ width: 160, height: 160 });
const active = computed(() => visible.value && pageVisible.value);
const noun = computed(() => (props.sticker ? "表情" : "图片"));
const result = computed(() => store.images[props.messageId]);
const labels: Record<string, string> = {
  image_not_downloaded: "图片尚未保存在本机",
  image_size_limit: "图片超过 8 MiB，暂不显示",
  image_format_unsupported: "暂不支持此图片格式",
  image_key_unavailable: "暂时无法解密图片",
  image_invalid: "图片不完整或格式不可用",
  image_reference_unavailable: "未找到图片资源信息",
  image_dimensions_limit: "图片尺寸过大，暂不显示",
  image_animation_limit: "动画帧数或尺寸超过处理限额",
  sticker_reference_unavailable: "未找到表情资源信息",
  sticker_encrypted_unsupported: "暂不支持此表情的加密格式",
  sticker_url_not_allowed: "表情地址不在允许的微信 CDN 范围内",
  sticker_download_failed: "表情下载失败，请稍后重试",
  sticker_download_timeout: "表情下载超时，请稍后重试",
  sticker_redirect_limit: "表情下载地址跳转异常",
  sticker_digest_mismatch: "表情文件校验不一致",
  source_offline: "微信当前离线，此资源尚未缓存",
  source_unknown: "微信连接尚未确认，此资源尚未缓存",
  message_not_found: "微信中已找不到此消息的资源",
  message_source_changed: "微信消息来源已变化，请刷新记录",
  sync_queue_full: "后台读取队列繁忙，请稍后重试",
};
const reason = computed(
  () => labels[result.value?.reason || ""] || `${noun.value}暂不可用`,
);
const quality = computed(() =>
  props.sticker
    ? result.value?.quality === "thumbnail"
      ? "仅缩略图，原始表情暂不可用"
      : result.value?.mime_type === "image/gif"
        ? "动态表情"
        : "表情"
    : { original: "原图", local: "本地图片", thumbnail: "缩略图" }[
        result.value?.quality || "local"
      ],
);
let observer: IntersectionObserver | undefined;
function load(retry = false) {
  if (active.value) store.loadImage(props.messageId, retry, () => active.value);
}
function visibilityChanged() {
  pageVisible.value = !document.hidden;
  load();
}
function rememberSize(event: Event) {
  const image = event.target as HTMLImageElement;
  const rect = image.getBoundingClientRect();
  if (rect.width && rect.height)
    dimensions.value = { width: rect.width, height: rect.height };
}
onMounted(() => {
  observer = new IntersectionObserver((entries) => {
    visible.value = !!entries[0]?.isIntersecting;
    load();
  });
  if (target.value) observer.observe(target.value);
  document.addEventListener("visibilitychange", visibilityChanged);
});
watch(
  () => store.loading,
  () => load(),
);
watch(result, () => {
  broken.value = false;
});
onBeforeUnmount(() => {
  observer?.disconnect();
  visible.value = false;
  document.removeEventListener("visibilitychange", visibilityChanged);
});
</script>
<template>
  <div
    ref="target"
    class="message-image"
    :class="{ 'message-sticker': sticker }"
  >
    <template
      v-if="result?.status === 'available' && result.data_base64 && !broken"
    >
      <img
        v-if="active"
        :src="`data:${result.mime_type};base64,${result.data_base64}`"
        :alt="quality"
        loading="lazy"
        @load="rememberSize"
        @error="broken = true"
      />
      <span
        v-else
        class="message-media-placeholder"
        aria-hidden="true"
        :style="{
          width: `${dimensions.width}px`,
          aspectRatio: `${dimensions.width} / ${dimensions.height}`,
        }"
      />
      <small>{{ quality }}</small>
    </template>
    <template v-else>
      <span>{{
        store.loadingImages[messageId]
          ? `正在读取${noun}…`
          : broken
            ? `${noun}显示失败`
            : result
              ? reason
              : noun
      }}</span>
      <button
        v-if="result"
        type="button"
        class="message-link"
        :disabled="!!store.loadingImages[messageId]"
        @click="load(true)"
      >
        重试
      </button>
    </template>
  </div>
</template>
