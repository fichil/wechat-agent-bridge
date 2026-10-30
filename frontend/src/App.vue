<script setup lang="ts">
import { computed, watch } from "vue";
import { useRoute } from "vue-router";
import BrandHeader from "./components/BrandHeader.vue";
import { useAccountsStore } from "./stores/accounts";
import { authState } from "./auth";
const accounts = useAccountsStore();
const route = useRoute();
const isWorkspace = computed(() => typeof route.params.accountId === "string");

watch(
  () => authState.authenticated,
  (loggedIn) => {
    if (loggedIn) void accounts.initialize();
  },
  { immediate: true },
);
</script>

<template>
  <div class="app-shell" :class="{ 'app-shell--workspace': isWorkspace }">
    <BrandHeader v-if="authState.authenticated && !isWorkspace" />
    <RouterView />
    <footer v-if="authState.authenticated" class="app-footer">
      <span>WECHAT AGENT BRIDGE</span><span>微信账号 · 独立工作空间</span>
    </footer>
  </div>
</template>
