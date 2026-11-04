<script setup lang="ts">
defineProps<{
  processingType?: string | null;
  timings?: Record<string, number>;
}>();
const names: Record<string, string> = {
  realtime_reply: "实时回复",
  memory_backfill: "后台补记忆",
  group_threshold: "群聊主动处理",
};
function seconds(value?: number) {
  return value === undefined ? "未记录" : `${(value / 1000).toFixed(1)} 秒`;
}
</script>

<template>
  <p v-if="processingType" class="settings-note">
    {{ names[processingType] || processingType }}
  </p>
  <p
    v-if="
      timings &&
      ['received_to_request', 'model_request', 'generated_to_sent'].some(
        (key) => timings?.[key] !== undefined,
      )
    "
    class="settings-note"
    aria-label="回复阶段耗时"
  >
    接收至请求：{{ seconds(timings.received_to_request) }} · 模型调用：{{
      seconds(timings.model_request)
    }}
    · 生成至发送：{{ seconds(timings.generated_to_sent) }}
  </p>
</template>
