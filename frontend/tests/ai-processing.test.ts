import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import AiProcessingDetails from "../src/components/AiProcessingDetails.vue";

describe("回复阶段耗时", () => {
  it("shows persisted role creation and later updates without exposing patch JSON", async () => {
    const change = {
      action: "created" as const,
      card_code: "RC_synthetic",
      name: "小鲸鱼 - 产品讨论群",
      revision: 1,
      changed_fields: ["personality"],
    };
    const wrapper = mount(AiProcessingDetails, {
      props: { roleCardChange: change },
    });
    expect(wrapper.get('[aria-label="角色卡修改结果"]').text()).toContain(
      "已创建并绑定角色卡",
    );
    expect(wrapper.text()).toContain(change.name);
    await wrapper.setProps({
      roleCardChange: { ...change, action: "updated", revision: 2 },
    });
    expect(wrapper.text()).toContain("已更新角色卡");
    expect(wrapper.text()).toContain("版本 2");
    expect(wrapper.text()).not.toContain("personality");
  });

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
