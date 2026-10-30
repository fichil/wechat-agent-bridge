<script setup lang="ts">
import { ref } from "vue";
import { authState, logout, changePassword } from "../auth";
const editing = ref(false);
const current = ref("");
const password = ref("");
const confirmation = ref("");
const error = ref("");
const busy = ref(false);
function close() {
  editing.value = false;
  current.value = password.value = confirmation.value = error.value = "";
}
async function signOut() {
  busy.value = true;
  try {
    await logout();
  } catch {
    error.value = "退出未成功，请检查网络后重试。";
  } finally {
    busy.value = false;
  }
}
async function save() {
  if (password.value !== confirmation.value) {
    error.value = "两次新密码不一致。";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await changePassword(current.value, password.value);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "修改失败。";
  } finally {
    busy.value = false;
    current.value = password.value = confirmation.value = "";
  }
}
</script>

<template>
  <div v-if="authState.authenticated" class="admin-menu">
    <span class="admin-name">{{ authState.username }}</span>
    <button type="button" @click="editing = true">修改密码</button>
    <button type="button" :disabled="busy" @click="signOut">退出登录</button>
    <p v-if="error && !editing" role="alert">{{ error }}</p>
    <Teleport to="body">
      <div v-if="editing" class="password-overlay" @click.self="close">
        <section
          role="dialog"
          aria-modal="true"
          aria-labelledby="password-title"
          class="password-dialog"
          @keydown.esc="close"
        >
          <h2 id="password-title">修改后台密码</h2>
          <p>修改后，所有设备都需要重新登录。</p>
          <form @submit.prevent="save">
            <label
              >当前密码<input
                v-model="current"
                type="password"
                autocomplete="current-password"
                required
                maxlength="128"
            /></label>
            <label
              >新密码<input
                v-model="password"
                type="password"
                autocomplete="new-password"
                required
                minlength="4"
                maxlength="128"
            /></label>
            <label
              >确认新密码<input
                v-model="confirmation"
                type="password"
                autocomplete="new-password"
                required
                minlength="4"
                maxlength="128"
            /></label>
            <small>密码长度为4–128个字符。</small>
            <p v-if="error" role="alert" class="password-error">{{ error }}</p>
            <div class="password-actions">
              <button type="button" :disabled="busy" @click="close">取消</button
              ><button type="submit" :disabled="busy">
                {{ busy ? "保存中…" : "保存密码" }}
              </button>
            </div>
          </form>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.admin-menu {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 13px;
}
button {
  font: inherit;
  cursor: pointer;
  border: 1px solid #dce3e9;
  border-radius: 6px;
  padding: 6px 9px;
  background: white;
  color: #334155;
}
button:disabled {
  opacity: 0.6;
}
.admin-name {
  color: #64748b;
}
.password-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  background: #14233466;
  display: grid;
  place-items: center;
  padding: 20px;
}
.password-dialog {
  width: min(100%, 420px);
  box-sizing: border-box;
  padding: 26px;
  border-radius: 16px;
  background: white;
}
.password-dialog h2 {
  margin-top: 0;
}
.password-dialog p,
small {
  color: #64748b;
}
form,
label {
  display: grid;
  gap: 10px;
}
form {
  gap: 16px;
}
input {
  font: inherit;
  padding: 10px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  min-width: 0;
}
.password-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
.password-error {
  color: #b42318 !important;
}
</style>
