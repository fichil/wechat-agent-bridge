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
  mime_type: "audio/wav" as const,
  data_base64: "UklGRg==",
  duration_ms: 1000,
};
const profile = {
  profile_id: "API_test",
  revision: 2,
  name: "测试 API",
  model: "chat",
  speech_model: "gpt-4o-mini-tts",
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
    expect(api.voicePreview).toHaveBeenCalledWith(
      "API_test",
      2,
      "nova",
      "",
      "",
    );
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

  it("keeps style bindings separate and previews the unsaved style and sample text", async () => {
    const extensions = {
      unknown: { keep: true },
      wechat_agent_bridge: {
        voices: { API_test: "marin" },
        voice_instructions: { API_other: "Keep this style" },
        future: 123,
      },
    };
    const wrapper = mount(RoleVoiceSettings, {
      props: { modelValue: extensions, profiles: [profile] },
      global,
    });
    await wrapper
      .get('[aria-label="测试 API 说话风格"]')
      .setValue("清亮女声\n短句利落");
    const updated = wrapper.emitted(
      "update:modelValue",
    )![0]![0] as typeof extensions;
    expect(updated).toEqual({
      ...extensions,
      wechat_agent_bridge: {
        ...extensions.wechat_agent_bridge,
        voice_instructions: {
          API_other: "Keep this style",
          API_test: "清亮女声\n短句利落",
        },
      },
    });
    await wrapper.setProps({ modelValue: updated });
    await wrapper
      .get('[aria-label="试听文案"]')
      .setValue("I'm Finn! Let's float here for a bit.");
    expect(wrapper.emitted("update:modelValue")).toHaveLength(1);
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(api.voicePreview).toHaveBeenCalledWith(
      "API_test",
      2,
      "marin",
      "清亮女声\n短句利落",
      "I'm Finn! Let's float here for a bit.",
    );
    expect(wrapper.find("audio").exists()).toBe(true);
    await wrapper.get('[aria-label="试听文案"]').setValue("新文案");
    expect(wrapper.find("audio").exists()).toBe(false);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:audio");
    wrapper.unmount();
  });

  it("does not silently ignore a saved style on an unsupported model", async () => {
    const extensions = {
      wechat_agent_bridge: {
        voices: { API_test: "marin" },
        voice_instructions: { API_test: "原有风格", API_other: "保留" },
      },
    };
    const wrapper = mount(RoleVoiceSettings, {
      props: {
        modelValue: extensions,
        profiles: [{ ...profile, speech_model: "tts-1" }],
      },
      global,
    });
    expect(wrapper.text()).toContain("当前模型不支持已保存的说话风格");
    expect(wrapper.get("button").attributes("disabled")).toBeDefined();
    await wrapper.get('[aria-label="测试 API 说话风格"]').setValue("");
    const cleared = wrapper.emitted(
      "update:modelValue",
    )![0]![0] as typeof extensions;
    expect(cleared.wechat_agent_bridge.voice_instructions).toEqual({
      API_other: "保留",
    });
    expect(cleared.wechat_agent_bridge.voices).toEqual({ API_test: "marin" });
    await wrapper.setProps({ modelValue: cleared });
    expect(wrapper.find('[aria-label="测试 API 说话风格"]').exists()).toBe(
      false,
    );
    expect(wrapper.get("button").attributes("disabled")).toBeUndefined();
    expect(api.voicePreview).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("discards a late preview after its role style changes", async () => {
    let resolve!: (value: typeof audio) => void;
    vi.mocked(api.voicePreview).mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const wrapper = mount(RoleVoiceSettings, {
      props: {
        modelValue: {
          wechat_agent_bridge: { voice_instructions: { API_test: "旧风格" } },
        },
        profiles: [profile],
      },
      global,
    });
    await wrapper.get("button").trigger("click");
    await wrapper.setProps({
      modelValue: {
        wechat_agent_bridge: { voice_instructions: { API_test: "新风格" } },
      },
    });
    resolve(audio);
    await flushPromises();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(wrapper.find("audio").exists()).toBe(false);
    wrapper.unmount();
  });
});
