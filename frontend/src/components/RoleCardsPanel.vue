<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type RoleCard } from "../api";
import { useRoleCardsStore } from "../stores/role-cards";
import { useAiConfigStore } from "../stores/ai-config";
import {
  blankCard,
  cardTextFields,
  parseCard,
  settingsError,
} from "../role-cards";
import { useUnsavedChanges } from "../unsaved-changes";

const cards = useRoleCardsStore();
const config = useAiConfigStore();
void config.load();
const selected = ref<RoleCard | null>(null);
const editing = ref(false);
const draft = ref(blankCard());
const mode = ref<"form" | "json">("form");
const raw = ref("");
const baseline = ref("");
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const notice = ref("");
const search = ref("");
const importInput = ref<HTMLInputElement>();
const busy = computed(() => loading.value || saving.value);
const serialized = computed(() =>
  mode.value === "json" ? raw.value : JSON.stringify(draft.value, null, 2),
);
const dirty = computed(
  () => editing.value && serialized.value !== baseline.value,
);
const visible = computed(() =>
  cards.items.filter((c) =>
    `${c.name} ${c.card_code}`
      .toLocaleLowerCase()
      .includes(search.value.toLocaleLowerCase()),
  ),
);
const latest = computed(() =>
  cards.items.find((c) => c.card_code === selected.value?.card_code),
);
const changed = computed(
  () =>
    !!selected.value &&
    (!latest.value || latest.value.revision !== selected.value.revision),
);
let generation = 0;
const beforeLeave = useUnsavedChanges(dirty, save, saving);
void cards.load();
onBeforeUnmount(() => generation++);

function setDraft(
  card: ReturnType<typeof blankCard>,
  existing: RoleCard | null,
  imported = false,
) {
  draft.value = card;
  selected.value = existing;
  raw.value = JSON.stringify(card, null, 2);
  baseline.value = imported ? "" : raw.value;
  editing.value = true;
  mode.value = "form";
  error.value = notice.value = "";
}
async function create() {
  if (await beforeLeave()) setDraft(blankCard(), null);
}
async function open(code: string) {
  if (!(await beforeLeave())) return;
  const ticket = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const result = await api.roleCard(code);
    if (ticket === generation)
      setDraft(parseCard(JSON.stringify(result.card)), result);
  } catch (cause) {
    if (ticket === generation) error.value = settingsError(cause);
  } finally {
    if (ticket === generation) loading.value = false;
  }
}
function changeMode(next: "form" | "json") {
  if (next === mode.value || busy.value) return;
  try {
    if (next === "form") draft.value = parseCard(raw.value);
    else raw.value = JSON.stringify(draft.value, null, 2);
    mode.value = next;
    error.value = "";
  } catch (cause) {
    error.value = settingsError(cause);
  }
}
async function save(): Promise<boolean> {
  if (busy.value || !editing.value) return false;
  const ticket = generation;
  try {
    const card = parseCard(serialized.value);
    saving.value = true;
    error.value = notice.value = "";
    const result = selected.value
      ? await api.updateRoleCard(
          selected.value.card_code,
          selected.value.revision,
          card,
        )
      : await api.createRoleCard(card);
    if (ticket !== generation) return false;
    const previousMode = mode.value;
    setDraft(parseCard(JSON.stringify(result.card)), result);
    mode.value = previousMode;
    notice.value = "角色卡已保存，所有微信账号共用。";
    await cards.load();
    return true;
  } catch (cause) {
    if (ticket === generation) error.value = settingsError(cause);
    return false;
  } finally {
    if (ticket === generation) saving.value = false;
  }
}
async function remove() {
  if (!selected.value || busy.value) return;
  const card = selected.value;
  if ((latest.value?.usage_count ?? card.usage_count) > 0) {
    error.value = `此角色卡正被 ${latest.value?.usage_count ?? card.usage_count} 个会话使用，请先解除全部引用。`;
    return;
  }
  try {
    await ElMessageBox.confirm(
      `删除“${card.name}”？${dirty.value ? "本次未保存的修改也将丢弃。" : ""}`,
      "删除角色卡",
      {
        confirmButtonText: "删除",
        cancelButtonText: "取消",
        type: "warning",
      },
    );
  } catch {
    return;
  }
  saving.value = true;
  error.value = "";
  try {
    await api.deleteRoleCard(card.card_code, card.revision);
    editing.value = false;
    selected.value = null;
    notice.value = "角色卡已删除。";
    await cards.load();
  } catch (cause) {
    error.value = settingsError(cause);
  } finally {
    saving.value = false;
  }
}
async function makeDefault() {
  if (!selected.value || busy.value) return;
  saving.value = true;
  error.value = "";
  try {
    await config.setDefault({ role_card_code: selected.value.card_code });
    await cards.load();
    notice.value = "已设为全账号默认角色卡。";
  } catch (cause) {
    error.value = settingsError(cause);
  } finally {
    saving.value = false;
  }
}
async function importFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file || !(await beforeLeave())) return;
  const ticket = ++generation;
  loading.value = true;
  try {
    if (!file.name.toLowerCase().endsWith(".json"))
      throw new Error("请选择 V2 角色卡 JSON 文件。");
    const card = parseCard(await file.text());
    if (ticket === generation) {
      setDraft(card, null, true);
      notice.value = "已导入草稿，保存后生成新的角色卡编码。";
    }
  } catch (cause) {
    if (ticket === generation) error.value = settingsError(cause);
  } finally {
    if (ticket === generation) loading.value = false;
  }
}
function exportFile() {
  try {
    const card = parseCard(serialized.value);
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(card, null, 2) + "\n"], {
        type: "application/json;charset=utf-8",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${selected.value?.card_code ?? "character-card"}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    error.value = "";
  } catch (cause) {
    error.value = settingsError(cause);
  }
}
watch(
  serialized,
  () => {
    notice.value = "";
  },
  { flush: "sync" },
);
</script>

