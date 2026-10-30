<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { ElSelect, ElOption } from "element-plus";
import "element-plus/es/components/select/style/css";
import "element-plus/es/components/option/style/css";
import { api, type Conversation, type ConversationPreferences } from "../api";
import { onChange } from "../api/events";
import { useRoleCardsStore } from "../stores/role-cards";
import { useAiConfigStore } from "../stores/ai-config";
import { settingsError } from "../role-cards";
import { useUnsavedChanges } from "../unsaved-changes";

const props = defineProps<{ accountId: string; conversation: Conversation }>();
const emit = defineEmits<{
  back: [];
  saved: [value: ConversationPreferences];
}>();
const cards = useRoleCardsStore();
const config = useAiConfigStore();
const original = ref<ConversationPreferences | null>(null);
const role = ref<string | null>(null);
const profile = ref<string | null>(null);
const memory = ref("");
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const notice = ref("");
const externalChange = ref(false);
const busy = computed(() => loading.value || saving.value);
const dirty = computed(
  () =>
    !!original.value &&
    (role.value !== original.value.role_card_code ||
      profile.value !== (original.value.api_profile_id ?? null) ||
      memory.value !== original.value.memory),
);
const beforeLeave = useUnsavedChanges(dirty, save, saving);
let generation = 0;
let reloadAfterSave = false;
void cards.load();
void config.load();

async function load(preserve = false) {
  const aid = props.accountId,
    cid = props.conversation.conversation_id;
  const ticket = ++generation;
  loading.value = true;
  error.value = "";
  if (!preserve) notice.value = "";
  if (!preserve) {
    original.value = null;
    role.value = null;
    profile.value = null;
    memory.value = "";
  }
  try {
    const result = await api.preferences(aid, cid);
    if (ticket !== generation) return;
    // A user can start typing while a notification-driven read is in flight.
    if (preserve && dirty.value) {
      externalChange.value =
        externalChange.value ||
        result.revision !== original.value?.revision ||
        result.memory_revision !== original.value?.memory_revision;
      if (original.value) original.value.effective = result.effective;
      return;
    }
    original.value = result;
    role.value = result.role_card_code;
    profile.value = result.api_profile_id ?? null;
    memory.value = result.memory;
    externalChange.value = false;
  } catch (cause) {
    if (ticket === generation) error.value = settingsError(cause);
  } finally {
    if (ticket === generation) loading.value = false;
  }
}
watch(
  () => [props.accountId, props.conversation.conversation_id],
  () => void load(),
  { immediate: true },
);
watch(
  dirty,
  () => {
    notice.value = "";
  },
  { flush: "sync" },
);

async function save(): Promise<boolean> {
  if (!original.value || busy.value) return false;
  if (!dirty.value) return true;
  const ticket = generation;
  saving.value = true;
  error.value = notice.value = "";
  try {
    const result = await api.savePreferences({
      account_id: props.accountId,
      conversation_id: props.conversation.conversation_id,
      expected_revision: original.value.revision,
      ...(role.value !== original.value.role_card_code
        ? { role_card_code: role.value }
        : {}),
      ...(profile.value !== (original.value.api_profile_id ?? null)
        ? { api_profile_id: profile.value }
        : {}),
      ...(memory.value !== original.value.memory
        ? {
            memory: memory.value,
            expected_memory_revision: original.value.memory_revision,
          }
        : {}),
    });
    if (ticket !== generation) return false;
    original.value = result;
    role.value = result.role_card_code;
    profile.value = result.api_profile_id ?? null;
    memory.value = result.memory;
    externalChange.value = false;
    notice.value = "会话设置已保存";
    emit("saved", result);
    return true;
  } catch (cause) {
    if (ticket === generation) error.value = settingsError(cause);
    return false;
  } finally {
    if (ticket === generation) {
      saving.value = false;
      if (reloadAfterSave) {
        reloadAfterSave = false;
        void load(true);
      }
    }
  }
}
async function reload() {
  if (await beforeLeave()) await load();
}
const unsubscribe = onChange((event) => {
  if (event.account_id && event.account_id !== props.accountId) return;
  const relevant =
    event.type === "ready" ||
    event.type === "resync" ||
    (event.resources?.some((r) =>
      [
        "conversation_preferences",
        "ai_defaults",
        "role_cards",
        "api_profiles",
      ].includes(r),
    ) &&
      (!event.conversation_ids?.length ||
        event.conversation_ids.includes(props.conversation.conversation_id)));
  if (!relevant) return;
  if (saving.value) {
    reloadAfterSave = true;
    return;
  }
  if (dirty.value && event.account_id) externalChange.value = true;
  void load(true);
});
onBeforeUnmount(() => {
  generation++;
  unsubscribe();
});
defineExpose({ beforeLeave });
</script>

