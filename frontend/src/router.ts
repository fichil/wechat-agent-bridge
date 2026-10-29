import { createRouter, createWebHistory } from "vue-router";
import AccountsPage from "./views/AccountsPage.vue";
import WorkspacePage from "./views/WorkspacePage.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/accounts" },
    { path: "/accounts", component: AccountsPage },
    {
      path: "/workspace/:accountId/:section(overview|conversations|role-cards|agents|settings)?",
      component: WorkspacePage,
    },
    { path: "/:pathMatch(.*)*", redirect: "/accounts" },
  ],
});
