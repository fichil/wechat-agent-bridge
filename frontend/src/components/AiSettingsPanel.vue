<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { api, type AiSettings } from "../api";
import { onChange } from "../api/events";
import { useUnsavedChanges } from "../unsaved-changes";
import { settingsError } from "../role-cards";
const original = ref<AiSettings | null>(null);
const draft = ref("");
const saving = ref(false);
const error = ref("");
const notice = ref("");
const changed = ref(false);
const dirty = computed(
  () => !!original.value && draft.value !== original.value.instructions,
);
let disposed = false;
let generation = 0;
async function load() {
  const ticket = ++generation;
  try {
    const result = await api.aiSettings();
    if (disposed || ticket !== generation) return;
    if (dirty.value) {
      changed.value = result.revision !== original.value?.revision;
      return;
    }
    original.value = result;
    draft.value = result.instructions;
    changed.value = false;
  } catch (cause) {
    if (!disposed) error.value = settingsError(cause);
  }
}
async function save() {
  if (!original.value || saving.value) return false;
  saving.value = true;
  const ticket = ++generation;
  error.value = notice.value = "";
  try {
    const result = await api.saveAiSettings(
      draft.value,
      original.value.revision,
    );
    if (disposed || ticket !== generation) return false;
    original.value = result;
    draft.value = result.instructions;
    changed.value = false;
    notice.value = "AI 提示词已保存，之后的所有 AI 调用生效。";
    return true;
  } catch (cause) {
    if (!disposed) error.value = settingsError(cause);
    return false;
  } finally {
    saving.value = false;
  }
}
const beforeLeave = useUnsavedChanges(dirty, save, saving);
async function reload() {
  if (await beforeLeave()) {
    original.value = null;
    await load();
  }
}
const unsubscribe = onChange((event) => {
  if (
    !saving.value &&
    (event.type === "resync" || event.resources?.includes("ai_settings"))
  )
    void load();
});
void load();
onBeforeUnmount(() => {
  disposed = true;
  unsubscribe();
});
</script>
<template>
  <section class="ai-settings" aria-label="AI 调用设置">
    <h3>AI 调用设置</h3>
    <p class="settings-note">
      所有微信账号、角色卡和 API
      共用。每次调用携带当前会话记忆与未处理消息；回复只在工作台展示。
    </p>
    <p v-if="error" class="conversation-alert" role="alert">{{ error }}</p>
    <p v-if="changed" class="conversation-alert">
      提示词已在其他页面更新，当前草稿保留。<ElButton @click="reload"
        >重新加载</ElButton
      >
    </p>
    <form v-if="original" @submit.prevent="save">
      <label class="editor-field"
        ><span>全局 AI 提示词</span
        ><textarea
          v-model="draft"
          class="editor-input"
          rows="9"
          maxlength="16000"
          required
          :disabled="saving"
        />
      </label>
      <p class="settings-note">
        程序会固定要求返回 memory 与 reply 两个 JSON 字段。恢复默认后需保存。
      </p>
      <div class="editor-actions">
        <ElButton type="primary" native-type="submit" :loading="saving"
          >保存 AI 提示词</ElButton
        >
        <ElButton
          :disabled="saving"
          @click="draft = original.default_instructions"
          >恢复默认提示词</ElButton
        >
        <span v-if="dirty" class="unsaved-label">未保存</span>
        <span v-if="notice && !dirty" class="save-notice" role="status">{{
          notice
        }}</span>
      </div>
    </form>
  </section>
</template>
