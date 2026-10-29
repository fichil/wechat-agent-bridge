<script setup lang="ts">
import { onMounted } from "vue";
import { useAccountsStore } from "./stores/accounts";
const accounts = useAccountsStore();
onMounted(() => {
  void accounts.initialize();
});
</script>

<template>
  <div class="app-shell">
    <header class="brand-header">
      <RouterLink to="/accounts" class="brand" aria-label="微信工作台首页">
        <img src="/favicon.svg" alt="" width="36" height="36" />
        <span>微信工作台<small>AGENT BRIDGE</small></span>
      </RouterLink>
      <div class="environment">
        <span class="local-dot" />本机空间<span class="environment-divider"
          >/</span
        >Windows
      </div>
    </header>
    <RouterView />
    <footer class="app-footer">
      <span>WECHAT AGENT BRIDGE</span><span>本机账号 · 独立工作空间</span>
    </footer>
  </div>
</template>
