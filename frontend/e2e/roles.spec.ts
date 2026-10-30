import { test, expect, type Page } from "@playwright/test";
import { snapshot, syntheticAccount } from "../tests/fixtures";
import { blankCard } from "../src/role-cards";
import { defaultExclusions } from "../src/conversation-options";
import { messagePage } from "../tests/message-fixtures";
import type { RoleCard, ConversationPreferences } from "../src/api";

async function mockWorkspace(page: Page) {
  const roles: RoleCard[] = [];
  const preferences = new Map<string, ConversationPreferences>();
  const state = {
    failToggle: false,
    failLoad: false,
    conflict: false,
    reads: 0,
    writes: [] as Record<string, unknown>[],
    delay: null as Promise<void> | null,
  };
  const accounts = snapshot([
    syntheticAccount,
    {
      ...syntheticAccount,
      account_id: "synthetic-account-2",
      wxid: "wxid_second",
      nickname: "第二账号",
    },
  ]);
  let notify: (event: object) => void = () => {};
  function pref(aid: string, cid: string) {
    const key = aid + ":" + cid;
    if (!preferences.has(key))
      preferences.set(key, {
        account_id: aid,
        conversation_id: cid,
        ai_enabled: false,
        role_card_code: null,
        memory: "",
        memory_revision: 0,
        revision: 0,
      });
    return preferences.get(key)!;
  }
  function count(code: string) {
    return [...preferences.values()].filter((p) => p.role_card_code === code)
      .length;
  }
  await page.routeWebSocket("**/api/v1/events", (socket) => {
    notify = (event) => socket.send(JSON.stringify(event));
    socket.onMessage(() => socket.send(JSON.stringify({ type: "ready" })));
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["content-type"]).toContain(
      "application/json",
    );
    const body = route.request().postDataJSON();
    const fail = (code: string, message: string, status = 409) =>
      route.fulfill({ status, json: { code, message } });
    if (path.endsWith("/api-profiles/query"))
      return route.fulfill({ json: { items: [], presets: [] } });
    if (path.endsWith("/ai-defaults/query"))
      return route.fulfill({
        json: { role_card_code: null, api_profile_id: null, revision: 0 },
      });
    if (path.endsWith("/accounts/query"))
      return route.fulfill({ json: accounts });
    if (path.endsWith("/accounts/get"))
      return route.fulfill({
        json: accounts.accounts.find((a) => a.account_id === body.account_id),
      });
    if (path.endsWith("/conversations/query"))
      return route.fulfill({
        json: {
          account_id: body.account_id,
          source_state: "offline",
          coverage: "complete",
          needs_sync: false,
          revision: 1,
          snapshot_id: "one",
          observed_at: "2026-10-29T01:00:00Z",
          status: "complete",
          total: 2,
          unfiltered_total: 2,
          next_cursor: null,
          applied_exclusions: defaultExclusions(),
          issues: [],
          stale: false,
          items: ["一", "二"].map((suffix, i) => ({
            conversation_id: `${body.account_id}-${i}`,
            name: `合成会话${suffix}`,
            category: "contact",
            is_container: false,
            summary: "新的工作安排",
            last_message_at: "2026-10-29T03:01:00Z",
            unread_count: i ? 0 : 1,
            avatar: null,
            ai_enabled: pref(body.account_id, `${body.account_id}-${i}`)
              .ai_enabled,
            preferences_revision: pref(
              body.account_id,
              `${body.account_id}-${i}`,
            ).revision,
          })),
        },
      });
    if (path.endsWith("/messages/query")) {
      state.reads++;
      return route.fulfill({
        json: {
          ...messagePage(body.account_id, body.conversation_id),
          source_state: "offline",
          needs_sync: false,
        },
      });
    }
    if (path.endsWith("/role-cards/query"))
      return route.fulfill({
        json: {
          items: roles.map(({ card: _, ...r }) => ({
            ...r,
            usage_count: count(r.card_code),
          })),
        },
      });
    if (path.endsWith("/role-cards/get")) {
      const role = roles.find((r) => r.card_code === body.card_code);
      return role
        ? route.fulfill({
            json: { ...role, usage_count: count(role.card_code) },
          })
        : fail("role_card_not_found", "角色卡不存在", 404);
    }
    if (
      path.endsWith("/role-cards/create") ||
      path.endsWith("/role-cards/update")
    ) {
      let role = roles.find((r) => r.card_code === body.card_code);
      if (role && role.revision !== body.expected_revision)
        return fail(
          "revision_conflict",
          "内容已在其他位置更新，请重新加载后再保存。",
        );
      if (!role) {
        role = {
          is_default: false,
          card_code: `RC_${String(roles.length + 1).padStart(32, "0")}`,
          card: body.card,
          name: body.card.data.name,
          revision: 0,
          usage_count: 0,
          created_at: "2026-10-29T01:00:00Z",
          updated_at: "2026-10-29T01:00:00Z",
        };
        roles.push(role);
      }
      role.card = body.card;
      role.name = body.card.data.name;
      role.revision++;
      await route.fulfill({
        status: path.endsWith("create") ? 201 : 200,
        json: role,
      });
      notify({ type: "changed", resources: ["role_cards"] });
      return;
    }
    if (path.endsWith("/role-cards/delete")) {
      if (count(body.card_code))
        return fail(
          "role_card_in_use",
          "角色卡正被会话使用，请先解除全部引用。",
        );
      roles.splice(
        roles.findIndex((r) => r.card_code === body.card_code),
        1,
      );
      return route.fulfill({
        json: { card_code: body.card_code, deleted: true },
      });
    }
    if (path.endsWith("/conversation-preferences/query")) {
      if (state.failLoad) return fail("storage_unavailable", "读取失败", 503);
      const result = structuredClone(
        pref(body.account_id, body.conversation_id),
      );
      if (state.delay) await state.delay;
      return route.fulfill({ json: result });
    }
    if (path.endsWith("/conversation-preferences/update")) {
      state.writes.push(body);
      if (state.failToggle) return fail("storage_unavailable", "保存失败", 503);
      const result = pref(body.account_id, body.conversation_id);
      if (state.conflict || result.revision !== body.expected_revision)
        return fail(
          "revision_conflict",
          "内容已在其他位置更新，请重新加载后再保存。",
        );
      for (const key of ["ai_enabled", "role_card_code", "memory"] as const)
        if (key in body) Object.assign(result, { [key]: body[key] });
      result.revision++;
      await route.fulfill({ json: result });
      notify({
        type: "changed",
        account_id: body.account_id,
        conversation_ids: [body.conversation_id],
        resources: ["conversation_preferences", "conversations"],
      });
      notify({ type: "changed", resources: ["role_cards"] });
      return;
    }
    throw new Error(`Unexpected endpoint ${path}`);
  });
  return {
    roles,
    preferences,
    state,
    pref,
    notify: (event: object) => notify(event),
  };
}

