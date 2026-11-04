import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import AiProcessingDetails from "../src/components/AiProcessingDetails.vue";

describe("回复阶段耗时", () => {
  it("shows reply priority and independent measured stages", () => {
    const wrapper = mount(AiProcessingDetails, {
      props: {
        processingType: "realtime_reply",
        timings: {
          received_to_request: 1600,
          model_request: 77000,
          generated_to_sent: 4200,
        },
      },
    });
    expect(wrapper.text()).toContain("实时回复");
    expect(wrapper.text()).toContain("接收至请求：1.6 秒");
    expect(wrapper.text()).toContain("模型调用：77.0 秒");
    expect(wrapper.text()).toContain("生成至发送：4.2 秒");
  });

  it("does not invent timings for legacy or unfinished deliveries", () => {
    expect(mount(AiProcessingDetails).text()).toBe("");
    const wrapper = mount(AiProcessingDetails, {
      props: {
        processingType: "memory_backfill",
        timings: { model_request: 2500 },
      },
    });
    expect(wrapper.text()).toContain("后台补记忆");
    expect(wrapper.text()).toContain("生成至发送：未记录");
  });
});
