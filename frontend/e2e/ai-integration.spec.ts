import { test, expect, type Page } from "@playwright/test";
import { blankCard } from "../src/role-cards";

let ids: { account_id: string; conversation_id: string }[];
const service = "http://127.0.0.1:18765";
let requestHeaders: Record<string, string>;
async function fixturePost(page: Page, url: string, options: { data: object }) {
  return page.request.post(url, { ...options, headers: requestHeaders });
}
test.beforeEach(async ({ context }) => {
  const request = context.request;
  const login = await request.post(service + "/api/v1/auth/login", {
    headers: { Origin: "http://127.0.0.1:15173" },
    data: { username: "admin", password: "synthetic-test-password-2026" },
  });
  expect(login.ok()).toBeTruthy();
  requestHeaders = {
    Origin: "http://127.0.0.1:15173",
    "X-CSRF-Token": (await login.json()).csrf_token,
  };
  ids = await (
    await request.post(service + "/test/reset", {
      data: {},
      headers: requestHeaders,
    })
  ).json();
});
async function post(page: Page, path: string, data: object = {}) {
  const response = await fixturePost(page, service + "/api/v1/" + path, {
    data,
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function addApi(page: Page, name: string, provider = "openai") {
  await page
    .getByRole("button", { name: "新增 API 配置", exact: true })
    .click();
  await page.getByLabel("配置名称 *", { exact: true }).fill(name);
  await page.getByLabel("服务商 *", { exact: true }).selectOption(provider);
  await page.getByLabel("模型名 *", { exact: true }).fill("synthetic-model");
  await page
    .getByLabel("API 密钥 *", { exact: true })
    .fill("synthetic-browser-key");
  await page
    .getByRole("button", { name: "保存 API 配置", exact: true })
    .click();
  await expect(
    page.getByText("API 配置已保存，所有微信账号共用。", { exact: true }),
  ).toBeVisible();
}
async function toggle(page: Page) {
  await page
    .locator(".el-switch")
    .filter({
      has: page.getByRole("switch", {
        name: "合成联系人 AI 接入",
        exact: true,
      }),
    })
    .click();
}

test("uncertain delivery requires a decision and failed delivery retries the existing reply", async ({
  page,
}) => {
  await page.goto(`/workspace/${ids[0]!.account_id}/agents`);
  await addApi(page, "发送测试连接");
  const card = blankCard();
  card.data.name = "发送测试角色";
  await post(page, "role-cards/create", { card });
  await post(page, "conversation-preferences/update", {
    ...ids[0]!,
    expected_revision: 0,
    ai_enabled: true,
  });
  await fixturePost(page, service + "/test/send-result", {
    data: { status: "unknown" },
  });
  await fixturePost(page, service + "/test/message", { data: { index: 1 } });
  await page.goto(`/workspace/${ids[0]!.account_id}/logs`);
  await page
    .getByLabel("会话", { exact: true })
    .selectOption(ids[0]!.conversation_id);
  await page
    .getByRole("button", { name: "处理未处理消息", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "已核对：不确定的段已发出", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "重试发送原回复", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "已核对：不确定的段已发出", exact: true })
    .click();
  await expect(page.locator(".ai-runs")).toContainText("手动确认");
  await fixturePost(page, service + "/test/send-result", {
    data: { status: "failed" },
  });
  await fixturePost(page, service + "/test/message", { data: { index: 2 } });
  await page
    .getByRole("button", { name: "处理未处理消息", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "重试发送原回复", exact: true }),
  ).toBeVisible();
  const before = await post(page, "ai-runs/list", ids[0]!);
  await fixturePost(page, service + "/test/send-result", {
    data: { status: "sent" },
  });
  await page
    .getByRole("button", { name: "重试发送原回复", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "重试发送原回复", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".ai-runs")).toContainText("已发送");
  const after = await post(page, "ai-runs/list", ids[0]!);
  expect(after.items.length).toBe(before.items.length);
});

test("interrupted result polling resumes the same API task", async ({
  page,
}) => {
  await page.goto(`/workspace/${ids[0]!.account_id}/agents`);
  await addApi(page, "查询恢复");
  let calls = 0;
  await page.route("**/api/v1/api-profiles/test", async (route) => {
    calls++;
    await route.continue();
  });
  await page.route(
    "**/api/v1/api-profiles/tasks/query",
    (route) => route.abort(),
    { times: 1 },
  );
  await page.getByRole("button", { name: "测试可用性", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "继续查询测试结果", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("配置名称 *", { exact: true })).toBeDisabled();
  await page
    .getByRole("button", { name: "继续查询测试结果", exact: true })
    .click();
  await expect(
    page.locator(".save-notice").filter({ hasText: "文本与 JSON 测试通过" }),
  ).toBeVisible();
  expect(calls).toBe(1);
});

test("global instructions, history-only memory and live reply delivery", async ({
  page,
}) => {
  await page.goto(`/workspace/${ids[0]!.account_id}/settings`);
  await expect(
    page.getByRole("tab", { name: "会话过滤", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "AI 提示词", exact: true }).click();
  await page
    .getByLabel("全局 AI 提示词", { exact: true })
    .fill("请回复 JSON，合并旧记忆、新消息和本次未发送回复。");
  await page.getByRole("tab", { name: "会话过滤", exact: true }).click();
  await expect(page.getByLabel("全局 AI 提示词", { exact: true })).toBeHidden();
  await page.getByRole("tab", { name: "AI 提示词", exact: true }).click();
  await expect(page.getByLabel("全局 AI 提示词", { exact: true })).toHaveValue(
    "请回复 JSON，合并旧记忆、新消息和本次未发送回复。",
  );
  await page
    .getByRole("button", { name: "保存 AI 提示词", exact: true })
    .click();
  await expect(
    page.getByText("AI 提示词已保存，之后的所有 AI 调用生效。", {
      exact: true,
    }),
  ).toBeVisible();
  await page.goto(`/workspace/${ids[1]!.account_id}/settings`);
  await page.getByRole("tab", { name: "AI 提示词", exact: true }).click();
  await expect(page.getByLabel("全局 AI 提示词", { exact: true })).toHaveValue(
    "请回复 JSON，合并旧记忆、新消息和本次未发送回复。",
  );
  const settings = await post(page, "ai-settings/query");
  await page
    .getByRole("button", { name: "恢复默认提示词", exact: true })
    .click();
  await expect(page.getByLabel("全局 AI 提示词", { exact: true })).toHaveValue(
    settings.default_instructions,
  );
  await page
    .getByRole("button", { name: "保存 AI 提示词", exact: true })
    .click();
  await expect(
    page.getByText("AI 提示词已保存，之后的所有 AI 调用生效。", {
      exact: true,
    }),
  ).toBeVisible();
  await page.goto(`/workspace/${ids[0]!.account_id}/agents`);
  await addApi(page, "生成连接");
  await page.getByRole("button", { name: "测试可用性", exact: true }).click();
  await expect(
    page.locator(".save-notice").filter({ hasText: "文本与 JSON 测试通过" }),
  ).toBeVisible();
  await page.getByLabel("模型名 *", { exact: true }).fill("invalid-model");
  await expect(
    page.getByRole("button", { name: "测试可用性", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "保存 API 配置", exact: true })
    .click();
  await expect(
    page.getByRole("alert").filter({ hasText: "认证失败" }),
  ).toBeVisible();
  expect((await post(page, "api-profiles/query")).items[0].model).toBe(
    "synthetic-model",
  );
  await page.getByLabel("模型名 *", { exact: true }).fill("synthetic-model");
  const role = blankCard();
  role.data.name = "合成角色";
  await post(page, "role-cards/create", { card: role });
  await fixturePost(page, service + "/test/message", {
    data: { index: 1, content: { text: "记住周五开会" } },
  });
  await page.goto(`/workspace/${ids[0]!.account_id}/conversations`);
  await toggle(page);
  await page
    .getByRole("button", { name: /合成联系人/ })
    .first()
    .click();
  await expect(
    page.locator(".ai-runs,.ai-message-state,.ai-reply"),
  ).toHaveCount(0);
  await page.getByRole("link", { name: "日志", exact: true }).click();
  await page
    .getByLabel("会话", { exact: true })
    .selectOption(ids[0]!.conversation_id);
  await page
    .getByRole("button", { name: "处理未处理消息", exact: true })
    .click();
  await expect(page.locator(".ai-runs")).toContainText("无需发送");
  await expect(page.locator(".ai-reply")).toHaveCount(0);
  const pref = await post(page, "conversation-preferences/query", ids[0]!);
  expect(pref.memory).toContain("记住周五开会");
  expect(pref.memory_revision).toBe(1);
  await fixturePost(page, service + "/test/message", {
    data: { index: 2, type: "voice", content: { transcript: null } },
  });
  await expect(
    page.locator(".log-pending-item").filter({ hasText: "等待转写" }),
  ).toHaveCount(1);
  await fixturePost(page, service + "/test/message", {
    data: { index: 2, type: "voice", content: { transcript: "改为周六" } },
  });
  await page
    .getByRole("button", { name: "处理未处理消息", exact: true })
    .click();
  await expect(page.locator(".ai-reply")).toHaveCount(1);
  await expect(page.locator(".ai-runs")).toContainText("已发送");
  const other = await post(page, "conversation-preferences/query", ids[1]!);
  expect(other.memory).toBe("");
  await page.screenshot({
    path: "../.local/ai-config-acceptance/ai-memory-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../.local/ai-config-acceptance/ai-memory-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("log details are lazy, failures can retry, and refresh preserves open JSON", async ({
  page,
  context,
}) => {
  await page.goto(`/workspace/${ids[0]!.account_id}/agents`);
  await addApi(page, "日志测试连接");
  const card = blankCard();
  card.data.name = "日志角色";
  await post(page, "role-cards/create", { card });
  await post(page, "conversation-preferences/update", {
    ...ids[0]!,
    expected_revision: 0,
    ai_enabled: true,
  });
  await fixturePost(page, service + "/test/message", {
    data: { index: 1, content: { text: "日志合成消息" } },
  });
  await fixturePost(page, service + "/test/fail-next", { data: {} });
  let detailRequests = 0;
  await page.route("**/api/v1/logs/get", async (route) => {
    detailRequests++;
    await route.continue();
  });
  await page.goto(`/workspace/${ids[0]!.account_id}/logs`);
  await page
    .getByLabel("会话", { exact: true })
    .selectOption(ids[0]!.conversation_id);
  await page
    .getByRole("button", { name: "处理未处理消息", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "重试并恢复自动处理", exact: true }),
  ).toBeVisible();
  expect(
    (await post(page, "conversation-preferences/query", ids[0]!))
      .memory_revision,
  ).toBe(0);
  await expect(page.locator(".log-entry")).toHaveCount(1);
  expect(detailRequests).toBe(0);
  const failedId = await page
    .locator(".log-entry")
    .first()
    .getAttribute("data-log-id");
  const failed = page.locator(`[data-log-id="${failedId}"]`);
  await failed.locator(":scope > summary").click();
  const raw = failed
    .locator(".log-json")
    .filter({ has: page.locator("summary", { hasText: "非 JSON 响应正文" }) });
  await raw.locator("summary").click();
  await expect(raw.locator("pre")).toContainText(
    "synthetic provider unavailable",
  );
  await page
    .getByRole("button", { name: "重试并恢复自动处理", exact: true })
    .click();
  await expect(page.locator(".ai-reply")).toHaveCount(1);
  await expect(page.locator(".log-entry")).toHaveCount(2);
  await expect(raw.locator("pre")).toBeVisible();
  const complete = page.locator(".log-entry").filter({
    has: page.locator("summary strong", { hasText: "会话调用 · 完成" }),
  });
  await complete.locator(":scope > summary").click();
  const request = complete
    .locator(".log-json")
    .filter({ has: page.locator("summary", { hasText: "实际请求 JSON" }) });
  await request.locator("summary").click();
  await expect(request.locator("pre")).toContainText("日志合成消息");
  await expect(request.locator("pre")).not.toContainText(
    "synthetic-browser-key",
  );
  await request
    .getByRole("button", { name: "复制 实际请求 JSON", exact: true })
    .click();
  await expect(page.getByText(/已复制当前显示内容/)).toBeVisible();
  await page.getByRole("button", { name: "刷新日志", exact: true }).click();
  await expect(request.locator("pre")).toBeVisible();
  await expect(page.getByLabel("会话", { exact: true })).toHaveValue(
    ids[0]!.conversation_id,
  );
  await page.getByLabel("调用状态", { exact: true }).selectOption("failed");
  await expect(page.locator(".log-entry")).toHaveCount(1);
  const other = await context.newPage();
  await other.goto(`/workspace/${ids[1]!.account_id}/logs`);
  await expect(other.locator(".log-entry")).toHaveCount(2); // Global text and image tests only.
  await expect(other.locator(".log-entry")).not.toContainText(["会话调用"]);
  await page.getByLabel("调用状态", { exact: true }).selectOption("");
  await page
    .getByRole("button", { name: "清空当前账号日志", exact: true })
    .click();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator(".log-entry")).toHaveCount(2);
  await page
    .getByRole("button", { name: "清空当前账号日志", exact: true })
    .click();
  await page.getByRole("button", { name: "清空", exact: true }).click();
  await expect(page.locator(".log-entry")).toHaveCount(0);
  expect(
    (await post(page, "conversation-preferences/query", ids[0]!))
      .memory_revision,
  ).toBe(1);
  await expect(page.locator(".ai-reply")).toHaveCount(1);
  await other.close();
});

test("source logs paginate, filter by time and preserve loaded details across updates", async ({
  page,
}) => {
  await fixturePost(page, service + "/test/source-logs", {
    data: { count: 35 },
  });
  await page.goto(`/workspace/${ids[0]!.account_id}/logs`);
  await page.getByRole("tab", { name: "后端读取", exact: true }).click();
  await page
    .getByLabel("会话", { exact: true })
    .selectOption(ids[0]!.conversation_id);
  await expect(page.locator(".log-entry")).toHaveCount(30);
  await page.getByRole("button", { name: "加载更多日志", exact: true }).click();
  await expect(page.locator(".log-entry")).toHaveCount(35);
  const oldest = page.locator(".log-entry").last();
  const id = await oldest.getAttribute("data-log-id");
  const anchored = page.locator(`[data-log-id="${id}"]`);
  await anchored.locator(":scope > summary").click();
  const result = anchored
    .locator(".log-json")
    .filter({ has: page.locator("summary", { hasText: "标准化读取结果" }) });
  await result.locator("summary").click();
  await expect(result.locator("pre")).toContainText("合成读取记录");
  await anchored.scrollIntoViewIfNeeded();
  await fixturePost(page, service + "/test/source-logs", {
    data: { count: 1 },
  });
  await expect(page.locator(".log-entry")).toHaveCount(36);
  await expect(result.locator("pre")).toBeVisible();
  await page.getByLabel("开始时间", { exact: true }).fill("2099-01-01T00:00");
  await expect(page.locator(".log-entry")).toHaveCount(0);
  await page.getByLabel("开始时间", { exact: true }).fill("");
  await expect(page.locator(".log-entry")).toHaveCount(30);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.local/logs-acceptance/logs-mobile.png",
    fullPage: true,
  });
});

test("real CRUD uses encrypted keys, preserves omitted keys, and shares across accounts", async ({
  page,
}) => {
  await page.goto(`/workspace/${ids[0]!.account_id}/agents`);
  await addApi(page, "第一连接");
  await expect(
    page.getByRole("button", { name: "默认 API", exact: true }),
  ).toBeDisabled();
  const key = page.getByLabel("API 密钥（留空保留原密钥）", { exact: true });
  await expect(key).toHaveValue("");
  const profiles = await post(page, "api-profiles/query");
  expect(JSON.stringify(profiles)).not.toContain("synthetic-browser-key");
  await page.getByLabel("配置名称 *", { exact: true }).fill("改名连接");
  await page
    .getByRole("button", { name: "保存 API 配置", exact: true })
    .click();
  await expect(
    page.getByText("API 配置已保存，所有微信账号共用。", { exact: true }),
  ).toBeVisible();
  expect((await post(page, "api-profiles/query")).items[0].key_readable).toBe(
    true,
  );
  await key.fill("synthetic-rotated-key");
  await page
    .getByRole("button", { name: "保存 API 配置", exact: true })
    .click();
  await expect(key).toHaveValue("");
  await page.goto(`/workspace/${ids[1]!.account_id}/agents`);
  await page.locator(".role-list-item").filter({ hasText: "改名连接" }).click();
  await expect(page.getByLabel("配置名称 *", { exact: true })).toHaveValue(
    "改名连接",
  );
  await page.reload();
  await page.locator(".role-list-item").filter({ hasText: "改名连接" }).click();
  await expect(key).toHaveValue("");
  await page
    .getByRole("button", { name: "删除 API 配置", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("全局默认 API 将被清空");
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.locator(".role-list-item")).toHaveCount(0);
  expect((await post(page, "ai-defaults/query")).api_profile_id).toBeNull();
});

test("provider presets support protocol switches, editable models, validation and narrow layout", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/workspace/${ids[0]!.account_id}/agents`);
  await addApi(page, "Gemini 连接", "gemini");
  await page
    .getByLabel("接口协议 *", { exact: true })
    .selectOption("openai_chat_completions");
  await expect(page.getByLabel("API 基础地址 *", { exact: true })).toHaveValue(
    "https://generativelanguage.googleapis.com/v1beta/openai",
  );
  await page
    .getByLabel("模型名 *", { exact: true })
    .fill("future-gemini-model");
  await page
    .getByRole("button", { name: "保存 API 配置", exact: true })
    .click();
  await expect(
    page.getByText("API 配置已保存，所有微信账号共用。", { exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("API 基础地址 *", { exact: true })
    .fill("https://example.test/v1?key=synthetic");
  await page
    .getByRole("button", { name: "保存 API 配置", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("请求格式不正确");
  expect(
    (await post(page, "api-profiles/query")).items[0].base_url,
  ).not.toContain("example.test");
  await page
    .getByLabel("API 基础地址 *", { exact: true })
    .fill("https://generativelanguage.googleapis.com/v1beta/openai");
  await page
    .getByRole("button", { name: "保存 API 配置", exact: true })
    .click();
  await expect(
    page.getByText("API 配置已保存，所有微信账号共用。", { exact: true }),
  ).toBeVisible();
  for (const provider of ["anthropic", "grok", "deepseek", "custom"]) {
    await page
      .getByRole("button", { name: "新增 API 配置", exact: true })
      .click();
    await page.getByLabel("服务商 *", { exact: true }).selectOption(provider);
    if (provider === "anthropic")
      await expect(
        page.getByLabel("Anthropic API 版本 *", { exact: true }),
      ).toHaveValue("2026-10-21");
    if (provider === "custom") {
      await expect(
        page.getByLabel("接口协议 *", { exact: true }).locator("option"),
      ).toHaveCount(4);
      await page
        .getByLabel("API 基础地址 *", { exact: true })
        .fill("http://127.0.0.1:9999/v1");
    }
    await page.getByLabel("配置名称 *", { exact: true }).fill(provider);
    await page.getByLabel("模型名 *", { exact: true }).fill("custom-model-id");
    await page.getByLabel("API 密钥 *", { exact: true }).fill("synthetic-only");
    await page
      .getByRole("button", { name: "保存 API 配置", exact: true })
      .click();
    await expect(
      page.getByText("API 配置已保存，所有微信账号共用。", { exact: true }),
    ).toBeVisible();
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.local/ai-config-acceptance/api-mobile.png",
    fullPage: true,
  });
});

test("default inheritance and deletion protection work across two open account pages", async ({
  page,
  context,
}) => {
  const card = blankCard();
  card.data.name = "默认角色";
  const first = await post(page, "role-cards/create", { card });
  await page.goto(`/workspace/${ids[0]!.account_id}/agents`);
  await addApi(page, "默认连接");
  const one = (await post(page, "api-profiles/query")).items[0];
  const other = await context.newPage();
  await other.goto(`/workspace/${ids[1]!.account_id}/conversations`);
  await other
    .getByRole("button", { name: "会话设置：合成联系人", exact: true })
    .click();
  await other.getByRole("combobox", { name: "API 配置", exact: true }).click();
  await expect(
    other.getByRole("option", { name: "使用全局默认 · 默认连接", exact: true }),
  ).toBeVisible();
  await other
    .getByRole("combobox", { name: "API 配置", exact: true })
    .press("Escape");
  await toggle(other);
  await expect(other.getByRole("switch")).toBeChecked();
  await other.getByLabel(/记忆 可随时编辑/).fill("保留草稿");
  await expect(page.locator(".role-list-item")).toContainText("1 个会话使用");
  await page
    .getByRole("button", { name: "删除 API 配置", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("1 个会话使用");
  await addApi(page, "第二连接", "deepseek");
  await page.getByRole("button", { name: "设为默认 API", exact: true }).click();
  await other.getByRole("combobox", { name: "API 配置", exact: true }).click();
  await expect(
    other.getByRole("option", { name: "使用全局默认 · 第二连接", exact: true }),
  ).toBeVisible();
  await other
    .getByRole("combobox", { name: "API 配置", exact: true })
    .press("Escape");
  await expect(other.getByLabel(/记忆 可随时编辑/)).toHaveValue("保留草稿");
  await other.getByRole("combobox", { name: "API 配置", exact: true }).click();
  await other
    .getByRole("option", { name: "默认连接 · synthetic-model", exact: true })
    .click();
  await other
    .getByRole("button", { name: "保存会话设置", exact: true })
    .click();
  await expect(
    other.getByText("会话设置已保存", { exact: true }),
  ).toBeVisible();
  const pref = await post(page, "conversation-preferences/query", ids[1]!);
  expect(pref.role_card_code).toBeNull();
  expect(pref.api_profile_id).toBe(one.profile_id);
  expect(pref.memory).toBe("保留草稿");
  card.data.name = "第二角色";
  const second = await post(page, "role-cards/create", { card });
  await page.getByRole("link", { name: "角色卡", exact: true }).click();
  await page.locator(".role-list-item").filter({ hasText: "第二角色" }).click();
  await page
    .getByRole("button", { name: "设为默认角色卡", exact: true })
    .click();
  await other.getByRole("combobox", { name: "角色卡", exact: true }).click();
  await expect(
    other.getByRole("option", { name: "使用全局默认 · 第二角色", exact: true }),
  ).toBeVisible();
  await other
    .getByRole("combobox", { name: "角色卡", exact: true })
    .press("Escape");
  expect(
    (await post(page, "conversation-preferences/query", ids[1]!)).effective
      .api_profile_id,
  ).toBe(one.profile_id);
  expect((await post(page, "ai-defaults/query")).role_card_code).toBe(
    second.card_code,
  );
  await expect(
    page.locator(".role-list-item").filter({ hasText: first.card_code }),
  ).toContainText("0 个会话使用");
  await other.close();
});

test("missing defaults leave switch off with actionable links; explicit bindings and memory persist", async ({
  page,
}) => {
  await page.goto(`/workspace/${ids[0]!.account_id}/conversations`);
  await toggle(page);
  await expect(page.getByRole("switch")).not.toBeChecked();
  await expect(page.getByRole("alert")).toContainText("默认角色卡");
  await expect(
    page.getByRole("link", { name: "管理 API 配置", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "会话设置：合成联系人", exact: true })
    .click();
  await page.getByLabel(/记忆 可随时编辑/).fill("AI 关闭也保留");
  await page.getByRole("button", { name: "保存会话设置", exact: true }).click();
  await expect(page.getByText("会话设置已保存", { exact: true })).toBeVisible();
  await page.reload();
  await page
    .getByRole("button", { name: "会话设置：合成联系人", exact: true })
    .click();
  await expect(page.getByLabel(/记忆 可随时编辑/)).toHaveValue("AI 关闭也保留");
  expect(
    (await post(page, "conversation-preferences/query", ids[1]!)).memory,
  ).toBe("");
});

test("API drafts survive external edits, failed saves and navigation prompts", async ({
  page,
}) => {
  await page.goto(`/workspace/${ids[0]!.account_id}/agents`);
  await addApi(page, "草稿测试");
  await page.getByLabel("模型名 *", { exact: true }).fill("unsaved-model");
  await page
    .getByLabel("API 密钥（留空保留原密钥）", { exact: true })
    .fill("synthetic-unsaved-key");
  const saved = (await post(page, "api-profiles/query")).items[0];
  const {
    is_default,
    usage_count,
    has_key,
    key_readable,
    verification,
    revision,
    created_at,
    updated_at,
    ...fields
  } = saved;
  await post(page, "api-profiles/update", {
    ...fields,
    name: "其他页面改名",
    expected_revision: revision,
  });
  await expect(page.getByText(/配置已在其他位置变更/)).toBeVisible();
  await page
    .getByRole("button", { name: "保存 API 配置", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("重新加载");
  await expect(page.getByLabel("模型名 *", { exact: true })).toHaveValue(
    "unsaved-model",
  );
  await page.getByRole("link", { name: "角色卡", exact: true }).click();
  await page
    .getByRole("button", { name: "Close this dialog", exact: true })
    .click();
  await expect(page.getByLabel("模型名 *", { exact: true })).toHaveValue(
    "unsaved-model",
  );
  await page.getByRole("link", { name: "角色卡", exact: true }).click();
  await page.getByRole("button", { name: "放弃修改", exact: true }).click();
  await page.getByRole("link", { name: "Agent", exact: true }).click();
  await page.locator(".role-list-item").click();
  await expect(
    page.getByLabel("API 密钥（留空保留原密钥）", { exact: true }),
  ).toHaveValue("");
  await page.getByLabel("模型名 *", { exact: true }).fill("saved-on-leave");
  await page.getByRole("link", { name: "角色卡", exact: true }).click();
  await page.getByRole("button", { name: "保存并离开", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "角色卡", exact: true }),
  ).toBeVisible();
  expect((await post(page, "api-profiles/query")).items[0].model).toBe(
    "saved-on-leave",
  );
  expect(
    await page.evaluate(
      () => JSON.stringify(localStorage) + JSON.stringify(sessionStorage),
    ),
  ).not.toContain("synthetic-unsaved-key");
});
