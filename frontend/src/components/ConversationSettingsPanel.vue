<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { api } from "../api";
import AiSettingsPanel from "./AiSettingsPanel.vue";
import {
  defaultExclusions,
  exclusionOptions,
  conversationErrorText,
} from "../conversation-options";
const props = defineProps<{ accountId: string }>();
const activeTab = ref<"filters" | "ai">("filters");
function moveTab(event: KeyboardEvent) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  activeTab.value =
    event.key === "Home"
      ? "filters"
      : event.key === "End"
        ? "ai"
        : activeTab.value === "filters"
          ? "ai"
          : "filters";
  document.getElementById(`settings-tab-${activeTab.value}`)?.focus();
}
const exclusions = ref(defaultExclusions());
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const saved = ref(false);
const ready = ref(false);
let generation = 0;
async function load() {
  const ticket = ++generation;
  const id = props.accountId;
  loading.value = true;
  saving.value = false;
  saved.value = false;
  ready.value = false;
  error.value = "";
  exclusions.value = defaultExclusions();
  try {
    const result = await api.conversationSettings(id);
    if (ticket === generation) {
      exclusions.value = result.exclusions;
      ready.value = true;
    }
  } catch (cause) {
    if (ticket === generation) error.value = conversationErrorText(cause);
  } finally {
    if (ticket === generation) loading.value = false;
  }
}
watch(() => props.accountId, load, { immediate: true });
onBeforeUnmount(() => generation++);
async function save() {
  if (!ready.value || saving.value) return;
  const ticket = generation;
  saving.value = true;
  saved.value = false;
  error.value = "";
  try {
    const result = await api.saveConversationSettings(props.accountId, {
      ...exclusions.value,
    });
    if (ticket === generation) {
      exclusions.value = result.exclusions;
      saved.value = true;
    }
  } catch (cause) {
    if (ticket === generation) error.value = conversationErrorText(cause);
  } finally {
    if (ticket === generation) saving.value = false;
  }
}
function reset() {
  exclusions.value = defaultExclusions();
  saved.value = false;
}
</script>

<template>
  <div
    class="settings-menu"
    role="tablist"
    aria-label="设置分类"
    @keydown="moveTab"
  >
    <button
      id="settings-tab-filters"
      type="button"
      role="tab"
      :aria-selected="activeTab === 'filters'"
      aria-controls="settings-panel-filters"
      :tabindex="activeTab === 'filters' ? 0 : -1"
      @click="activeTab = 'filters'"
    >
      会话过滤
    </button>
    <button
      id="settings-tab-ai"
      type="button"
      role="tab"
      :aria-selected="activeTab === 'ai'"
      aria-controls="settings-panel-ai"
      :tabindex="activeTab === 'ai' ? 0 : -1"
      @click="activeTab = 'ai'"
    >
      AI 提示词
    </button>
  </div>
  <div
    v-show="activeTab === 'ai'"
    id="settings-panel-ai"
    role="tabpanel"
    aria-labelledby="settings-tab-ai"
  >
    <AiSettingsPanel />
  </div>
  <form
    v-show="activeTab === 'filters'"
    id="settings-panel-filters"
    role="tabpanel"
    aria-labelledby="settings-tab-filters"
    @submit.prevent="save"
  >
    <div class="settings-toolbar">
      <div class="settings-copy">
        <p class="intro-copy">
          仅应用于当前微信账号。开启开关表示排除，默认全部开启。
        </p>
        <p class="settings-note">
          取消排除后，服务号、公众号和折叠群聊仍以分类入口显示。
        </p>
      </div>
      <div v-if="ready" class="settings-actions">
        <ElButton type="primary" native-type="submit" :loading="saving"
          >保存设置</ElButton
        >
        <ElButton :disabled="saving" @click="reset">恢复默认</ElButton>
        <span v-if="saved" role="status">设置已保存</span>
      </div>
    </div>
    <p v-if="loading" role="status">正在读取设置…</p>
    <div v-if="error" class="conversation-alert" role="alert">
      {{ error }}
      <ElButton v-if="!ready && !loading" @click="load">重试</ElButton>
    </div>
    <div v-if="ready" class="conversation-settings">
      <div
        v-for="option in exclusionOptions"
        :key="option.key"
        class="filter-option"
      >
        <label :for="`exclude-${option.key}`"
          ><strong>{{ option.label }}</strong
          ><span>{{ option.description }}</span></label
        >
        <ElSwitch
          :id="`exclude-${option.key}`"
          v-model="exclusions[option.key]"
          :aria-label="`排除${option.label}`"
          :disabled="saving"
          @change="saved = false"
        />
      </div>
    </div>
  </form>
</template>
