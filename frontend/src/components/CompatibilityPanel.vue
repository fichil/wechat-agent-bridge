<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { api, type CompatibilitySnapshot } from "../api";

const state = ref<CompatibilitySnapshot | null>(null);
const error = ref("");
const busy = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;
const names: Record<string, string> = {
  account: "账号识别",
  messages: "会话消息",
  images: "图片",
  stickers: "表情",
  send_text: "文本发送",
};

async function refresh() {
  if (busy.value) return;
  busy.value = true;
  try {
    state.value = await api.compatibility();
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "兼容状态暂不可用";
  } finally {
    busy.value = false;
  }
}
onMounted(() => {
  void refresh();
  timer = setInterval(() => void refresh(), 10000);
});
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <section class="compatibility-panel" aria-label="微信版本兼容">
    <div class="panel-heading">
      <h2>微信版本兼容</h2>
      <ElButton :loading="busy" @click="refresh">检查状态</ElButton>
    </div>
    <ElAlert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <template v-if="state?.enforced">
      <p v-if="state.held || state.migration_pending" role="status">
        {{
          state.migration_pending
            ? "迁移账号正在自动检查；就绪的账号会独立恢复。"
            : state.message
        }}
      </p>
      <p v-for="item in state.unresolved_instances" :key="item.pid">
        微信 {{ item.version }}：身份或兼容性尚未确认。
      </p>
      <article
        v-for="account in state.accounts"
        :key="account.account_id"
        class="compatibility-account"
      >
        <div>
          <strong>{{
            [...new Set((account.instances ?? []).map((i) => i.version))].join(
              " / ",
            ) || "历史账号"
          }}</strong>
          <span class="subtle"> · {{ account.nickname || "未命名账号" }}</span>
        </div>
        <p>{{ account.message }}</p>
        <div class="compatibility-capabilities">
          <ElTag
            v-for="(supported, name) in account.capabilities"
            :key="name"
            :type="supported ? 'success' : 'warning'"
          >
            {{ names[name] || name }}：{{ supported ? "已适配" : "未适配" }}
          </ElTag>
        </div>
        <ElTag :type="account.auto_state === 'ready' ? 'success' : 'warning'">
          {{
            account.auto_state === "ready"
              ? "已就绪"
              : account.auto_state === "checking"
                ? "自动检查中"
                : "等待条件恢复"
          }}
        </ElTag>
      </article>
      <p
        v-if="!state.accounts.length && !state.unresolved_instances.length"
        class="subtle"
      >
        等待识别微信账号；历史归档仍可使用。
      </p>
    </template>
  </section>
</template>

<style scoped>
.compatibility-panel {
  margin-bottom: 24px;
  padding: 24px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 16px;
  background: var(--el-bg-color);
}
.compatibility-account {
  padding: 16px 0;
  border-top: 1px solid var(--el-border-color-light);
}
.compatibility-capabilities {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
</style>
