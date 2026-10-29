<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Account } from "../api";
const props = withDefaults(
  defineProps<{
    account: Pick<Account, "avatar" | "nickname">;
    size?: number;
  }>(),
  {
    size: 60,
  },
);
const broken = ref(false);
const source = computed(() => {
  const avatar = props.account.avatar;
  return avatar && /^image\/(png|jpeg|gif|webp)$/.test(avatar.mime_type)
    ? `data:${avatar.mime_type};base64,${avatar.data_base64}`
    : "";
});
watch(source, () => {
  broken.value = false;
});
</script>

<template>
  <div
    class="account-avatar"
    :style="{ width: `${size}px`, height: `${size}px` }"
  >
    <img
      v-if="source && !broken"
      :src="source"
      :alt="`${account.nickname || '微信账号'}的头像`"
      @error="broken = true"
    />
    <span v-else aria-label="暂无头像">{{
      (account.nickname || "微").slice(0, 1)
    }}</span>
  </div>
</template>
