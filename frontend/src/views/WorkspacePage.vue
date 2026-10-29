<script setup lang="ts">
import { computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AccountAvatar from "../components/AccountAvatar.vue";
import BrandHeader from "../components/BrandHeader.vue";
import ConversationsPanel from "../components/ConversationsPanel.vue";
import ConversationSettingsPanel from "../components/ConversationSettingsPanel.vue";
import RoleCardsPanel from "../components/RoleCardsPanel.vue";
import { useAccountsStore } from "../stores/accounts";
import { formatTime, reasonText } from "../reasons";
const route = useRoute();
const router = useRouter();
const store = useAccountsStore();
const account = computed(() =>
  store.accounts.find((item) => item.account_id === route.params.accountId),
);
const section = computed(() => route.params.section || "overview");
const entries = [
  { key: "overview", label: "账号概览", icon: "◫" },
  { key: "conversations", label: "会话", icon: "◌" },
  { key: "role-cards", label: "角色卡", icon: "▤" },
  { key: "agents", label: "Agent", icon: "◇" },
  { key: "settings", label: "设置", icon: "⚙" },
];
const title = computed(
  () => entries.find((item) => item.key === section.value)?.label ?? "账号概览",
);

watch(
  () => [store.initialized, store.scanning, account.value] as const,
  ([initialized, scanning, selected]) => {
    if (!initialized || scanning) return;
    if (!selected?.account_id) {
      store.select(null);
      store.notice = store.notice || "所选账号已失效，请重新选择。";
      void router.replace("/accounts");
    } else store.select(selected.account_id);
  },
  { immediate: true },
);
</script>

<template>
  <main class="workspace-page">
    <div class="workspace-layout">
      <aside class="sidebar">
        <BrandHeader workspace />
        <template v-if="account">
          <div class="selected-account">
            <AccountAvatar :account="account" :size="48" />
            <div>
              <p class="eyebrow">当前工作空间</p>
              <h1>{{ account.nickname || account.wxid }}</h1>
            </div>
          </div>
          <p class="sidebar-label">工作台</p>
          <nav aria-label="工作台导航">
            <RouterLink
              v-for="entry in entries"
              :key="entry.key"
              :to="`/workspace/${account.account_id}/${entry.key}`"
              :class="{ active: section === entry.key }"
              ><span aria-hidden="true">{{ entry.icon }}</span
              >{{ entry.label
              }}<span v-if="entry.key === 'agents'" class="soon-dot"
            /></RouterLink>
          </nav>
          <div class="sidebar-note">
            会话与记忆按账号独立<br />角色卡由所有账号共用
          </div>
        </template>
      </aside>
      <div
        v-if="!store.initialized || (store.scanning && !store.accounts.length)"
        class="workspace-content scan-state"
        role="status"
      >
        <div class="scan-orbit">
          <img src="/favicon.svg" width="42" height="42" alt="" />
        </div>
        <h3>正在重新确认所选账号</h3>
        <p>正在读取本机微信信息，请稍候。</p>
      </div>
      <section
        v-else-if="account"
        class="workspace-content"
        :aria-label="title"
      >
        <template v-if="section === 'overview'"
          ><p class="intro-copy">查看当前账号资料与本机客户端实例。</p>
          <div class="detail-grid">
            <div>
              <span>微信昵称</span
              ><strong>{{ account.nickname || "暂未取得" }}</strong>
            </div>
            <div>
              <span>微信号</span
              ><strong>{{ account.wechat_id || "未设置或未取得" }}</strong>
            </div>
            <div>
              <span>wxid</span><strong class="mono">{{ account.wxid }}</strong>
            </div>
            <div>
              <span>最近识别</span
              ><strong>{{ formatTime(store.snapshot?.scanned_at) }}</strong>
            </div>
            <div>
              <span>微信状态</span>
              <strong>{{
                account.source_state === "online"
                  ? "在线"
                  : account.source_state === "offline"
                    ? "离线，历史仍可查看"
                    : "正在确认，历史仍可查看"
              }}</strong>
            </div>
            <div>
              <span>最近同步</span
              ><strong>{{ formatTime(account.last_synced_at) }}</strong>
            </div>
            <div>
              <span>监控起始时间</span
              ><strong>{{ formatTime(account.monitoring_started_at) }}</strong>
            </div>
            <div>
              <span>后台同步</span>
              <strong>{{
                account.sync_issues?.length
                  ? "部分来源等待重试"
                  : account.sync_pending
                    ? "正在建立基线或补齐增量"
                    : account.source_state === "online"
                      ? "每十秒检查变化"
                      : "等待微信上线"
              }}</strong>
            </div>
          </div>
          <p v-if="account.reason" class="reason-copy">
            {{ reasonText(account.reason) }}
          </p>
          <h3 class="instances-heading">
            客户端实例
            <span class="count-badge">{{
              account.instances?.length ?? 0
            }}</span>
          </h3>
          <ElTable :data="account.instances ?? []" class="instances-table"
            ><ElTableColumn prop="pid" label="进程 PID" /><ElTableColumn
              prop="session_id"
              label="Windows 会话" /><ElTableColumn
              prop="version"
              label="微信版本"
          /></ElTable>
          <div class="workspace-note">
            在会话页面查看消息摘要，在设置中调整当前账号的过滤规则。
          </div></template
        >
        <ConversationsPanel
          v-else-if="section === 'conversations' && account.account_id"
          :account-id="account.account_id"
        />
        <RoleCardsPanel v-else-if="section === 'role-cards'" />
        <ConversationSettingsPanel
          v-else-if="section === 'settings' && account.account_id"
          :account-id="account.account_id"
        />
        <div v-else class="placeholder-state">
          <div class="placeholder-icon" aria-hidden="true">
            {{ entries.find((item) => item.key === section)?.icon }}
          </div>
          <h3>功能待接入</h3>
          <p>{{ title }}将围绕当前账号展开，工作空间已准备就绪。</p>
          <ElButton
            @click="router.push(`/workspace/${account.account_id}/overview`)"
            >返回账号概览</ElButton
          >
        </div>
      </section>
    </div>
  </main>
</template>
