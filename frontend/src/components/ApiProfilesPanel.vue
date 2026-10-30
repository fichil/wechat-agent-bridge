<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { ElMessageBox } from "element-plus";
import {
  api,
  ApiFailure,
  type ApiProfile,
  type ProfileCreate,
  type ProfileTask,
} from "../api";
import { useAiConfigStore } from "../stores/ai-config";
import { useUnsavedChanges } from "../unsaved-changes";
import { settingsError } from "../role-cards";

const config = useAiConfigStore();
const selected = ref<ApiProfile | null>(null);
const editing = ref(false);
const draft = ref<ProfileCreate>(blank());
const loading = ref(false),
  saving = ref(false),
  error = ref(""),
  notice = ref("");
const search = ref("");
const pendingTask = ref<ProfileTask | null>(null);
const testing = ref(false);
const baseline = ref("");
const busy = computed(
  () => loading.value || saving.value || !!pendingTask.value,
);
const dirty = computed(
  () => editing.value && JSON.stringify(draft.value) !== baseline.value,
);
const beforeLeave = useUnsavedChanges(dirty, save, busy);
const visible = computed(() =>
  config.items.filter((p) =>
    `${p.name} ${p.provider} ${p.model}`
      .toLowerCase()
      .includes(search.value.toLowerCase()),
  ),
);
const latest = computed(() =>
  config.items.find((p) => p.profile_id === selected.value?.profile_id),
);
const changed = computed(
  () =>
    !!selected.value &&
    (!latest.value || latest.value.revision !== selected.value.revision),
);
const options = computed(
  () =>
    config.presets.find((p) => p.provider === draft.value.provider)?.options ??
    [],
);
const protocolNames: Record<string, string> = {
  openai_responses: "Responses",
  openai_chat_completions: "Chat Completions",
  anthropic_messages: "Anthropic Messages",
  gemini_generate_content: "Gemini GenerateContent",
};
let generation = 0;
async function finishTask(task: ProfileTask): Promise<ApiProfile> {
  pendingTask.value = task;
  const ticket = generation;
  while (task.status === "queued" || task.status === "running") {
    await new Promise((resolve) => setTimeout(resolve, 700));
    if (ticket !== generation) throw new Error("页面已切换");
    task = await api.profileTask(task.task_id);
    pendingTask.value = task;
  }
  if (task.status !== "completed" || !task.profile_id) {
    pendingTask.value = null;
    throw new ApiFailure(
      task.error || "ai_test_failed",
      task.message || "测试失败，配置未保存。",
    );
  }
  const profile = await api.apiProfile(task.profile_id);
  pendingTask.value = null;
  return profile;
}
function verificationText(profile: ApiProfile) {
  const result = profile.verification;
  if (!result || result.status === "unverified") return "尚未通过可用性测试";
  if (result.status === "failed")
    return `最近测试失败：${result.message || "请重试"}`;
  return `文本与 JSON 测试通过 · 图片${result.vision === "supported" ? "支持" : result.vision === "unsupported" ? "不支持" : "未验证"}`;
}
void config.load();
function blank(): ProfileCreate {
  return {
    name: "",
    provider: "openai",
    protocol: "openai_responses",
    base_url: "https://api.openai.com/v1",
    model: "",
    api_key: "",
    anthropic_version: "2026-10-21",
    workspace_id: "",
  };
}
function setDraft(profile: ApiProfile | null) {
  selected.value = profile;
  draft.value = profile
    ? {
        name: profile.name,
        provider: profile.provider,
        protocol: profile.protocol,
        base_url: profile.base_url,
        model: profile.model,
        api_key: "",
        anthropic_version: profile.anthropic_version,
        workspace_id: profile.workspace_id,
      }
    : blank();
  baseline.value = JSON.stringify(draft.value);
  editing.value = true;
  error.value = notice.value = "";
}
async function create() {
  if (await beforeLeave()) {
    generation++;
    setDraft(null);
  }
}
async function open(id: string) {
  if (!(await beforeLeave())) return;
  const ticket = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const result = await api.apiProfile(id);
    if (ticket === generation) setDraft(result);
  } catch (cause) {
    if (ticket === generation) error.value = settingsError(cause);
  } finally {
    if (ticket === generation) loading.value = false;
  }
}
function changeProvider() {
  const option = options.value[0];
  if (option) {
    draft.value.protocol = option.protocol;
    draft.value.base_url = option.base_url;
  }
}
function changeProtocol() {
  const option = options.value.find((o) => o.protocol === draft.value.protocol);
  if (option?.base_url) draft.value.base_url = option.base_url;
}
async function save(): Promise<boolean> {
  if (!editing.value || loading.value || saving.value) return false;
  const ticket = generation;
  saving.value = true;
  error.value = notice.value = "";
  try {
    const { api_key, ...fields } = draft.value;
    const task =
      pendingTask.value ??
      (selected.value
        ? await api.updateApiProfile({
            ...fields,
            ...(api_key ? { api_key } : {}),
            profile_id: selected.value.profile_id,
            expected_revision: selected.value.revision,
          })
        : await api.createApiProfile({ ...fields, api_key }));
    const result = await finishTask(task);
    if (ticket !== generation) return false;
    setDraft(result);
    notice.value = "API 配置已保存，所有微信账号共用。";
    await config.load();
    return true;
  } catch (cause) {
    if (ticket === generation) error.value = settingsError(cause);
    return false;
  } finally {
    if (ticket === generation) saving.value = false;
  }
}
async function testSaved() {
  if (!selected.value || loading.value || saving.value || dirty.value) return;
  saving.value = testing.value = true;
  error.value = notice.value = "";
  try {
    const result = await finishTask(
      pendingTask.value ??
        (await api.testProfile(
          selected.value.profile_id,
          selected.value.revision,
        )),
    );
    setDraft(result);
    notice.value = verificationText(result);
    await config.load();
  } catch (cause) {
    error.value = settingsError(cause);
    await config.load();
  } finally {
    saving.value = testing.value = false;
  }
}
async function makeDefault() {
  if (!selected.value || busy.value) return;
  saving.value = true;
  error.value = "";
  try {
    await config.setDefault({ api_profile_id: selected.value.profile_id });
    notice.value = "已设为全账号默认 API。";
  } catch (cause) {
    error.value = settingsError(cause);
  } finally {
    saving.value = false;
  }
}
async function remove() {
  if (!selected.value || busy.value) return;
  const profile = selected.value;
  const count = latest.value?.usage_count ?? profile.usage_count;
  if (count) {
    error.value = `此 API 配置正被 ${count} 个会话使用，请先更换配置或解除引用。`;
    return;
  }
  try {
    await ElMessageBox.confirm(
      `删除“${profile.name}”？${latest.value?.is_default ? "全局默认 API 将被清空。" : ""}${dirty.value ? "未保存修改也将丢弃。" : ""}`,
      "删除 API 配置",
      { confirmButtonText: "删除", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  saving.value = true;
  error.value = "";
  try {
    await api.deleteApiProfile(profile.profile_id, profile.revision);
    generation++;
    selected.value = null;
    editing.value = false;
    draft.value = blank();
    baseline.value = "";
    notice.value = "API 配置已删除。";
    await config.load();
  } catch (cause) {
    error.value = settingsError(cause);
  } finally {
    saving.value = false;
  }
}
onBeforeUnmount(() => {
  generation++;
  draft.value.api_key = "";
  baseline.value = "";
});
</script>

<template>
  <div class="role-toolbar">
    <div>
      <h2>Agent · API 配置</h2>
      <p class="intro-copy">配置与默认选择由所有微信账号共用</p>
    </div>
    <ElButton
      type="primary"
      :disabled="busy || !config.presets.length"
      @click="create"
      >新增 API 配置</ElButton
    >
  </div>
  <p class="ai-placeholder-note">
    保存前会测试文本与 JSON 输出，图片能力单独检测。所有微信账号共用。
  </p>
  <p v-if="config.error" class="conversation-alert" role="alert">
    {{ config.error }} <ElButton @click="config.load">重新加载配置</ElButton>
  </p>
  <p v-if="error" class="conversation-alert" role="alert">{{ error }}</p>
  <p v-if="notice" class="save-notice" role="status">{{ notice }}</p>
  <div class="role-layout">
    <section class="role-list" aria-label="API 配置列表">
      <input
        v-model="search"
        class="editor-input"
        type="search"
        aria-label="搜索 API 配置"
        placeholder="搜索名称、服务商或模型"
      />
      <p v-if="config.loading && !config.items.length" role="status">
        正在读取配置…
      </p>
      <p v-else-if="!visible.length" class="intro-copy">
        {{
          config.items.length
            ? "没有匹配的配置"
            : "还没有 API 配置，新增第一条后将自动设为默认。"
        }}
      </p>
      <button
        v-for="profile in visible"
        :key="profile.profile_id"
        type="button"
        class="role-list-item"
        :class="{ selected: selected?.profile_id === profile.profile_id }"
        :disabled="busy"
        @click="open(profile.profile_id)"
      >
        <strong
          >{{ profile.name }}
          <span v-if="profile.is_default" class="default-badge"
            >默认</span
          ></strong
        ><code>{{ profile.model }}</code
        ><small
          >{{ profile.provider }} · {{ profile.usage_count }} 个会话使用</small
        ><small v-if="!profile.key_readable">密钥无法读取，请重新填写</small>
        <small>{{ verificationText(profile) }}</small>
      </button>
    </section>
    <section class="role-editor" aria-label="API 配置编辑">
      <p v-if="loading" role="status">正在读取配置…</p>
      <template v-if="editing">
        <div class="role-editor-heading">
          <h3>
            {{ selected ? "编辑 API 配置" : "新增 API 配置" }}
            <span v-if="dirty" class="unsaved-label">未保存</span>
          </h3>
          <code>{{ selected?.profile_id ?? "保存后自动生成唯一编码" }}</code>
        </div>
        <p v-if="changed && !config.loading" class="conversation-alert">
          配置已在其他位置变更，当前草稿保留。<ElButton
            :disabled="busy"
            @click="open(selected!.profile_id)"
            >重新加载</ElButton
          >
        </p>
        <form @submit.prevent="save">
          <div class="editor-actions editor-savebar">
            <ElButton
              native-type="submit"
              type="primary"
              :loading="saving"
              :disabled="loading"
              >保存 API 配置</ElButton
            ><ElButton
              v-if="selected"
              :disabled="
                busy ||
                config.loading ||
                !config.defaults ||
                !!config.error ||
                latest?.is_default
              "
              @click="makeDefault"
              >{{ latest?.is_default ? "默认 API" : "设为默认 API" }}</ElButton
            ><ElButton
              v-if="selected"
              type="danger"
              plain
              :disabled="busy"
              @click="remove"
              >删除 API 配置</ElButton
            >
            <ElButton
              v-if="selected"
              :disabled="busy || dirty"
              :loading="testing"
              @click="testSaved"
              >测试可用性</ElButton
            >
          </div>
          <p v-if="saving" role="status">
            正在执行 API 测试，通过后才保存。可继续查看其他窗口。
          </p>
          <p v-if="pendingTask && !saving" class="conversation-alert">
            测试任务已提交。<ElButton
              @click="pendingTask.operation === 'test' ? testSaved() : save()"
              >继续查询测试结果</ElButton
            >
          </p>
          <p v-if="selected && dirty" class="settings-note">
            有未保存修改，请通过保存测试当前草稿。
          </p>
          <fieldset class="editor-fields" :disabled="busy || !!pendingTask">
            <label class="editor-field"
              ><span>配置名称 *</span
              ><input
                v-model="draft.name"
                class="editor-input"
                required
                maxlength="200"
            /></label>
            <label class="editor-field"
              ><span>服务商 *</span
              ><select
                aria-label="服务商 *"
                v-model="draft.provider"
                class="editor-input"
                @change="changeProvider"
              >
                <option
                  v-for="preset in config.presets"
                  :key="preset.provider"
                  :value="preset.provider"
                >
                  {{ preset.name }}
                </option>
              </select></label
            >
            <label class="editor-field"
              ><span>接口协议 *</span
              ><select
                aria-label="接口协议 *"
                v-model="draft.protocol"
                class="editor-input"
                @change="changeProtocol"
              >
                <option
                  v-for="option in options"
                  :key="option.protocol"
                  :value="option.protocol"
                >
                  {{ protocolNames[option.protocol] }}
                </option>
              </select></label
            >
            <label class="editor-field"
              ><span>API 基础地址 *</span
              ><input
                aria-label="API 基础地址 *"
                v-model="draft.base_url"
                class="editor-input"
                type="url"
                required
                maxlength="2048"
                autocomplete="off"
              /><small
                >填写服务地址，不含密钥、查询参数或完整生成接口路径。</small
              ></label
            >
            <label class="editor-field"
              ><span>模型名 *</span
              ><input
                v-model="draft.model"
                class="editor-input"
                required
                maxlength="256"
                placeholder="填写服务商提供的模型 ID"
            /></label>
            <label class="editor-field"
              ><span>API 密钥{{ selected ? "（留空保留原密钥）" : " *" }}</span
              ><input
                :aria-label="
                  selected ? 'API 密钥（留空保留原密钥）' : 'API 密钥 *'
                "
                v-model="draft.api_key"
                class="editor-input"
                type="password"
                :required="!selected"
                maxlength="8192"
                autocomplete="new-password"
                spellcheck="false"
              /><small v-if="selected">{{
                selected.key_readable
                  ? "密钥已配置"
                  : "密钥无法读取，请重新填写"
              }}</small></label
            >
            <template v-if="draft.protocol === 'anthropic_messages'"
              ><label class="editor-field"
                ><span>Anthropic API 版本 *</span
                ><input
                  v-model="draft.anthropic_version"
                  class="editor-input"
                  required
                  pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}" /></label
              ><label class="editor-field"
                ><span>工作空间 ID（可选）</span
                ><input
                  v-model="draft.workspace_id"
                  class="editor-input"
                  maxlength="200" /></label
            ></template>
          </fieldset>
        </form>
        <p class="settings-note">
          保存和测试会发送合成测试请求，可能产生 API 费用，不使用真实聊天。
          <template v-if="selected"
            >{{ verificationText(latest || selected) }}。 最近测试：{{
              (latest || selected).verification?.tested_at
                ? new Date(
                    (latest || selected).verification!.tested_at!,
                  ).toLocaleString("zh-CN")
                : "尚未测试"
            }}。 </template
          >密钥由当前 Windows
          用户加密保存，迁移到其他电脑或用户后通常需要重新填写。
        </p>
      </template>
      <p v-else class="intro-copy">选择配置进行编辑，或新增一条 API 配置。</p>
    </section>
  </div>
</template>
