import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AudioPreview from "../src/components/AudioPreview.vue";
import RoleVoiceSettings from "../src/components/RoleVoiceSettings.vue";
import BailianVoiceSettings from "../src/components/BailianVoiceSettings.vue";
import { api, type ApiProfile } from "../src/api";

vi.mock("../src/api", () => ({
  api: {
    voicePreview: vi.fn(),
    voiceService: vi.fn(),
    updateVoiceService: vi.fn(),
    previewVoiceService: vi.fn(),
  },
}));
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
const service = {
  service_id: "bailian" as const,
  revision: 1,
  workspace_id: "test-workspace",
  model: "cosyvoice-v3-flash" as const,
  has_key: true,
  key_readable: true,
  verification: { status: "supported" as const },
};

describe("语音播放与角色音色", () => {
  beforeEach(() => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:audio"),
      revokeObjectURL: vi.fn(),
    });
    vi.mocked(api.voicePreview).mockResolvedValue(
      audio as Awaited<ReturnType<typeof api.voicePreview>>,
    );
    vi.mocked(api.voiceService).mockResolvedValue(service);
    vi.mocked(api.updateVoiceService).mockResolvedValue({
      ...service,
      revision: 2,
    });
    vi.mocked(api.previewVoiceService).mockResolvedValue(audio);
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

  it("switches to independent child speech while preserving existing API voices", async () => {
    const original = {
      unknown: true,
      wechat_agent_bridge: {
        voices: { API_test: "cedar" },
        voice_instructions: { API_test: "Warm American voice" },
      },
    };
    const wrapper = mount(RoleVoiceSettings, {
      props: { modelValue: original, profiles: [profile] },
      global,
    });
    expect(api.voiceService).not.toHaveBeenCalled();
    await wrapper.get('[aria-label="语音来源"]').setValue("bailian");
    const updated = wrapper.emitted("update:modelValue")![0]![0] as Record<
      string,
      unknown
    >;
    expect(updated).toMatchObject(original);
    await wrapper.setProps({ modelValue: updated });
    await flushPromises();
    expect(wrapper.find('[aria-label="测试 API 音色"]').exists()).toBe(false);
    await wrapper
      .get('[aria-label="试听文案"]')
      .setValue("才不是大肥鱼，是小鲸鱼。");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "试听百炼音色")!
      .trigger("click");
    await flushPromises();
    expect(api.previewVoiceService).toHaveBeenCalledWith({
      service_id: "bailian",
      expected_revision: 1,
      voice: "longhuhu_v3",
      instructions: "你正在进行自由对话，你说话的情感是neutral。",
      text: "才不是大肥鱼，是小鲸鱼。",
    });
    expect(api.voicePreview).not.toHaveBeenCalled();
    await wrapper.get('[aria-label="语音来源"]').setValue("api");
    expect(wrapper.emitted("update:modelValue")![1]![0]).toEqual(original);
    wrapper.unmount();
  });

  it("saves the shared key outside role settings and keeps it blank afterwards", async () => {
    const wrapper = mount(BailianVoiceSettings, {
      props: {
        binding: { service_id: "bailian", voice: "longhuhu_v3" },
        text: "",
      },
      global,
    });
    await flushPromises();
    const save = () =>
      wrapper
        .findAll("button")
        .find((b) => b.text() === "保存并测试百炼")!
        .trigger("click");
    await wrapper
      .get('[aria-label="百炼 API Key"]')
      .setValue("test-only-secret");
    await save();
    await flushPromises();
    expect(api.updateVoiceService).toHaveBeenLastCalledWith({
      expected_revision: 1,
      workspace_id: "test-workspace",
      api_key: "test-only-secret",
    });
    expect(
      (wrapper.get('[aria-label="百炼 API Key"]').element as HTMLInputElement)
        .value,
    ).toBe("");
    expect(wrapper.emitted("update")).toBeUndefined();
    await save();
    await flushPromises();
    expect(api.updateVoiceService).toHaveBeenLastCalledWith({
      expected_revision: 2,
      workspace_id: "test-workspace",
    });
    wrapper.unmount();
  });

  it("discards independent preview audio after the line changes", async () => {
    let resolve!: (value: typeof audio) => void;
    vi.mocked(api.previewVoiceService).mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const wrapper = mount(BailianVoiceSettings, {
      props: {
        binding: { service_id: "bailian", voice: "longhuhu_v3" },
        text: "旧文案",
      },
      global,
    });
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "试听百炼音色")!
      .trigger("click");
    await wrapper.setProps({ text: "新文案" });
    resolve(audio);
    await flushPromises();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(wrapper.find("audio").exists()).toBe(false);
    wrapper.unmount();
  });
});
