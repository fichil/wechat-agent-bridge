<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { ElMessageBox, ElInput } from "element-plus";
import "element-plus/es/components/input/style/css";
import { api, type Sticker } from "../api";
import { onChange } from "../api/events";
import { settingsError } from "../role-cards";
import { useUnsavedChanges } from "../unsaved-changes";
import MediaPreview from "./MediaPreview.vue";
const items = ref<Sticker[]>([]);
const selected = ref<Sticker | null>(null);
const editing = ref(false);
const draft = ref({ name: "", description: "", enabled: true });
const uploaded = ref({ mime_type: "", data_base64: "" });
const baseline = ref("");
const search = ref("");
const busy = ref(false);
const error = ref("");
const notice = ref("");
let alive = true;
let pickGeneration = 0;
function mediaFetcher(id: string) {
  return async () => (await api.sticker(id)).media;
}
const serialized = computed(() =>
  JSON.stringify([draft.value, uploaded.value]),
);
const dirty = computed(
  () => editing.value && serialized.value !== baseline.value,
);
const beforeLeave = useUnsavedChanges(dirty, save, busy);
const visible = computed(() =>
  items.value.filter((i) =>
    `${i.name} ${i.description}`.includes(search.value.trim()),
  ),
);
const changed = computed(
  () =>
    !!selected.value &&
    items.value.find((i) => i.asset_id === selected.value?.asset_id)
      ?.revision !== selected.value.revision,
);
async function load() {
  try {
    const result = await api.stickers();
    if (alive) items.value = result.items;
  } catch (cause) {
    if (alive) error.value = settingsError(cause);
  }
}
async function open(item: Sticker | null) {
  if (!(await beforeLeave())) return;
  pickGeneration++;
  selected.value = item;
  draft.value = item
    ? { name: item.name, description: item.description, enabled: item.enabled }
    : { name: "", description: "", enabled: true };
  uploaded.value = { mime_type: "", data_base64: "" };
  baseline.value = serialized.value;
  editing.value = true;
  error.value = notice.value = "";
}
async function pick(event: Event) {
  const ticket = ++pickGeneration;
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  if (file.size > 8 * 1024 * 1024) {
    error.value = "图片不能超过 8 MiB。";
    return;
  }
  try {
    const result = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    if (!alive || ticket !== pickGeneration) return;
    uploaded.value = {
      mime_type: file.type,
      data_base64: result.slice(result.indexOf(",") + 1),
    };
    if (!draft.value.name)
      draft.value.name = file.name.replace(/\.[^.]+$/, "").slice(0, 100);
    error.value = "";
  } catch {
    error.value = "无法读取所选图片。";
  }
}
async function save(): Promise<boolean> {
  busy.value = true;
  error.value = notice.value = "";
  try {
    if (!draft.value.name.trim() || !draft.value.description.trim())
      throw new Error("请填写名称和使用场景。");
    const saved = selected.value
      ? await api.updateSticker({
          asset_id: selected.value.asset_id,
          expected_revision: selected.value.revision,
          ...draft.value,
        })
      : await api.createSticker({ ...draft.value, ...uploaded.value });
    if (!alive) return false;
    selected.value = saved;
    uploaded.value = { mime_type: "", data_base64: "" };
    baseline.value = serialized.value;
    notice.value = "表情包已保存。";
    await load();
    return true;
  } catch (cause) {
    if (alive)
      error.value =
        cause instanceof Error ? cause.message : settingsError(cause);
    return false;
  } finally {
    if (alive) busy.value = false;
  }
}
async function remove() {
  const item = selected.value;
  if (!item) return;
  try {
    await ElMessageBox.confirm(
      `删除“${item.name}”？已生成回复使用的素材会保留。`,
      "删除表情包",
      { confirmButtonText: "删除", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  busy.value = true;
  try {
    await api.deleteSticker(item.asset_id, item.revision);
    editing.value = false;
    selected.value = null;
    await load();
    notice.value = "表情包已删除。";
  } catch (cause) {
    error.value = settingsError(cause);
  } finally {
    busy.value = false;
  }
}
const off = onChange((event) => {
  if (event.type === "resync" || event.resources?.includes("stickers"))
    void load();
});
onBeforeUnmount(() => {
  alive = false;
  off();
});
void load();
</script>

<template>
  <div class="sticker-panel">
    <header class="sticker-toolbar">
      <div>
        <h2>表情包</h2>
        <p class="settings-note">
          所有账号共用，AI 会按角色和语境选用已启用的表情。
        </p>
      </div>
      <ElButton type="primary" :disabled="busy" @click="open(null)"
        >上传表情包</ElButton
      >
    </header>
    <p v-if="error" role="alert" class="conversation-alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <form v-if="editing" class="sticker-editor" @submit.prevent="save">
      <h3>{{ selected ? "编辑表情包" : "上传表情包" }}</h3>
      <p v-if="changed" role="alert">
        此素材已在其他页面修改或删除，草稿已保留。请重新打开最新版本后保存。
      </p>
      <label
        >名称<ElInput v-model="draft.name" maxlength="100" :disabled="busy"
      /></label>
      <label
        >使用场景<ElInput
          v-model="draft.description"
          type="textarea"
          :rows="3"
          maxlength="300"
          :disabled="busy"
          placeholder="描述画面、情绪和适合使用的场景，帮助 AI 选图"
      /></label>
      <label v-if="!selected"
        >图片<input
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          :disabled="busy"
          @change="pick"
      /></label>
      <p v-if="!selected" class="settings-note">
        支持 PNG、JPEG、WebP、GIF。宽高最多 1024，发送的 GIF 最多 1
        MiB；静态图片会转换为单帧表情。
      </p>
      <ElSwitch
        v-model="draft.enabled"
        active-text="允许 AI 选用"
        :disabled="busy"
      />
      <div class="editor-actions">
        <ElButton type="primary" native-type="submit" :loading="busy"
          >保存表情包</ElButton
        ><ElButton
          v-if="selected && !selected.builtin"
          type="danger"
          :disabled="busy"
          @click="remove"
          >删除</ElButton
        >
      </div>
    </form>
    <ElInput
      v-model="search"
      placeholder="搜索名称或使用场景"
      aria-label="搜索表情包"
      clearable
    />
    <div class="sticker-grid">
      <article
        v-for="item in visible"
        :key="item.asset_id"
        class="sticker-card"
      >
        <MediaPreview
          :identity="`${item.asset_id}:${item.revision}`"
          :label="item.name"
          :fetcher="mediaFetcher(item.asset_id)"
        />
        <h3>{{ item.name }}</h3>
        <p>{{ item.description }}</p>
        <div class="sticker-card-actions">
          <span
            >{{ item.enabled ? "已启用" : "已停用" }} ·
            {{ item.builtin ? "内置" : "自定义" }}</span
          ><ElButton :disabled="busy" @click="open(item)">编辑</ElButton>
        </div>
      </article>
    </div>
    <p v-if="!visible.length" class="settings-note">没有匹配的表情包。</p>
    <p class="settings-note">
      内置素材来自
      <a
        href="https://github.com/PC2005-cloud/dsh-pet"
        target="_blank"
        rel="noreferrer"
        >PC2005-cloud/dsh-pet</a
      >；形象原作者为
      <a
        href="https://space.bilibili.com/4456176"
        target="_blank"
        rel="noreferrer"
        >上善无形</a
      >
      和
      <a
        href="https://space.bilibili.com/4168597"
        target="_blank"
        rel="noreferrer"
        >ZipZipPipe</a
      >， 遵循
      <a
        href="https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans"
        target="_blank"
        rel="noreferrer"
        >CC BY-NC-SA 4.0</a
      >
      及非商业素材许可。上传的素材保存在当前后台。
    </p>
  </div>
</template>

<style scoped>
.sticker-panel {
  display: grid;
  gap: 18px;
}
.sticker-toolbar,
.sticker-card-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
h2,
h3,
p {
  margin: 0;
}
.sticker-toolbar p {
  margin-top: 8px;
}
.sticker-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(220px, 100%), 1fr));
  gap: 16px;
}
.sticker-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  min-width: 0;
  background: #fff;
}
.sticker-card p {
  font-size: 13px;
  line-height: 1.6;
  color: #65728a;
  flex: 1;
  overflow-wrap: anywhere;
}
.sticker-card h3 {
  font-size: 15px;
  overflow-wrap: anywhere;
}
.sticker-card-actions span {
  font-size: 12px;
  color: #65728a;
}
.sticker-editor {
  display: grid;
  gap: 14px;
  padding: 20px;
  border: 1px solid #d7e2f0;
  border-radius: 14px;
}
label {
  display: grid;
  gap: 8px;
}
input[type="file"] {
  max-width: 100%;
}
@media (max-width: 600px) {
  .sticker-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
