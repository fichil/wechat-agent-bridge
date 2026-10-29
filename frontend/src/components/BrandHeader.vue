<script setup lang="ts">
import { useRouter } from "vue-router";
import { useAccountsStore } from "../stores/accounts";

defineProps<{ workspace?: boolean }>();
const accounts = useAccountsStore();
const router = useRouter();

function switchAccount() {
  accounts.select(null);
  void router.push("/accounts");
}
</script>

<template>
  <header class="brand-header">
    <RouterLink to="/accounts" class="brand" aria-label="微信工作台首页">
      <img src="/favicon.svg" alt="" width="36" height="36" />
      <span>微信工作台<small>AGENT BRIDGE</small></span>
    </RouterLink>
    <div class="header-actions">
      <div class="environment">
        <span class="local-dot" />本机空间<span class="environment-divider"
          >/</span
        >Windows
      </div>
      <ElButton v-if="workspace" @click="switchAccount">切换账号</ElButton>
    </div>
  </header>
</template>
