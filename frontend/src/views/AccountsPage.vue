<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import AccountAvatar from "../components/AccountAvatar.vue";
import CompatibilityPanel from "../components/CompatibilityPanel.vue";
import { useAccountsStore } from "../stores/accounts";
import { formatTime, reasonText } from "../reasons";
const store = useAccountsStore();
const router = useRouter();
const entering = ref<string | null>(null);

async function enter(id: string) {
  entering.value = id;
  const account = await store.enter(id);
  entering.value = null;
  if (account) await router.push(`/workspace/${id}/overview`);
}
</script>

<template>
  <main class="accounts-page">
    <section class="page-intro">
      <div>
        <h1>从一个微信账号开始</h1>
        <p class="intro-copy">选择已保存的微信账号；离线也能查看已入库历史。</p>
      </div>
      <ElButton
        class="refresh-button"
        :loading="store.scanning"
        :disabled="!!entering"
        @click="store.scan()"
        >{{ store.scanning ? "正在识别" : "重新识别" }}</ElButton
      >
    </section>
    <ElAlert
      v-if="store.notice"
      :title="store.notice"
      type="info"
      show-icon
      :closable="false"
      class="status-alert"
    />
    <ElAlert
      v-if="store.error"
      :title="store.error"
      type="error"
      show-icon
      :closable="false"
      class="status-alert"
    />
    <CompatibilityPanel />
    <section class="accounts-panel" aria-label="微信账号列表">
      <div class="panel-heading">
        <h2>
          可用账号
          <span class="count-badge">{{
            store.accounts.filter((item) => item.account_id).length
          }}</span>
        </h2>
        <span class="subtle">当前 Windows 用户</span>
      </div>
      <div
        v-if="!store.initialized || (store.scanning && !store.accounts.length)"
        class="scan-state"
        role="status"
      >
        <div class="scan-orbit">
          <img src="/favicon.svg" width="42" height="42" alt="" />
        </div>
        <h3>正在识别本机微信</h3>
        <p>正在读取账号资料和头像，请稍候。</p>
        <p class="subtle">多个实例可能需要约 3 分钟，你可以继续使用微信。</p>
      </div>
      <template v-else>
        <ElAlert
          v-if="store.snapshot?.exit_code === 2"
          title="部分账号信息尚未确认，已确认的账号仍可进入。"
          type="warning"
          :closable="false"
          show-icon
          class="status-alert"
        />
        <div v-if="store.accounts.length" class="account-grid">
          <article
            v-for="(account, index) in store.accounts"
            :key="account.account_id ?? index"
            class="account-card"
            :class="{ 'is-unresolved': !account.account_id }"
            data-testid="account-card"
          >
            <div class="card-top">
              <div class="account-identity">
                <AccountAvatar :account="account" :size="52" />
                <h3>
                  {{
                    account.nickname ||
                    (account.wxid ? "昵称暂未取得" : "未确认的微信实例")
                  }}
                </h3>
              </div>
              <ElTag
                :type="account.status === 'complete' ? 'success' : 'warning'"
                effect="plain"
                round
                >{{
                  account.source_state === "offline" ||
                  account.source_state === "unknown"
                    ? "历史可用"
                    : account.status === "complete"
                      ? "已识别"
                      : account.account_id
                        ? "资料不完整"
                        : "身份待确认"
                }}</ElTag
              >
            </div>
            <dl class="account-fields">
              <div>
                <dt>微信号</dt>
                <dd>{{ account.wechat_id || "未设置或未取得" }}</dd>
              </div>
              <div>
                <dt>wxid</dt>
                <dd>{{ account.wxid || "未确认" }}</dd>
              </div>
              <div v-if="account.account_id">
                <dt>最近同步</dt>
                <dd>{{ formatTime(account.last_synced_at) }}</dd>
              </div>
            </dl>
            <p v-if="account.reason" class="reason-copy">
              {{ reasonText(account.reason) }}
            </p>
            <div class="card-bottom">
              <span class="subtle"
                >{{ account.instances?.length ?? 0 }} 个客户端实例</span
              ><ElButton
                type="primary"
                :disabled="
                  !account.account_id ||
                  (!!entering && entering !== account.account_id)
                "
                :loading="
                  !!account.account_id && entering === account.account_id
                "
                @click="account.account_id && enter(account.account_id)"
                >{{ account.account_id ? "进入工作台" : "暂不可进入"
                }}<span aria-hidden="true">&nbsp; →</span></ElButton
              >
            </div>
          </article>
        </div>
        <div v-else class="empty-state">
          <div class="empty-symbol" aria-hidden="true">◎</div>
          <h3>
            {{
              store.error
                ? "暂时无法显示账号"
                : store.snapshot?.summary.coverage_complete
                  ? "还没有发现已登录的微信"
                  : "暂未确认可用账号"
            }}
          </h3>
          <p>
            {{
              store.error
                ? "检查本地服务后，点击上方“重新识别”。"
                : "请先在本机登录微信，然后重新识别。"
            }}
          </p>
        </div>
        <div v-if="store.snapshot?.issues?.length" class="issue-list">
          <p v-for="(issue, index) in store.snapshot.issues" :key="index">
            {{ reasonText(issue.reason)
            }}<span v-if="issue.pid" class="subtle">
              · PID {{ issue.pid }}</span
            >
          </p>
        </div>
      </template>
      <div class="snapshot-caption">
        <span>最近识别：{{ formatTime(store.snapshot?.scanned_at) }}</span
        ><span>离线账号保留已入库历史</span>
      </div>
    </section>
    <div class="scope-note">
      <span class="note-icon" aria-hidden="true">i</span>
      <p>
        账号列表覆盖当前 Windows
        用户的全部会话。同一微信账号的多个实例会合并展示。
      </p>
    </div>
  </main>
</template>