async function addRole(page: Page, name = "工作助手") {
  await page.getByRole("link", { name: "角色卡", exact: true }).click();
  await page.getByRole("button", { name: "新增角色卡", exact: true }).click();
  await page.getByLabel("角色名称 *", { exact: true }).fill(name);
  await page
    .getByLabel("系统提示词", { exact: true })
    .fill("保持简洁，按上下文回答。");
  await page.getByRole("button", { name: "保存角色卡", exact: true }).click();
  await expect(
    page.getByText("角色卡已保存，所有微信账号共用。", { exact: true }),
  ).toBeVisible();
}

test("global V2 library edits and preserves JSON through import/export and account changes", async ({
  page,
}) => {
  const { roles } = await mockWorkspace(page);
  await page.goto("/workspace/synthetic-account-1/conversations");
  await addRole(page);
  const original = blankCard();
  original.data.name = "导入角色";
  original.data.extensions = {
    "vendor/nested": { preserve: [1, false, "value"] },
  };
  original.data.character_book = {
    entries: [
      {
        keys: ["x"],
        content: "保留角色书",
        enabled: true,
        insertion_order: 1,
        extensions: {},
      },
    ],
    extensions: {},
  };
  original.unknown = { keep: true };
  await page.getByLabel("导入角色卡文件").setInputFiles({
    name: "role.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(original)),
  });
  await expect(page.getByLabel("角色名称 *", { exact: true })).toHaveValue(
    "导入角色",
  );
  expect(roles).toHaveLength(1);
  await page.getByRole("button", { name: "高级 JSON", exact: true }).click();
  const raw = page.getByLabel("完整 V2 JSON", { exact: true });
  await raw.fill("{broken");
  await page.getByRole("button", { name: "中文表单", exact: true }).click();
  await expect(raw).toHaveValue("{broken");
  await expect(page.getByRole("alert")).toContainText("JSON 格式不正确");
  await raw.fill(JSON.stringify(original));
  await page.getByRole("button", { name: "中文表单", exact: true }).click();
  await page.getByLabel("角色名称 *", { exact: true }).fill("已编辑角色");
  await page.getByRole("button", { name: "保存角色卡", exact: true }).click();
  await expect(
    page.getByText("角色卡已保存，所有微信账号共用。", { exact: true }),
  ).toBeVisible();
  expect(roles[1]!.card).toEqual({
    ...original,
    data: { ...original.data, name: "已编辑角色" },
  });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 JSON", exact: true }).click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream!) chunks.push(chunk);
  expect(JSON.parse(Buffer.concat(chunks).toString())).toEqual(roles[1]!.card);
  await page.getByRole("button", { name: "切换账号", exact: true }).click();
  await page
    .getByRole("button", { name: "进入工作台", exact: true })
    .nth(1)
    .click();
  await page.getByRole("link", { name: "角色卡", exact: true }).click();
  await expect(page.locator(".role-list-item")).toHaveCount(2);
  await page.getByRole("button", { name: /已编辑角色 RC_/ }).click();
  await expect(page.getByLabel("系统提示词", { exact: true })).toHaveValue("");
  await page.screenshot({
    path: "../.local/roles-acceptance/role-cards-desktop.png",
    fullPage: true,
  });
});

