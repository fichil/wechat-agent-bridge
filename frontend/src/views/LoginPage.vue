<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { authState, ensureSession, login, safeReturn } from "../auth";
const route = useRoute();
const router = useRouter();
const username = ref("admin");
const password = ref("");
const error = ref("");
const busy = ref(false);
onMounted(async () => {
  try {
    await ensureSession();
  } catch {
    error.value = "无法连接后台，请检查网络后重试。";
  }
});
async function submit() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await login(username.value, password.value);
    password.value = "";
    await router.replace(safeReturn(route.query.next));
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "登录失败，请稍后重试。";
    password.value = "";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card" aria-labelledby="login-title">
      <img src="/favicon.svg" width="48" height="48" alt="" />
      <p class="eyebrow">WECHAT AGENT BRIDGE</p>
      <h1 id="login-title">登录微信工作台</h1>
      <p class="login-description">管理你的微信账号、会话与 AI 助理</p>
      <div v-if="!authState.initialized" role="status" class="login-notice">
        管理员尚未初始化，请在服务器终端运行
        <code>python -m backend.admin init</code> 后刷新页面。
      </div>
      <form v-else @submit.prevent="submit">
        <label for="login-username">后台用户名</label>
        <input
          id="login-username"
          v-model="username"
          autocomplete="username"
          maxlength="64"
          required
          :disabled="busy"
        />
        <label for="login-password">密码</label>
        <input
          id="login-password"
          v-model="password"
          type="password"
          autocomplete="current-password"
          maxlength="128"
          required
          :disabled="busy"
        />
        <p v-if="error" role="alert" class="auth-error">{{ error }}</p>
        <button class="auth-primary" :disabled="busy" type="submit">
          {{ busy ? "正在登录…" : "登录" }}
        </button>
        <p class="login-hint">登录状态保留 7 天 · 此密码与微信密码无关</p>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  min-height: 90vh;
  display: grid;
  place-items: center;
  padding: 24px;
}
.login-card {
  width: min(100%, 440px);
  padding: 36px;
  border: 1px solid #e3e8ed;
  border-radius: 20px;
  background: white;
  box-shadow: 0 18px 60px #17324c0a;
}
h1 {
  margin: 12px 0;
  font-size: 26px;
}
.login-description,
.login-hint {
  color: #64748b;
  line-height: 1.7;
}
form {
  display: grid;
  gap: 12px;
  margin-top: 24px;
}
input {
  min-width: 0;
  width: 100%;
  box-sizing: border-box;
  padding: 12px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  font: inherit;
}
input:focus {
  outline: 2px solid #b8dccc;
  border-color: #25866a;
}
.auth-primary {
  border: 0;
  padding: 13px;
  border-radius: 8px;
  background: #237c64;
  color: white;
  font: inherit;
  cursor: pointer;
  margin-top: 8px;
}
button:disabled {
  opacity: 0.65;
  cursor: wait;
}
.auth-error {
  color: #b42318;
  margin: 0;
}
.login-hint {
  margin: 3px 0 0;
  font-size: 13px;
}
.login-notice {
  line-height: 1.8;
  overflow-wrap: anywhere;
  padding: 16px;
  background: #f4f7fa;
  border-radius: 8px;
}
@media (max-width: 520px) {
  .login-page {
    padding: 16px;
  }
  .login-card {
    padding: 26px 20px;
  }
}
</style>
