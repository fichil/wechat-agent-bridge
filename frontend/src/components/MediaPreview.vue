<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { MediaData } from "../api";
const props = defineProps<{
  identity: string;
  label: string;
  fetcher: () => Promise<MediaData>;
}>();
const target = ref<HTMLElement>();
const visible = ref(false);
const shown = ref(!document.hidden);
const source = ref("");
const error = ref("");
const loading = ref(false);
const active = computed(() => visible.value && shown.value);
let observer: IntersectionObserver | undefined;
let generation = 0;
function release() {
  if (source.value) URL.revokeObjectURL(source.value);
  source.value = "";
}
async function load() {
  const ticket = ++generation;
  release();
  error.value = "";
  loading.value = true;
  try {
    const media = await props.fetcher();
    if (ticket !== generation || !active.value) return;
    const bytes = Uint8Array.from(atob(media.data_base64), (c) =>
      c.charCodeAt(0),
    );
    source.value = URL.createObjectURL(
      new Blob([bytes], { type: media.mime_type }),
    );
  } catch {
    if (ticket === generation) error.value = "图片暂时无法显示";
  } finally {
    if (ticket === generation) loading.value = false;
  }
}
function visibility() {
  shown.value = !document.hidden;
}
watch(
  () => [props.identity, active.value],
  () => {
    generation++;
    release();
    loading.value = false;
    if (active.value) void load();
  },
);
onMounted(() => {
  observer = new IntersectionObserver(([entry]) => {
    visible.value = !!entry?.isIntersecting;
  });
  if (target.value) observer.observe(target.value);
  document.addEventListener("visibilitychange", visibility);
});
onBeforeUnmount(() => {
  generation++;
  observer?.disconnect();
  release();
  document.removeEventListener("visibilitychange", visibility);
});
</script>

<template>
  <div ref="target" class="media-preview">
    <img
      v-if="source && active"
      :src="source"
      :alt="label"
      @error="error = '图片暂时无法显示'"
    />
    <span v-else>{{ loading ? "加载中…" : error || label }}</span>
    <button v-if="error && active" type="button" @click="load">重新加载</button>
  </div>
</template>

<style scoped>
.media-preview {
  width: 100%;
  min-height: 110px;
  display: grid;
  place-items: center;
  background: var(--surface-soft, #f4f7fa);
  border-radius: 10px;
  overflow: hidden;
}
img {
  display: block;
  max-width: 100%;
  width: auto;
  max-height: 240px;
  object-fit: contain;
}
span {
  color: #68758a;
  padding: 16px;
  font-size: 13px;
}
</style>
