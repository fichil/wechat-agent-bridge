<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api, type VoiceServiceSettings } from "../api";
import { settingsError } from "../role-cards";

const props = defineProps<{ binding: Record<string, unknown>; text: string }>();
const emit = defineEmits<{ update: [value: Record<string, unknown>] }>();
const service = ref<VoiceServiceSettings | null>(null);
const workspace = ref("");
const key = ref("");
const busy = ref("");
const error = ref("");
const source = ref("");
let generation = 0;
let mounted = true;
function release() {
  generation++;
  if (source.value) URL.revokeObjectURL(source.value);
  source.value = "";
}
async function load() {
  release();
  error.value = "";
  try {
    const result = await api.voiceService();
    if (!mounted) return;
    service.value = result;
    workspace.value = result.workspace_id;
  } catch (cause) {
    if (mounted) error.value = settingsError(cause);
  }
}
function set(field: string, event: Event) {
  emit("update", {
    ...props.binding,
    service_id: "bailian",
    [field]: (event.target as HTMLInputElement).value,
  });
}
async function save() {
  if (!service.value || busy.value) return;
  release();
  error.value = "";
  busy.value = "save";
  const apiKey = key.value.trim();
  key.value = "";
  try {
    const result = await api.updateVoiceService({
      expected_revision: service.value.revision,
      workspace_id: workspace.value.trim(),
      ...(apiKey ? { api_key: apiKey } : {}),
    });
    if (mounted) service.value = result;
  } catch (cause) {
    if (mounted) error.value = settingsError(cause);
  } finally {
    if (mounted) busy.value = "";
  }
}
async function preview() {
  if (!service.value || busy.value) return;
  release();
  const ticket = generation;
  error.value = "";
  busy.value = "preview";
  try {
    const audio = await api.previewVoiceService({
      service_id: "bailian",
      expected_revision: service.value.revision,
      voice: String(props.binding.voice || "longhuhu_v3"),
      instructions: String(props.binding.instructions || ""),
      text: props.text,
    });
    if (!mounted || ticket !== generation) return;
    const bytes = Uint8Array.from(atob(audio.data_base64), (c) =>
      c.charCodeAt(0),
    );
    source.value = URL.createObjectURL(
      new Blob([bytes], { type: audio.mime_type }),
    );
  } catch (cause) {
    if (mounted && ticket === generation) error.value = settingsError(cause);
  } finally {
    if (mounted) busy.value = "";
  }
}
watch(() => [props.binding, props.text], release, { deep: true });
onMounted(load);
onBeforeUnmount(() => {
  mounted = false;
  key.value = "";
  release();
});
</script>
<template>
  <div class="bailian-voice">
    <p class="settings-note">
      使用独立的百炼语音服务，切换聊天模型后仍保持此音色。
    </p>
    <details>
      <summary>
        百炼语音配置 ·
        {{
          service?.key_readable && service.verification?.status === "supported"
            ? "已验证"
            : "待配置"
        }}
      </summary>
      <p class="settings-note">
        所有选择百炼的角色共用此配置。保存时会合成一条测试语音。
      </p>
      <label class="editor-field">
        <span>北京工作空间 ID</span>
        <input
          v-model="workspace"
          class="editor-input"
          aria-label="百炼工作空间 ID"
          maxlength="128"
          :disabled="!!busy"
        />
      </label>
      <label class="editor-field">
        <span>百炼 API Key</span>
        <input
          v-model="key"
          class="editor-input"
          aria-label="百炼 API Key"
          type="password"
          autocomplete="new-password"
          :placeholder="service?.has_key ? '已保存，留空保留' : '填写 API Key'"
          :disabled="!!busy"
        />
      </label>
      <p v-if="service?.has_key && !service.key_readable" class="settings-note">
        当前设备无法读取已保存的密钥，请重新填写。
      </p>
      <ElButton
        :loading="busy === 'save'"
        :disabled="!!busy || !service || !workspace.trim()"
        @click="save"
        >保存并测试百炼</ElButton
      >
      <ElButton :disabled="!!busy" @click="load">刷新配置</ElButton>
    </details>
    <label class="editor-field">
      <span>百炼音色</span>
      <input
        class="editor-input"
        aria-label="百炼音色"
        :value="String(binding.voice || 'longhuhu_v3')"
        maxlength="200"
        @input="set('voice', $event)"
      />
      <small>longhuhu_v3：龙呼呼，自然女童声。</small>
    </label>
    <label class="editor-field">
      <span>百炼说话风格</span>
      <textarea
        class="editor-input"
        aria-label="百炼说话风格"
        :value="String(binding.instructions || '')"
        maxlength="100"
        rows="2"
        @input="set('instructions', $event)"
      />
      <small
        >例如：你正在进行自由对话，你说话的情感是neutral。最多 100
        个字符，汉字按 2 个计算。</small
      >
    </label>
    <ElButton
      :loading="busy === 'preview'"
      :disabled="
        !!busy ||
        !service?.key_readable ||
        service.verification?.status !== 'supported'
      "
      @click="preview"
      >试听百炼音色</ElButton
    >
    <audio
      v-if="source"
      :src="source"
      controls
      preload="metadata"
      aria-label="角色音色试听"
    />
    <p v-if="error" role="alert" class="conversation-alert">{{ error }}</p>
  </div>
</template>
<style scoped>
.bailian-voice {
  width: 100%;
}
.editor-field,
details {
  margin: 12px 0;
}
audio {
  display: block;
  max-width: 100%;
  margin-top: 12px;
}
</style>
