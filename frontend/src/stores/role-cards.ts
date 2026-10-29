import { onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";
import { api, type CardSummary } from "../api";
import { onChange } from "../api/events";
import { settingsError } from "../role-cards";

export const useRoleCardsStore = defineStore("role-cards", () => {
  const items = ref<CardSummary[]>([]);
  const loading = ref(false);
  const error = ref("");
  let generation = 0;
  async function load() {
    const ticket = ++generation;
    loading.value = true;
    try {
      const result = await api.roleCards();
      if (ticket === generation) {
        items.value = result.items;
        error.value = "";
      }
    } catch (cause) {
      if (ticket === generation) error.value = settingsError(cause);
    } finally {
      if (ticket === generation) loading.value = false;
    }
  }
  onScopeDispose(
    onChange((event) => {
      if (
        event.type === "ready" ||
        event.type === "resync" ||
        event.resources?.includes("role_cards")
      )
        void load();
    }),
  );
  return { items, loading, error, load };
});
