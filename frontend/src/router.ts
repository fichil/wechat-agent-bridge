import { createRouter, createWebHistory } from "vue-router";
import AccountsPage from "./views/AccountsPage.vue";
import WorkspacePage from "./views/WorkspacePage.vue";
import LoginPage from "./views/LoginPage.vue";
import { authState, ensureSession, safeReturn } from "./auth";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: LoginPage },
    { path: "/", redirect: "/accounts" },
    { path: "/accounts", component: AccountsPage },
    {
      path: "/workspace/:accountId/:section(overview|conversations|role-cards|agents|settings|logs)?",
      component: WorkspacePage,
    },
    { path: "/:pathMatch(.*)*", redirect: "/accounts" },
  ],
});

router.beforeEach(async (to) => {
  try {
    await ensureSession();
  } catch {
    /* Login page offers a retry. */
  }
  if (!authState.authenticated && to.path !== "/login")
    return { path: "/login", query: { next: safeReturn(to.fullPath) } };
  if (authState.authenticated && to.path === "/login")
    return safeReturn(to.query.next);
});
