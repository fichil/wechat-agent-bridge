<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useMessagesStore } from "../stores/messages";
const props = defineProps<{ messageId: string }>();
const store = useMessagesStore();
const target = ref<HTMLElement>();
const visible = ref(false);
const broken = ref(false);
const result = computed(() => store.images[props.messageId]);
const labels: Record<string, string> = {
  image_not_downloaded: "图片尚未保存在本机",
  image_size_limit: "图片超过 8 MiB，暂不显示",
  image_format_unsupported: "暂不支持此图片格式",
  image_key_unavailable: "暂时无法解密图片",
  image_invalid: "图片不完整或格式不可用",
  image_reference_unavailable: "未找到图片资源信息",
};
const reason = computed(
  () => labels[result.value?.reason || ""] || "图片暂不可用",
);
const quality = computed(
  () =>
    ({ original: "原图", local: "本地图片", thumbnail: "缩略图" })[
      result.value?.quality || "local"
    ],
);
let observer: IntersectionObserver | undefined;
function load() {
  if (visible.value && !document.hidden) store.loadImage(props.messageId);
}
onMounted(() => {
  observer = new IntersectionObserver((entries) => {
    visible.value = !!entries[0]?.isIntersecting;
    load();
  });
  if (target.value) observer.observe(target.value);
  document.addEventListener("visibilitychange", load);
});
watch(() => store.loading, load);
watch(result, () => {
  broken.value = false;
});
onBeforeUnmount(() => {
  observer?.disconnect();
  document.removeEventListener("visibilitychange", load);
});
</script>
<template>
  <div ref="target" class="message-image">
    <template
      v-if="result?.status === 'available' && result.data_base64 && !broken"
    >
      <img
        :src="`data:${result.mime_type};base64,${result.data_base64}`"
        :alt="quality"
        loading="lazy"
        @error="broken = true"
      />
      <small>{{ quality }}</small>
    </template>
    <template v-else>
      <span>{{
        store.loadingImages[messageId]
          ? "正在读取图片…"
          : broken
            ? "图片显示失败"
            : result
              ? reason
              : "图片"
      }}</span>
      <button
        v-if="result"
        type="button"
        class="message-link"
        @click="store.loadImage(messageId, true)"
      >
        重试
      </button>
    </template>
  </div>
</template>
