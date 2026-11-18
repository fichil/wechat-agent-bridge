<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, type ApiProfile } from "../api";
import { settingsError } from "../role-cards";
const props = defineProps<{
  modelValue: Record<string, unknown>;
  profiles: ApiProfile[];
}>();
const emit = defineEmits<{
  "update:modelValue": [value: Record<string, unknown>];
}>();
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
const extension = computed(() => object(props.modelValue.wechat_agent_bridge));
const bindings = computed(() => object(extension.value.voices));
const busy = ref(""),
  source = ref(""),
  previewProfile = ref(""),
  error = ref("");
let generation = 0;
function release() {
  generation++;
  if (source.value) URL.revokeObjectURL(source.value);
  source.value = "";
  busy.value = "";
  previewProfile.value = "";
  error.value = "";
}
function setVoice(pid: string, event: Event) {
  const voice = (event.target as HTMLInputElement).value;
  const next = { ...bindings.value };
  if (voice.trim()) next[pid] = voice;
  else delete next[pid];
  emit("update:modelValue", {
    ...props.modelValue,
    wechat_agent_bridge: { ...extension.value, voices: next },
  });
}
function defaultVoice(profile: ApiProfile) {
  return (
    profile.verification?.speech?.default_voice ||
    (profile.speech_protocol === "gemini_generate_content" ||
    ((profile.speech_protocol || "auto") === "auto" &&
      profile.protocol === "gemini_generate_content")
      ? "Kore"
      : "alloy")
  );
}
async function preview(profile: ApiProfile) {
  release();
  const ticket = generation;
  busy.value = profile.profile_id;
  try {
    const name = bindings.value[profile.profile_id];
    const audio = await api.voicePreview(
      profile.profile_id,
      profile.revision,
      typeof name === "string" ? name.trim() : null,
    );
    if (ticket !== generation) return;
    const bytes = Uint8Array.from(atob(audio.data_base64), (c) =>
      c.charCodeAt(0),
    );
    source.value = URL.createObjectURL(
      new Blob([bytes], { type: audio.mime_type }),
    );
    previewProfile.value = profile.profile_id;
  } catch (cause) {
    if (ticket === generation) error.value = settingsError(cause);
  } finally {
    if (ticket === generation) busy.value = "";
  }
}
watch(() => [props.modelValue, props.profiles], release, { deep: true });
onBeforeUnmount(release);
</script>
<template>
  <section class="voice-settings" aria-label="角色语音">
    <h4>角色语音</h4>
    <p class="settings-note">
      AI 会根据角色和对话选择文字或语音。音色按 API
      配置绑定，留空使用服务商默认音色；试听为 AI 合成语音。
    </p>
    <p v-if="!profiles.length" class="settings-note">
      添加 API 配置后可设置音色。
    </p>
    <div
      v-for="profile in profiles"
      :key="profile.profile_id"
      class="voice-setting"
    >
      <label class="editor-field"
        ><span>{{ profile.name }} · 音色</span>
        <input
          class="editor-input"
          :aria-label="`${profile.name} 音色`"
          :value="bindings[profile.profile_id] || ''"
          :placeholder="`默认 ${defaultVoice(profile)}`"
          maxlength="200"
          @input="setVoice(profile.profile_id, $event)"
        />
        <small
          >{{ profile.speech_model || profile.model }} ·
          {{
            profile.verification?.speech?.status === "supported"
              ? "语音已验证"
              : "可在 API 配置中测试语音能力"
          }}</small
        >
      </label>
      <ElButton
        :loading="busy === profile.profile_id"
        :disabled="!!busy || !profile.key_readable"
        @click="preview(profile)"
        >试听音色</ElButton
      >
      <audio
        v-if="previewProfile === profile.profile_id && source"
        :src="source"
        controls
        preload="metadata"
        aria-label="角色音色试听"
      />
    </div>
    <p v-if="error" role="alert" class="conversation-alert">{{ error }}</p>
  </section>
</template>
<style scoped>
.voice-settings {
  width: 100%;
}
.voice-setting {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 12px 0;
}
.voice-setting label {
  flex: 1;
  min-width: 180px;
}
audio {
  max-width: 100%;
}
</style>