<template>
  <div class="role-toolbar">
    <div>
      <h2>角色卡</h2>
      <p class="intro-copy">所有微信账号共用 · Character Card V2</p>
    </div>
    <div class="editor-actions">
      <ElButton :disabled="busy" type="primary" @click="create"
        >新增角色卡</ElButton
      >
      <ElButton :disabled="busy" @click="importInput?.click()"
        >导入 JSON</ElButton
      >
      <input
        ref="importInput"
        class="visually-hidden"
        type="file"
        accept=".json,application/json"
        aria-label="导入角色卡文件"
        @change="importFile"
      />
    </div>
  </div>
  <p v-if="cards.error" class="conversation-alert" role="alert">
    {{ cards.error }} <ElButton @click="cards.load">重试列表</ElButton>
  </p>
  <p v-if="error" class="conversation-alert" role="alert">{{ error }}</p>
  <p v-if="config.error" class="conversation-alert" role="alert">
    {{ config.error }}
    <ElButton @click="config.load">重新加载默认配置</ElButton>
  </p>
  <p v-if="notice" class="save-notice" role="status">{{ notice }}</p>
  <div class="role-layout">
    <section class="role-list" aria-label="角色卡列表">
      <input
        v-model="search"
        class="editor-input"
        type="search"
        aria-label="搜索角色卡"
        placeholder="搜索名称或编码"
      />
      <p v-if="cards.loading && !cards.items.length" role="status">
        正在读取角色卡…
      </p>
      <p v-else-if="!visible.length" class="intro-copy">
        {{
          cards.items.length
            ? "没有匹配的角色卡"
            : "还没有角色卡，新增或导入一张。"
        }}
      </p>
      <button
        v-for="card in visible"
        :key="card.card_code"
        type="button"
        class="role-list-item"
        :class="{ selected: selected?.card_code === card.card_code }"
        :disabled="busy"
        @click="open(card.card_code)"
      >
        <strong
          >{{ card.name }}
          <span v-if="card.is_default" class="default-badge">默认</span></strong
        ><code>{{ card.card_code }}</code
        ><small>{{ card.usage_count }} 个会话使用</small>
      </button>
    </section>
    <section class="role-editor" aria-label="角色卡编辑">
      <p v-if="loading" role="status">正在读取角色卡…</p>
      <template v-if="editing">
        <div class="role-editor-heading">
          <h3>
            {{ selected ? "编辑角色卡" : "新增角色卡"
            }}<span v-if="dirty" class="unsaved-label">未保存</span>
          </h3>
          <code>{{ selected?.card_code ?? "保存后自动生成唯一编码" }}</code>
        </div>
        <p v-if="changed && !cards.loading" class="conversation-alert">
          此角色卡已在其他位置变更，当前草稿保留。<ElButton
            :disabled="busy"
            @click="open(selected!.card_code)"
            >重新加载</ElButton
          >
        </p>
        <div class="editor-actions editor-tabs">
          <button
            type="button"
            :aria-pressed="mode === 'form'"
            :disabled="busy"
            @click="changeMode('form')"
          >
            中文表单
          </button>
          <button
            type="button"
            :aria-pressed="mode === 'json'"
            :disabled="busy"
            @click="changeMode('json')"
          >
            高级 JSON
          </button>
        </div>
        <form @submit.prevent="save">
          <div class="editor-actions editor-savebar">
            <ElButton
              native-type="submit"
              type="primary"
              :loading="saving"
              :disabled="loading"
              >保存角色卡</ElButton
            >
            <ElButton :disabled="busy" @click="exportFile">导出 JSON</ElButton>
            <ElButton
              v-if="selected"
              :disabled="
                busy ||
                config.loading ||
                !config.defaults ||
                !!config.error ||
                latest?.is_default
              "
              @click="makeDefault"
              >{{
                latest?.is_default ? "默认角色卡" : "设为默认角色卡"
              }}</ElButton
            >
            <ElButton
              v-if="selected"
              :disabled="busy"
              type="danger"
              plain
              @click="remove"
              >删除角色卡</ElButton
            >
          </div>
          <fieldset :disabled="busy" class="editor-fields">
            <template v-if="mode === 'form'">
              <label
                v-for="field in cardTextFields"
                :key="field.key"
                class="editor-field"
              >
                <span
                  >{{ field.label }}{{ field.key === "name" ? " *" : "" }}</span
                >
                <input
                  v-if="'short' in field"
                  v-model="draft.data[field.key]"
                  class="editor-input"
                  :required="field.key === 'name'"
                  :maxlength="field.key === 'name' ? 200 : undefined"
                />
                <textarea
                  v-else
                  v-model="draft.data[field.key]"
                  class="editor-input"
                  rows="3"
                />
              </label>
              <div
                v-for="group in ['tags', 'alternate_greetings'] as const"
                :key="group"
                class="editor-field"
              >
                <span>{{ group === "tags" ? "标签" : "备用开场白" }}</span>
                <div
                  v-for="(_, index) in draft.data[group]"
                  :key="index"
                  class="array-field"
                >
                  <textarea
                    v-if="group === 'alternate_greetings'"
                    v-model="draft.data[group][index]"
                    class="editor-input"
                    :aria-label="`备用开场白 ${index + 1}`"
                    rows="2"
                  />
                  <input
                    v-else
                    v-model="draft.data[group][index]"
                    class="editor-input"
                    :aria-label="`标签 ${index + 1}`"
                  />
                  <ElButton
                    :aria-label="`删除${group === 'tags' ? '标签' : '备用开场白'} ${index + 1}`"
                    @click="draft.data[group].splice(index, 1)"
                    >移除</ElButton
                  >
                </div>
                <ElButton @click="draft.data[group].push('')"
                  >添加{{ group === "tags" ? "标签" : "备用开场白" }}</ElButton
                >
              </div>
              <p class="settings-note">
                角色书、扩展字段及完整数据可在高级 JSON
                中编辑。表单保存会保留这些数据。
              </p>
            </template>
            <label v-else class="editor-field"
              ><span>完整 V2 JSON</span
              ><textarea
                v-model="raw"
                class="editor-input json-editor"
                rows="28"
                spellcheck="false"
              />
            </label>
          </fieldset>
        </form>
      </template>
      <div v-else class="editor-empty">
        <h3>为会话准备角色</h3>
        <p>新建角色卡，或从左侧选择一张进行编辑。</p>
      </div>
    </section>
  </div>
</template>