<template>
  <section
    class="conversation-preferences"
    aria-label="单会话设置"
    :aria-busy="busy"
  >
    <header class="preferences-heading">
      <div>
        <p class="eyebrow">会话设置</p>
        <h3>{{ conversation.name }}</h3>
      </div>
      <ElButton @click="emit('back')">返回聊天</ElButton>
    </header>
    <p class="settings-note">
      仅应用于当前账号的此会话。关闭 AI 或更换角色卡都会保留记忆。
    </p>
    <p v-if="error" class="conversation-alert" role="alert">
      {{ error }} <ElButton :disabled="busy" @click="reload">重新加载</ElButton>
    </p>
    <p v-if="loading" role="status">正在读取设置…</p>
    <p v-if="externalChange" class="conversation-alert">
      设置已在其他位置变更，当前草稿保留。<ElButton
        :disabled="busy"
        @click="reload"
        >重新加载</ElButton
      >
    </p>
    <form v-if="original" @submit.prevent="save">
      <div class="editor-actions editor-savebar">
        <ElButton
          native-type="submit"
          type="primary"
          :loading="saving"
          :disabled="loading"
          >保存会话设置</ElButton
        ><span v-if="dirty" class="unsaved-label">未保存</span
        ><span v-if="notice" role="status" class="save-notice">{{
          notice
        }}</span>
      </div>
      <fieldset class="editor-fields" :disabled="busy">
        <label class="editor-field" for="conversation-role"
          ><span>角色卡</span></label
        >
        <ElSelect
          id="conversation-role"
          :model-value="role ?? ''"
          :disabled="busy"
          filterable
          clearable
          placeholder="使用全局默认角色卡"
          aria-label="角色卡"
          class="role-select"
          @update:model-value="role = $event || null"
        >
          <ElOption
            value=""
            :label="`使用全局默认${cards.items.find((c) => c.is_default) ? ' · ' + cards.items.find((c) => c.is_default)!.name : '（未设置）'}`"
          />
          <ElOption
            v-for="card in cards.items"
            :key="card.card_code"
            :value="card.card_code"
            :label="`${card.name} · ${card.card_code}`"
          />
          <ElOption
            v-if="role && !cards.items.some((c) => c.card_code === role)"
            :value="role"
            :label="`当前角色 · ${role}`"
            disabled
          />
        </ElSelect>
        <p v-if="cards.error" class="conversation-alert" role="alert">
          角色卡列表读取失败，当前选择已保留。<ElButton @click="cards.load"
            >重试角色卡</ElButton
          >
        </p>
        <label class="editor-field" for="conversation-api"
          ><span>API 配置</span></label
        >
        <ElSelect
          id="conversation-api"
          :model-value="profile ?? ''"
          :disabled="busy"
          filterable
          clearable
          placeholder="使用全局默认 API"
          aria-label="API 配置"
          class="role-select"
          @update:model-value="profile = $event || null"
        >
          <ElOption
            value=""
            :label="`使用全局默认${config.items.find((p) => p.is_default) ? ' · ' + config.items.find((p) => p.is_default)!.name : '（未设置）'}`"
          />
          <ElOption
            v-for="item in config.items"
            :key="item.profile_id"
            :value="item.profile_id"
            :label="`${item.name} · ${item.model}`"
          />
          <ElOption
            v-if="
              profile && !config.items.some((p) => p.profile_id === profile)
            "
            :value="profile"
            :label="`当前 API · ${profile}`"
            disabled
          />
        </ElSelect>
        <p v-if="config.error" class="conversation-alert" role="alert">
          API 列表读取失败，当前选择已保留。<ElButton @click="config.load"
            >重试 API 配置</ElButton
          >
        </p>
        <label class="editor-field memory-field"
          ><span>记忆 <small>可随时编辑，保存后仅存于本机</small></span
          ><textarea
            v-model="memory"
            class="editor-input memory-editor"
            rows="16"
            placeholder="尚无记忆，可在此手动填写。"
          />
        </label>
      </fieldset>
    </form>
  </section>
</template>
