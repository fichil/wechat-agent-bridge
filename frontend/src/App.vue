<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useRoute } from "vue-router";
import BrandHeader from "./components/BrandHeader.vue";
import { useAccountsStore } from "./stores/accounts";
const accounts = useAccountsStore();
const route = useRoute();
const isWorkspace = computed(() => typeof route.params.accountId === "string");

onMounted(() => {
  void accounts.initialize();
});
</script>

<template>
  <div class="app-shell" :class="{ 'app-shell--workspace': isWorkspace }">
    <BrandHeader v-if="!isWorkspace" />
    <RouterView />
    <footer class="app-footer">
      <span>WECHAT AGENT BRIDGE</span><span>本机账号 · 独立工作空间</span>
    </footer>
  </div>
</template>
