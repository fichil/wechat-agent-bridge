import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AudioPreview from "../src/components/AudioPreview.vue";
import RoleVoiceSettings from "../src/components/RoleVoiceSettings.vue";
import { api, type ApiProfile } from "../src/api";

vi.mock("../src/api", () => ({ api: { voicePreview: vi.fn() } }));
const global = {
  stubs: { ElButton: { template: "<button><slot /></button>" } },
};
const audio = {
  mime_type: "audio/wav",
  data_base64: "UklGRg==",
  duration_ms: 1000,
};
const profile = {
  profile_id: "API_test",
  revision: 2,
  name: "测试 API",
  model: "chat",
  speech_model: "speech",
  protocol: "openai_responses",
  key_readable: true,
  verification: {
    status: "passed",
    speech: { status: "supported", default_voice: "alloy" },
  },
} as ApiProfile;

describe("语音播放与角色音色", () => {
  beforeEach(() => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:audio"),
      revokeObjectURL: vi.fn(),
    });
    vi.mocked(api.voicePreview).mockResolvedValue(
      audio as Awaited<ReturnType<typeof api.voicePreview>>,
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("loads on demand and releases playback when switching conversation", async () => {
    const fetcher = vi.fn().mockResolvedValue(audio);
    const wrapper = mount(AudioPreview, {
      props: { identity: "run:1", fetcher },
      global,
    });
    expect(fetcher).not.toHaveBeenCalled();
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(wrapper.get("audio").attributes("controls")).toBeDefined();
    expect(wrapper.get("audio").attributes("autoplay")).toBeUndefined();
    await wrapper.setProps({ identity: "other:1" });
    expect(wrapper.find("audio").exists()).toBe(false);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:audio");
    wrapper.unmount();
  });

  it("discards late audio instead of displaying it in another reply", async () => {
    let resolve!: (value: typeof audio) => void;
    const wrapper = mount(AudioPreview, {
      props: {
        identity: "first",
        fetcher: () =>
          new Promise((r) => {
            resolve = r;
          }),
      },
      global,
    });
    await wrapper.get("button").trigger("click");
    await wrapper.setProps({ identity: "second" });
    resolve(audio);
    await flushPromises();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("preserves unrelated extensions and previews the selected profile and voice", async () => {
    const extensions = {
      unknown: { preserved: true },
      wechat_agent_bridge: { future: 123, voices: { API_other: "Kore" } },
    };
    const wrapper = mount(RoleVoiceSettings, {
      props: { modelValue: extensions, profiles: [profile] },
      global,
    });
    await wrapper.get("input").setValue("nova");
    const updated = wrapper.emitted("update:modelValue")![0]![0] as Record<
      string,
      unknown
    >;
    expect(updated).toEqual({
      ...extensions,
      wechat_agent_bridge: {
        future: 123,
        voices: { API_other: "Kore", API_test: "nova" },
      },
    });
    await wrapper.setProps({ modelValue: updated });
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(api.voicePreview).toHaveBeenCalledWith("API_test", 2, "nova");
    expect(wrapper.get("audio").attributes("autoplay")).toBeUndefined();
    await wrapper.get("input").setValue("");
    const cleared = wrapper.emitted("update:modelValue")![1]![0] as Record<
      string,
      unknown
    >;
    await wrapper.setProps({ modelValue: cleared });
    expect(wrapper.find("audio").exists()).toBe(false);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:audio");
    wrapper.unmount();
  });
});
