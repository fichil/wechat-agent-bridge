<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from "vue";
import { api } from "../api";
type State = Awaited<ReturnType<typeof api.updates>>;
const state = ref<State>();
const busy = ref(false),
  notice = ref(""),
  confirming = ref<State["latest"]>(null);
const pending = computed(
  () =>
    !!state.value?.task &&
    !["succeeded", "failed", "rolled_back"].includes(state.value.task.stage),
);
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false,
  polling = false;
let initialVersion: string | undefined;
const timeText = (value: string | number) =>
  new Date(value).toLocaleString("zh-CN", { hour12: false });
function received(next: State) {
  if (disposed) return;
  if (
    initialVersion &&
    next.current_version !== initialVersion &&
    next.task?.stage === "succeeded"
  ) {
    location.reload();
    return;
  }
  initialVersion ??= next.current_version;
  state.value = next;
  notice.value = "";
}
async function refresh() {
  if (disposed || polling || busy.value) return;
  polling = true;
  try {
    received(await api.updates());
  } catch {
    if (!disposed)
      notice.value = pending.value
        ? "正在等待后台重新连接，页面会自动恢复。"
        : "无法读取版本状态，稍后将自动重试。";
  } finally {
    polling = false;
  }
}
async function poll() {
  await refresh();
  if (!disposed)
    timer = setTimeout(poll, pending.value || notice.value ? 2000 : 15000);
}
async function check() {
  busy.value = true;
  try {
    received(await api.checkUpdates());
  } catch (e) {
    notice.value = e instanceof Error ? e.message : "检查失败，请重试。";
  } finally {
    busy.value = false;
  }
}
async function install() {
  const target = confirming.value;
  if (!target || busy.value) return;
  confirming.value = null;
  busy.value = true;
  try {
    received(await api.installUpdate(target.version, target.release_id));
  } catch (e) {
    notice.value = e instanceof Error ? e.message : "升级请求未成功。";
  } finally {
    busy.value = false;
    clearTimeout(timer);
    void poll();
  }
}
onMounted(poll);
onBeforeUnmount(() => {
  disposed = true;
  clearTimeout(timer);
});
</script>
<template>
  <main class="accounts-page">
    <section class="content-panel update-panel">
      <h1>版本更新</h1>
      <p v-if="!state && !notice">正在读取当前版本…</p>
      <template v-if="state">
        <h2>
          当前版本：{{
            state.current_version === "source"
              ? "源码运行"
              : "v" + state.current_version
          }}
        </h2>
        <p v-if="state.current_commit !== 'source'">
          构建提交：<code>{{ state.current_commit.slice(0, 12) }}</code>
        </p>
        <p v-if="state.mode === 'managed'">此服务器由 main 自动部署管理。</p>
        <p v-else-if="!state.can_install">
          此安装不支持程序内升级，可从发布页获取正式便携包。
        </p>
        <template v-else>
          <p>启动后及每 24 小时检查一次。只有你确认升级后才会下载更新包。</p>
          <p v-if="state.checked_at">
            上次检查：{{ timeText(state.checked_at * 1000) }}
          </p>
          <button :disabled="busy || pending || state.checking" @click="check">
            {{ busy || state.checking ? "正在处理…" : "检查更新" }}
          </button>
          <p v-if="state.error" role="alert">{{ state.error }}</p>
          <article v-if="state.latest">
            <h2>发现 v{{ state.latest.version }}</h2>
            <p>发布于 {{ timeText(state.latest.published_at) }}</p>
            <pre>{{ state.latest.notes || "此版本没有附加更新说明。" }}</pre>
            <p v-if="state.latest.reason">{{ state.latest.reason }}</p>
            <button
              class="primary"
              :disabled="busy || pending || !state.latest.ready"
              @click="confirming = state.latest"
            >
              一键升级
            </button>
          </article>
          <p v-else-if="state.checked_at && !state.error">
            当前已是最新正式版本。
          </p>
        </template>
        <div v-if="state.task" role="status">
          <h2>升级到 v{{ state.task.version }}</h2>
          <p>{{ state.task.message }}</p>
          <progress
            v-if="state.task.total"
            :value="state.task.downloaded"
            :max="state.task.total"
          ></progress>
        </div>
        <a :href="state.releases_url" target="_blank" rel="noopener noreferrer"
          >在 GitHub 查看所有版本</a
        >
      </template>
      <p v-if="notice" role="status">{{ notice }}</p>
      <div
        v-if="confirming"
        class="update-overlay"
        @click.self="confirming = null"
      >
        <section
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-version"
          @keydown.esc="confirming = null"
        >
          <h2 id="confirm-version">升级到 v{{ confirming.version }}？</h2>
          <p>
            程序会先下载并校验更新，等待在途任务结束并备份数据，再重启后台。聊天和配置会保留。
          </p>
          <button autofocus @click="confirming = null">稍后</button>
          <button class="primary" :disabled="busy" @click="install">
            确认升级
          </button>
        </section>
      </div>
    </section>
  </main>
</template>
<style scoped>
.update-panel {
  max-width: 800px;
  margin: 0 auto;
  padding: 28px;
  line-height: 1.6;
}
.update-panel h2 {
  font-size: 18px;
  margin-top: 18px;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
}
button {
  padding: 7px 14px;
  border-radius: 6px;
  border: 1px solid #b8c8c1;
  background: white;
  color: #253b31;
  cursor: pointer;
}
button:disabled {
  opacity: 0.55;
  cursor: default;
}
.primary {
  color: white;
  background: #13734f;
  margin-left: 6px;
}
article,
[role="status"] {
  margin: 18px 0;
}
.update-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  place-items: center;
  background: #14233466;
  padding: 20px;
}
.update-overlay section {
  background: white;
  padding: 24px;
  border-radius: 12px;
  max-width: 440px;
}
progress {
  width: 100%;
}
</style>
