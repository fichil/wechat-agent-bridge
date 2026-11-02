<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import {
  api,
  type NotificationSettings,
  type NotificationUpdate,
} from "../api";
import { onChange } from "../api/events";
import { settingsError } from "../role-cards";
import { useUnsavedChanges } from "../unsaved-changes";
const original = ref<NotificationSettings | null>(null);
const draft = ref<NotificationUpdate>({
  expected_revision: 0,
  enabled: false,
  recipient: "",
  sender: "",
  host: "",
  port: 465,
  username: "",
  security: "ssl",
});
const password = ref("");
const busy = ref(false),
  error = ref(""),
  notice = ref(""),
  external = ref(false);
let generation = 0,
  disposed = false,
  timer: ReturnType<typeof setTimeout> | undefined;
const fields = [
  "enabled",
  "recipient",
  "sender",
  "host",
  "port",
  "username",
  "security",
] as const;
const dirty = computed(
  () =>
    !!original.value &&
    (!!password.value ||
      fields.some((k) => draft.value[k] !== original.value![k])),
);
const statuses: Record<string, string> = {
  queued: "等待发送",
  sending: "发送中",
  sent: "邮件已提交",
  failed: "发送失败",
  unknown: "结果待核对",
  cancelled: "已取消",
};
function apply(value: NotificationSettings) {
  original.value = value;
  draft.value = {
    expected_revision: value.revision,
    enabled: value.enabled,
    recipient: value.recipient,
    sender: value.sender,
    host: value.host,
    port: value.port,
    username: value.username,
    security: value.security,
  };
  password.value = "";
  external.value = false;
}
async function load() {
  const ticket = ++generation;
  try {
    const value = await api.notificationSettings();
    if (disposed || ticket !== generation) return;
    if (dirty.value) {
      external.value = value.revision !== original.value?.revision;
      if (original.value) original.value.latest = value.latest;
    } else apply(value);
    clearTimeout(timer);
    if (["queued", "sending"].includes(value.latest?.status ?? ""))
      timer = setTimeout(() => void load(), 2000);
  } catch (cause) {
    if (!disposed) error.value = settingsError(cause);
  }
}
async function save() {
  if (busy.value || !original.value) return false;
  busy.value = true;
  error.value = notice.value = "";
  generation++;
  try {
    const result = await api.saveNotificationSettings({
      ...draft.value,
      expected_revision: original.value.revision,
      ...(password.value ? { password: password.value } : {}),
    });
    if (disposed) return false;
    apply(result);
    notice.value = "邮件设置已保存。";
    return true;
  } catch (cause) {
    error.value = settingsError(cause);
    return false;
  } finally {
    busy.value = false;
  }
}
const beforeLeave = useUnsavedChanges(dirty, save, busy);
async function reload() {
  if (await beforeLeave()) {
    apply(original.value!);
    await load();
  }
}
async function test() {
  if (!original.value || dirty.value || busy.value) return;
  busy.value = true;
  error.value = notice.value = "";
  try {
    await api.testNotification(original.value.revision);
    notice.value = "测试邮件已排队。";
    await load();
  } catch (cause) {
    error.value = settingsError(cause);
  } finally {
    busy.value = false;
  }
}
const unsubscribe = onChange((event) => {
  if (
    !busy.value &&
    (event.type === "resync" ||
      event.resources?.includes("notification_settings"))
  )
    void load();
});
void load();
onBeforeUnmount(() => {
  disposed = true;
  generation++;
  password.value = "";
  clearTimeout(timer);
  unsubscribe();
});
</script>
<template>
  <section class="notification-settings" aria-label="邮件通知设置">
    <h3>邮件通知</h3>
    <p class="settings-note">
      所有账号共用。已在线账号离线或身份无法确认持续 1
      分钟后通知，同一次异常只提醒一次。后台需持续运行并能连接邮件服务器。
    </p>
    <p v-if="error" class="conversation-alert" role="alert">{{ error }}</p>
    <p v-if="external" class="conversation-alert">
      设置已在其他页面更新，当前草稿保留。<ElButton @click="reload"
        >重新加载</ElButton
      >
    </p>
    <form v-if="original" @submit.prevent="save" class="notification-form">
      <label class="notification-toggle"
        ><input
          type="checkbox"
          v-model="draft.enabled"
          :disabled="busy"
        />启用掉线邮件通知</label
      >
      <label
        >收件邮箱<input
          v-model="draft.recipient"
          type="email"
          :required="draft.enabled"
          maxlength="254"
          :disabled="busy"
      /></label>
      <label
        >发件邮箱<input
          v-model="draft.sender"
          type="email"
          :required="draft.enabled"
          maxlength="254"
          :disabled="busy"
      /></label>
      <label
        >SMTP 服务器<input
          v-model="draft.host"
          placeholder="例如 smtp.qq.com"
          :required="draft.enabled"
          maxlength="253"
          :disabled="busy"
      /></label>
      <label
        >端口<input
          v-model.number="draft.port"
          type="number"
          min="1"
          max="65535"
          required
          :disabled="busy"
      /></label>
      <label
        >加密方式<select v-model="draft.security" :disabled="busy">
          <option value="ssl">SSL/TLS（通常为 465）</option>
          <option value="starttls">STARTTLS（通常为 587）</option>
        </select></label
      >
      <label
        >SMTP 用户名<input
          v-model="draft.username"
          :required="draft.enabled"
          maxlength="254"
          autocomplete="off"
          :disabled="busy"
      /></label>
      <label
        >SMTP 授权码<input
          v-model="password"
          type="password"
          maxlength="8192"
          autocomplete="new-password"
          :placeholder="
            original.has_password
              ? '已保存，留空保留'
              : '填写邮箱服务提供的授权码'
          "
          :disabled="busy"
      /></label>
      <p
        v-if="original.has_password && !original.password_readable"
        class="conversation-alert"
      >
        已有授权码无法在当前 Windows 用户下读取，请重新填写。
      </p>
      <div class="editor-actions">
        <ElButton type="primary" native-type="submit" :loading="busy"
          >保存邮件设置</ElButton
        ><ElButton
          :disabled="busy || dirty || !original.password_readable"
          @click="test"
          >发送测试邮件</ElButton
        ><span v-if="dirty">未保存</span>
      </div>
      <p v-if="notice" role="status">{{ notice }}</p>
      <p v-if="original.latest" role="status">
        最近投递：{{ statuses[original.latest.status] }} · 已尝试
        {{ original.latest.attempts }} 次<span v-if="original.latest.message">
          · {{ original.latest.message }}</span
        >
      </p>
    </form>
  </section>
</template>
<style scoped>
.notification-settings {
  max-width: 760px;
  padding: 8px 0;
}
.notification-form {
  display: grid;
  gap: 16px;
  max-width: 600px;
}
.notification-form label {
  display: grid;
  gap: 6px;
  font-size: 14px;
}
.notification-form input:not([type="checkbox"]),
.notification-form select {
  font: inherit;
  padding: 9px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  width: 100%;
  box-sizing: border-box;
}
.notification-form .notification-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
}
.editor-actions {
  flex-wrap: wrap;
}
</style>
