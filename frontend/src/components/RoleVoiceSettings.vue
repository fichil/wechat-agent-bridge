<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, type ApiProfile } from "../api";
import { settingsError } from "../role-cards";
import BailianVoiceSettings from "./BailianVoiceSettings.vue";
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
const styles = computed(() => object(extension.value.voice_instructions));
const serviceBinding = computed(() => object(extension.value.voice_service));
const independent = computed(
  () => serviceBinding.value.service_id === "bailian",
);
function setService(value?: Record<string, unknown>) {
  const next = { ...extension.value };
  if (value) next.voice_service = value;
  else delete next.voice_service;
  emit("update:modelValue", { ...props.modelValue, wechat_agent_bridge: next });
}
function chooseService(event: Event) {
  setService(
    (event.target as HTMLSelectElement).value === "bailian"
      ? {
          service_id: "bailian",
          voice: "longhuhu_v3",
          instructions: "你正在进行自由对话，你说话的情感是neutral。",
        }
      : undefined,
  );
}
const previewText = ref("");
function styleValue(pid: string): string {
  const value = styles.value[pid];
  return typeof value === "string" ? value : "";
}
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
function setBinding(
  key: "voices" | "voice_instructions",
  pid: string,
  event: Event,
) {
  const value = (event.target as HTMLInputElement | HTMLTextAreaElement).value;
  const next = { ...object(extension.value[key]) };
  if (value.trim()) next[pid] = value;
  else delete next[pid];
  emit("update:modelValue", {
    ...props.modelValue,
    wechat_agent_bridge: { ...extension.value, [key]: next },
  });
}
function supportsStyle(profile: ApiProfile) {
  const protocol = profile.speech_protocol || "auto";
  const openai =
    protocol === "openai_speech" ||
    (protocol === "auto" && profile.protocol !== "gemini_generate_content");
  return (
    openai &&
    /^gpt-4o-mini-tts(?:-\d{4}-\d{2}-\d{2})?$/.test(
      profile.speech_model || profile.model,
    )
  );
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
    const instructions = styles.value[profile.profile_id];
    const audio = await api.voicePreview(
      profile.profile_id,
      profile.revision,
      typeof name === "string" ? name.trim() : null,
      typeof instructions === "string" ? instructions : "",
      previewText.value,
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
watch(previewText, release);
onBeforeUnmount(release);
</script>
<template>
  <section class="voice-settings" aria-label="角色语音">
    <h4>角色语音</h4>
    <p class="settings-note">
      AI 会根据角色和对话选择文字或语音。可跟随聊天 API 或使用独立语音服务。
      说话风格控制语气和节奏，试听与正式回复使用相同设置；试听为 AI 合成语音。
    </p>
    <label class="editor-field">
      <span>语音来源</span>
      <select
        class="editor-input"
        aria-label="语音来源"
        :value="independent ? 'bailian' : 'api'"
        @change="chooseService"
      >
        <option value="api">跟随聊天 API</option>
        <option value="bailian">阿里云百炼</option>
      </select>
    </label>
    <p v-if="!profiles.length && !independent" class="settings-note">
      添加 API 配置后可设置音色。
    </p>
    <label v-if="profiles.length || independent" class="editor-field">
      <span>试听文案（可选）</span>
      <textarea
        v-model="previewText"
        class="editor-input"
        aria-label="试听文案"
        maxlength="500"
        rows="2"
        placeholder="输入角色常说的中文或英文短句，留空使用默认试听句"
      />
      <small>最多 500 字符，仅用于试听，不保存到角色卡。</small>
    </label>
    <BailianVoiceSettings
      v-if="independent"
      :binding="serviceBinding"
      :text="previewText"
      @update="setService"
    />
    <div
      v-for="profile in independent ? [] : profiles"
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
          @input="setBinding('voices', profile.profile_id, $event)"
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
      <label
        v-if="supportsStyle(profile) || styles[profile.profile_id]"
        class="editor-field voice-style"
      >
        <span>{{ profile.name }} · 说话风格</span>
        <textarea
          class="editor-input"
          :aria-label="`${profile.name} 说话风格`"
          :value="styleValue(profile.profile_id)"
          maxlength="2000"
          rows="3"
          placeholder="例如：清亮自然的女声，短句利落，略带俏皮；认真关心时温柔克制"
          @input="setBinding('voice_instructions', profile.profile_id, $event)"
        />
        <small v-if="supportsStyle(profile)"
          >最多 2000 字符，只描述声音与演绎方式，留空使用音色的自然表达。</small
        >
        <small v-else
          >当前模型不支持已保存的说话风格，请清空风格或更换支持的语音模型。</small
        >
      </label>
      <small v-else class="settings-note"
        >说话风格仅适用于 OpenAI gpt-4o-mini-tts 系列。</small
      >
      <ElButton
        :loading="busy === profile.profile_id"
        :disabled="
          !!busy ||
          !profile.key_readable ||
          (!supportsStyle(profile) && !!styles[profile.profile_id])
        "
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
.voice-setting .voice-style {
  flex-basis: 100%;
}
audio {
  max-width: 100%;
}
</style>
