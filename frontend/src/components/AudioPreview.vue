<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
const props = defineProps<{
  identity: string;
  fetcher: () => Promise<{ mime_type: string; data_base64: string }>;
}>();
const source = ref(""),
  loading = ref(false),
  error = ref("");
let generation = 0;
function release() {
  generation++;
  if (source.value) URL.revokeObjectURL(source.value);
  source.value = "";
  loading.value = false;
  error.value = "";
}
async function load() {
  release();
  const ticket = generation;
  loading.value = true;
  try {
    const audio = await props.fetcher();
    if (ticket !== generation) return;
    if (audio.mime_type !== "audio/wav") throw new Error("invalid audio");
    const bytes = Uint8Array.from(atob(audio.data_base64), (c) =>
      c.charCodeAt(0),
    );
    source.value = URL.createObjectURL(
      new Blob([bytes], { type: audio.mime_type }),
    );
  } catch {
    if (ticket === generation) error.value = "语音暂时无法播放，请重试。";
  } finally {
    if (ticket === generation) loading.value = false;
  }
}
watch(() => props.identity, release);
onBeforeUnmount(release);
</script>
<template>
  <div class="audio-preview">
    <audio
      v-if="source"
      :src="source"
      controls
      preload="metadata"
      aria-label="AI 合成语音"
      @error="error = '语音暂时无法播放，请重试。'"
    />
    <ElButton v-if="!source || error" :loading="loading" @click="load">{{
      error ? "重新加载语音" : "加载语音"
    }}</ElButton>
    <small v-if="error" role="alert">{{ error }}</small>
  </div>
</template>
<style scoped>
.audio-preview {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 8px 0;
}
audio {
  max-width: 100%;
}
</style>
