import { ref, onScopeDispose } from "vue";
import { defineStore } from "pinia";
import {
  api,
  type ApiProfile,
  type AiDefaults,
  type Preset,
  type DefaultsUpdate,
} from "../api";
import { onChange } from "../api/events";
import { settingsError } from "../role-cards";

export const useAiConfigStore = defineStore("ai-config", () => {
  const items = ref<ApiProfile[]>([]);
  const presets = ref<Preset[]>([]);
  const defaults = ref<AiDefaults | null>(null);
  const loading = ref(false);
  const error = ref("");
  let generation = 0;
  async function load() {
    const ticket = ++generation;
    loading.value = true;
    try {
      const [profiles, selected] = await Promise.all([
        api.apiProfiles(),
        api.aiDefaults(),
      ]);
      if (ticket !== generation) return;
      items.value = profiles.items;
      presets.value = profiles.presets;
      defaults.value = selected;
      error.value = "";
    } catch (cause) {
      if (ticket === generation) error.value = settingsError(cause);
    } finally {
      if (ticket === generation) loading.value = false;
    }
  }
  async function setDefault(patch: Omit<DefaultsUpdate, "expected_revision">) {
    if (!defaults.value || error.value)
      throw new Error("请先重新加载全局配置。");
    const saved = await api.saveAiDefaults({
      ...patch,
      expected_revision: defaults.value.revision,
    });
    if (!defaults.value || saved.revision >= defaults.value.revision)
      defaults.value = saved;
    await load();
  }
  onScopeDispose(
    onChange((event) => {
      if (
        event.type === "ready" ||
        event.type === "resync" ||
        event.resources?.some((r) =>
          ["api_profiles", "ai_defaults"].includes(r),
        )
      )
        void load();
    }),
  );
  return { items, presets, defaults, loading, error, load, setDefault };
});