test("per-chat controls preserve memory, block used-card deletion and persist across reload", async ({
  page,
}) => {
  const { state, roles, pref } = await mockWorkspace(page);
  await page.goto("/workspace/synthetic-account-1/conversations");
  await addRole(page);
  await page.getByRole("link", { name: "会话", exact: true }).click();
  await page
    .getByRole("button", { name: "会话设置：合成会话一", exact: true })
    .click();
  expect(state.reads).toBe(0);
  await page.getByRole("combobox", { name: "角色卡", exact: true }).click();
  await page.getByRole("option", { name: /工作助手 · RC_/ }).click();
  const memory = "  人物偏好\n保留第二行\n";
  await page.getByLabel(/记忆 可随时编辑/).fill(memory);
  await page.getByRole("button", { name: "保存会话设置", exact: true }).click();
  await expect(page.getByText("会话设置已保存", { exact: true })).toBeVisible();
  await page
    .locator(".el-switch")
    .filter({
      has: page.getByRole("switch", {
        name: "合成会话一 AI 接入",
        exact: true,
      }),
    })
    .click();
  await expect(
    page.getByRole("switch", { name: "合成会话一 AI 接入", exact: true }),
  ).toBeChecked();
  expect(Object.keys(state.writes.at(-1)!)).toEqual(
    expect.arrayContaining([
      "account_id",
      "conversation_id",
      "expected_revision",
      "ai_enabled",
    ]),
  );
  expect(state.writes.at(-1)).not.toHaveProperty("memory");
  await page
    .locator(".el-switch")
    .filter({
      has: page.getByRole("switch", {
        name: "合成会话一 AI 接入",
        exact: true,
      }),
    })
    .click();
  await expect(
    page.getByRole("switch", { name: "合成会话一 AI 接入", exact: true }),
  ).not.toBeChecked();
  await expect(page.getByLabel(/记忆 可随时编辑/)).toHaveValue(memory);
  await page.screenshot({
    path: "../.local/roles-acceptance/conversation-settings-desktop.png",
    fullPage: true,
  });
  await page.reload();
  await page
    .getByRole("button", { name: "会话设置：合成会话一", exact: true })
    .click();
  await expect(page.getByLabel(/记忆 可随时编辑/)).toHaveValue(memory);
  await page
    .getByRole("button", { name: "会话设置：合成会话二", exact: true })
    .click();
  await expect(page.getByLabel(/记忆 可随时编辑/)).toHaveValue("");
  await page.getByRole("link", { name: "角色卡", exact: true }).click();
  await page.getByRole("button", { name: /工作助手 RC_/ }).click();
  await page.getByRole("button", { name: "删除角色卡", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("1 个会话使用");
  expect(roles).toHaveLength(1);
  expect(pref("synthetic-account-1", "synthetic-account-1-0").memory).toBe(
    memory,
  );
});

test("draft navigation supports cancel, discard and save; failed toggle and stale save retain data", async ({
  page,
}) => {
  const { state, notify, pref } = await mockWorkspace(page);
  await page.goto("/workspace/synthetic-account-1/conversations");
  state.failToggle = true;
  await page
    .locator(".el-switch")
    .filter({
      has: page.getByRole("switch", {
        name: "合成会话一 AI 接入",
        exact: true,
      }),
    })
    .click();
  await expect(page.getByRole("alert")).toContainText("保存失败");
  await expect(
    page.getByRole("switch", { name: "合成会话一 AI 接入", exact: true }),
  ).not.toBeChecked();
  state.failToggle = false;
  await page
    .getByRole("button", { name: "会话设置：合成会话一", exact: true })
    .click();
  await page.getByLabel(/记忆 可随时编辑/).fill("保留草稿");
  notify({
    type: "changed",
    account_id: "synthetic-account-1",
    conversation_ids: ["synthetic-account-1-0"],
    resources: ["conversation_preferences"],
  });
  await expect(page.getByText(/设置已在其他位置变更/)).toBeVisible();
  await expect(page.getByLabel(/记忆 可随时编辑/)).toHaveValue("保留草稿");
  await page
    .getByRole("button", { name: "会话设置：合成会话二", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Close this dialog", exact: true })
    .click();
  await expect(page.getByLabel(/记忆 可随时编辑/)).toHaveValue("保留草稿");
  state.conflict = true;
  await page.getByRole("button", { name: "保存会话设置", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "单会话设置" }).getByRole("alert").first(),
  ).toContainText("重新加载");
  await expect(page.getByLabel(/记忆 可随时编辑/)).toHaveValue("保留草稿");
  state.conflict = false;
  await page
    .getByRole("button", { name: "会话设置：合成会话二", exact: true })
    .click();
  await page.getByRole("button", { name: "保存并离开", exact: true }).click();
  await expect(page.getByLabel(/记忆 可随时编辑/)).toHaveValue("");
  expect(pref("synthetic-account-1", "synthetic-account-1-0").memory).toBe(
    "保留草稿",
  );
  await page.getByLabel(/记忆 可随时编辑/).fill("丢弃草稿");
  await page.getByRole("link", { name: "角色卡", exact: true }).click();
  await page.getByRole("button", { name: "放弃修改", exact: true }).click();
  await expect(page.locator(".role-layout")).toBeVisible();
  expect(pref("synthetic-account-1", "synthetic-account-1-1").memory).toBe("");
});

test("failed initial load cannot overwrite settings; narrow screen stays usable", async ({
  page,
}) => {
  const { state } = await mockWorkspace(page);
  state.failLoad = true;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/workspace/synthetic-account-1/conversations");
  await page
    .getByRole("button", { name: "会话设置：合成会话一", exact: true })
    .click();
  await expect(page.getByRole("region", { name: "单会话设置" })).toContainText(
    "读取失败",
  );
  await expect(
    page.getByRole("button", { name: "保存会话设置", exact: true }),
  ).toHaveCount(0);
  expect(state.writes).toHaveLength(0);
  state.failLoad = false;
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(page.getByLabel(/记忆 可随时编辑/)).toHaveValue("");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.local/roles-acceptance/conversation-settings-mobile.png",
    fullPage: true,
  });
});

test("late settings responses cannot replace the newly selected conversation", async ({
  page,
}) => {
  const { state, pref } = await mockWorkspace(page);
  pref("synthetic-account-1", "synthetic-account-1-0").memory = "第一个会话";
  pref("synthetic-account-1", "synthetic-account-1-1").memory = "第二个会话";
  let release!: () => void;
  state.delay = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.goto("/workspace/synthetic-account-1/conversations");
  const request = page.waitForRequest((r) =>
    r.url().endsWith("/conversation-preferences/query"),
  );
  await page
    .getByRole("button", { name: "会话设置：合成会话一", exact: true })
    .click();
  await request;
  state.delay = null;
  await page
    .getByRole("button", { name: "会话设置：合成会话二", exact: true })
    .click();
  await expect(page.getByLabel(/记忆 可随时编辑/)).toHaveValue("第二个会话");
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/conversation-preferences/query") &&
      r.request().postDataJSON().conversation_id.endsWith("-0"),
  );
  release();
  await response;
  await expect(page.getByLabel(/记忆 可随时编辑/)).toHaveValue("第二个会话");
  await page.getByLabel(/记忆 可随时编辑/).fill("只保存第二个");
  await page.getByRole("button", { name: "保存会话设置", exact: true }).click();
  await expect(page.getByText("会话设置已保存", { exact: true })).toBeVisible();
  expect(pref("synthetic-account-1", "synthetic-account-1-0").memory).toBe(
    "第一个会话",
  );
  expect(pref("synthetic-account-1", "synthetic-account-1-1").memory).toBe(
    "只保存第二个",
  );
});
