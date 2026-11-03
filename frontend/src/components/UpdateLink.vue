<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from "vue";
import { api } from "../api";
const available = ref("");
let timer: ReturnType<typeof setInterval> | undefined;
async function refresh() {
  try {
    const state = await api.updates();
    available.value =
      state.can_install && state.latest?.ready ? state.latest.version : "";
  } catch {
    /* Checking a local notification must not interrupt the workspace. */
  }
}
onMounted(() => {
  void refresh();
  timer = setInterval(refresh, 60000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>
<template>
  <RouterLink to="/settings/updates"
    >版本更新<span v-if="available" class="new-version"
      >v{{ available }}</span
    ></RouterLink
  >
</template>
<style scoped>
.new-version {
  color: #087959;
  background: #e6f5ec;
  padding: 2px 5px;
  margin-left: 4px;
  border-radius: 5px;
  font-size: 12px;
}
</style>
