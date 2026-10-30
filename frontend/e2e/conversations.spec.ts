import { mockAuthenticatedSession } from "./auth-fixture";
import { test, expect, type Page } from "@playwright/test";
import { snapshot, syntheticAccount, task } from "../tests/fixtures";
import { defaultExclusions } from "../src/conversation-options";
import type { Conversation, Exclusions } from "../src/api";
import { message, messagePage, messageTask } from "../tests/message-fixtures";
import openapi from "../../backend/openapi.json" with { type: "json" };

const conversationQueryFields = new Set(
  Object.keys(openapi.components.schemas.ConversationQuery.properties),
);

async function mockConversations(page: Page) {
  const settings: Record<string, Exclusions> = {};
  const state = {
    refreshes: 0,
    historyReads: 0,
    extraMessage: false,
    fail: false,
    empty: false,
    allFiltered: false,
    imageAvailable: false,
    historyFail: false,
    stickers: false,
    mediaReads: 0,
  };
  const messageTasks = new Map();
  const second = {
    ...syntheticAccount,
    account_id: "synthetic-account-2",
    wxid: "wxid_synthetic_other",
    nickname: "合成第二账号",
  };
  const accounts = snapshot([syntheticAccount, second]);
  const categories: Conversation["category"][] = [
    "contact",
    "group",
    "wechat_pay",
    "service_accounts",
    "official_accounts",
    "folded_chats",
    "enterprise_contacts",
  ];
  let notify: (event: object) => void = () => {};
  await page.routeWebSocket("**/api/v1/events", (socket) => {
    notify = (event) => socket.send(JSON.stringify(event));
    socket.onMessage(() =>
      socket.send(JSON.stringify({ type: "ready", revisions: {} })),
    );
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (await mockAuthenticatedSession(route, path)) return;
    const body =
      route.request().method() === "POST" ? route.request().postDataJSON() : {};
    const id = body?.account_id;
    if (id && !settings[id]) settings[id] = defaultExclusions();
    if (path.endsWith("/account-scans") || path.includes("/account-scans/"))
      return route.fulfill({ json: task() });
    if (path.endsWith("/accounts/query"))
      return route.fulfill({ json: accounts });
    if (path.includes("/accounts/"))
      return route.fulfill({
        json: accounts.accounts.find((a) => body.account_id === a.account_id),
      });
    if (path.endsWith("/ai-runs/list"))
      return route.fulfill({
        json: { items: [], pending_count: 0, waiting_count: 0, paused: false },
      });
    if (path.endsWith("/ai-settings/query"))
      return route.fulfill({
        json: {
          instructions: "合成提示词",
          default_instructions: "合成提示词",
          revision: 0,
        },
      });
    if (path.endsWith("/message-images/query"))
      return route.fulfill({
        json: {
          message_id: body.message_id,
          status: "unavailable",
          reason: "image_not_cached",
          needs_sync: true,
        },
      });
    if (path.endsWith("/message-reads") || path.endsWith("/messages/query")) {
      if (state.historyFail && path.endsWith("/message-reads"))
        return route.fulfill({
          status: 503,
          json: { code: "message_read_failed", message: "合成失败" },
        });
      if (path.endsWith("/message-reads")) state.historyReads++;
      const items =
        body.cursor === "older"
          ? Array.from({ length: 12 }, (_, i) => message(i - 12))
          : [
              message(1),
              message(2, true),
              {
                ...message(3),
                type: "voice",
                content: {
                  transcript: "这是微信已有的转写",
                  transcript_status: "available",
                  duration_ms: 3000,
                },
              },
              {
                ...message(4),
                is_self: null,
                sender: {
                  nickname: null,
                  remark: null,
                  wxid: null,
                  wechat_id: null,
                  status: "unknown",
                },
                content: { text: "身份未知的历史消息" },
              },
              {
                ...message(5, true),
                type: "image",
                content: { image_status: "not_loaded" },
              },
            ];
      if (state.extraMessage && body.cursor !== "older") items.push(message(6));
      if (state.stickers && body.cursor !== "older") {
        items[4] = {
          ...message(5, true),
          type: "sticker",
          content: { image_status: "not_loaded" },
        };
        items.push({
          ...message(7),
          type: "sticker",
          content: { image_status: "not_loaded" },
        });
      }
      const task = messageTask(
        messagePage(
          id,
          body.conversation_id,
          items as ReturnType<typeof message>[],
        ),
      );
      task.task_id = `${id}:${body.conversation_id}:${body.cursor || "latest"}`;
      if (body.cursor === "older") task.page!.older_cursor = null;
      messageTasks.set(task.task_id, task);
      if (path.endsWith("/messages/query")) {
        if (body.cursor === "newer") {
          task.page!.items = state.extraMessage ? [message(6)] : [];
          task.page!.has_more = false;
        }
        return route.fulfill({ json: task.page });
      }
      return route.fulfill({ status: 202, json: task });
    }
    if (path.endsWith("/message-image-reads")) {
      state.mediaReads++;
      const gif = state.stickers && body.message_id === "message-7";
      const task = {
        ...messageTask(messagePage(id, body.conversation_id)),
        task_id: `image:${body.message_id}`,
        kind: "image",
        page: null,
        image: state.imageAvailable
          ? {
              message_id: body.message_id,
              status: "available",
              reason: null,
              quality: state.stickers ? "original" : "thumbnail",
              mime_type: gif ? "image/gif" : "image/png",
              data_base64: gif
                ? "R0lGODlhEAAQAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQAFAAAACwAAAAAEAAQAAAIHQABCBxIsKDBgwgTKlzIsKHDhxAjSpxIsaLFgQEBACH5BAEUAAEALAAAAAAQABAAgQAA/wAAAAAAAAAAAAgdAAEIHEiwoMGDCBMqXMiwocOHECNKnEixosWBAQEAOw=="
                : "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
            }
          : {
              message_id: body.message_id,
              status: "unavailable",
              reason: "image_not_downloaded",
            },
      };
      messageTasks.set(task.task_id, task);
      return route.fulfill({ status: 202, json: task });
    }
    if (path.endsWith("/message-read-tasks/query"))
      return route.fulfill({ json: messageTasks.get(body.task_id) });
    if (path.endsWith("/conversation-settings/query"))
      return route.fulfill({
        json: { account_id: id, exclusions: settings[id] },
      });
    if (path.endsWith("/conversation-settings/update")) {
      settings[id] = body.exclusions;
      return route.fulfill({
        json: { account_id: id, exclusions: settings[id] },
      });
    }
    if (path.endsWith("/conversation-refreshes")) {
      state.refreshes++;
      if (state.fail)
        return route.fulfill({
          status: 503,
          json: { code: "conversation_read_failed", message: "合成读取失败" },
        });
      return route.fulfill({
        status: 202,
        json: {
          account_id: id,
          refresh_id: "refresh",
          status: "completed",
          started_at: "2026-10-28T00:00:00Z",
        },
      });
    }
    if (path.endsWith("/conversations/query")) {
      if (Object.keys(body).some((key) => !conversationQueryFields.has(key)))
        return route.fulfill({
          status: 422,
          json: { code: "invalid_request", message: "请求格式不正确" },
        });
      const all: Conversation[] = state.empty
        ? []
        : Array.from({ length: 61 }, (_, index) => {
            const category = categories[index % categories.length]!;
            const name =
              category === "service_accounts"
                ? "服务号"
                : category === "official_accounts"
                  ? "公众号"
                  : category === "folded_chats"
                    ? "折叠的聊天"
                    : category === "wechat_pay"
                      ? "微信支付"
                      : `合成会话 ${index}`;
            return {
              conversation_id: `${id}-${index}`,
              ai_enabled: false,
              ai_pending_count: 0,
              ai_waiting_count: 0,
              ai_paused: false,
              memory_revision: 0,
              preferences_revision: 0,
              name,
              category,
              is_container: [
                "service_accounts",
                "official_accounts",
                "folded_chats",
              ].includes(category),
              summary: "合成消息摘要 · 今天的安排已确认",
              last_message_at: "2026-10-28T05:30:00Z",
              unread_count: index === 0 ? 9 : 0,
              avatar: null,
            };
          });
      const visible = state.allFiltered
        ? []
        : all.filter(
            (item) => !settings[id]![item.category as keyof Exclusions],
          );
      const offset = Number(body.cursor || 0);
      return route.fulfill({
        json: {
          account_id: id,
          source_state: "online",
          coverage: "complete",
          needs_sync: false,
          revision: 0,
          snapshot_id: "snapshot",
          observed_at: "2026-10-28T05:00:00Z",
          status: "complete",
          items: visible.slice(offset, offset + 50),
          total: visible.length,
          unfiltered_total: all.length,
          next_cursor:
            offset + 50 < visible.length ? String(offset + 50) : null,
          applied_exclusions: settings[id],
          issues: [],
          stale: false,
        },
      });
    }
    throw new Error(`Unexpected request ${path}`);
  });
  return { state, settings, notify: (event: object) => notify(event) };
}

test("filter settings persist per account; history renders and preserves the scroll anchor", async ({
  page,
}) => {
  const { state } = await mockConversations(page);
  await page.goto("/");
  await page.getByRole("button", { name: "进入工作台" }).first().click();
  await page.getByRole("link", { name: "设置", exact: true }).click();
  await expect(page.getByRole("switch")).toHaveCount(7);
  await expect(
    page.getByRole("switch", { name: "排除微信支付", exact: true }),
  ).toBeChecked();
  await page.locator('label[for="exclude-wechat_pay"]').click();
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByText("设置已保存", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "../.local/framework-acceptance/conversation-settings-synthetic.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "会话", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /微信支付/ }).first(),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /服务号/ })).toHaveCount(0);
  await page.getByRole("button", { name: /合成会话 0 / }).click();
  await expect(page.locator(".message-heading")).toBeVisible();
  await expect(page.getByText("合成消息 2", { exact: true })).toBeVisible();
  await expect(
    page.getByText("这是微信已有的转写", { exact: true }),
  ).toBeVisible();
  await page.locator(".message-image").scrollIntoViewIfNeeded();
  await expect(
    page.getByText("图片尚未保存在本机", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".message-neutral")).toContainText(
    "身份未知的历史消息",
  );
  const left = await page
    .locator('[data-message-id="message-1"] .message-bubble')
    .boundingBox();
  const right = await page
    .locator('[data-message-id="message-2"] .message-bubble')
    .boundingBox();
  expect(right!.x).toBeGreaterThan(left!.x);
  await page.locator(".message-scroll").evaluate((el) => {
    el.scrollTop = 0;
  });
  await page.getByRole("button", { name: "加载更早记录", exact: true }).click();
  await expect(page.locator(".message-row")).toHaveCount(17);
  expect(
    await page.locator(".message-scroll").evaluate((el) => el.scrollTop),
  ).toBeGreaterThan(0);
  expect(state.refreshes).toBe(0);
  await page.screenshot({
    path: "../.local/framework-acceptance/conversations-synthetic.png",
    fullPage: true,
  });
  await page.reload();
  await page.getByRole("link", { name: "设置", exact: true }).click();
  await expect(
    page.getByRole("switch", { name: "排除微信支付", exact: true }),
  ).not.toBeChecked();
  await page.getByRole("button", { name: "切换账号" }).click();
  await page.getByRole("button", { name: "进入工作台" }).nth(1).click();
  await page.getByRole("link", { name: "设置", exact: true }).click();
  await expect(
    page.getByRole("switch", { name: "排除微信支付", exact: true }),
  ).toBeChecked();
});

test("base64 image renders and a failed manual refresh preserves visible history", async ({
  page,
}) => {
  const { state } = await mockConversations(page);
  state.imageAvailable = true;
  await page.goto("/workspace/synthetic-account-1/conversations");
  await page.getByRole("button", { name: /合成会话 0 / }).click();
  await page.locator(".message-image").scrollIntoViewIfNeeded();
  const image = page.getByRole("img", { name: "缩略图", exact: true });
  await expect(image).toBeVisible();
  await expect
    .poll(() => image.evaluate((img) => (img as HTMLImageElement).naturalWidth))
    .toBe(1);
  state.historyFail = true;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("聊天记录暂未更新");
  await expect(page.locator(".message-row")).toHaveCount(5);
  await expect(image).toBeVisible();
  await page.screenshot({
    path: "../.local/framework-acceptance/message-history-synthetic.png",
    fullPage: true,
  });
});

test("static stickers and GIF animate from JSON and suspend outside visible content", async ({
  page,
}) => {
  const { state } = await mockConversations(page);
  state.stickers = state.imageAvailable = true;
  const remoteImages: string[] = [];
  page.on("request", (request) => {
    if (
      request.resourceType() === "image" &&
      request.url().startsWith("http") &&
      !request.url().startsWith("http://127.0.0.1")
    )
      remoteImages.push(request.url());
  });
  await page.goto("/workspace/synthetic-account-1/conversations");
  await page.getByRole("button", { name: /合成会话 0 / }).click();
  const gif = page.getByRole("img", { name: "动态表情", exact: true });
  await page.locator('[data-message-id="message-7"]').scrollIntoViewIfNeeded();
  await expect(gif).toBeVisible();
  await expect(gif).toHaveAttribute("src", /^data:image\/gif;base64,/);
  await expect(
    page.getByRole("img", { name: "表情", exact: true }),
  ).toHaveAttribute("src", /^data:image\/png;base64,/);
  const frames = new Set<string>();
  await expect
    .poll(
      async () => {
        frames.add(
          (await gif.screenshot({ animations: "allow" })).toString("base64"),
        );
        return frames.size;
      },
      { intervals: [70, 130, 190], timeout: 3000 },
    )
    .toBeGreaterThan(1);
  const reads = state.mediaReads;
  const height = await page
    .locator('[data-message-id="message-7"]')
    .evaluate((node) => node.getBoundingClientRect().height);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(gif).toHaveCount(0);
  expect(
    await page
      .locator('[data-message-id="message-7"]')
      .evaluate((node) => node.getBoundingClientRect().height),
  ).toBeCloseTo(height, 0);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(gif).toBeVisible();
  expect(state.mediaReads).toBe(reads);
  await page.locator(".message-scroll").evaluate((node) => {
    (node as HTMLElement).style.height = "100px";
    node.scrollTop = 0;
  });
  await expect(gif).toHaveCount(0);
  await page.locator('[data-message-id="message-7"]').scrollIntoViewIfNeeded();
  await expect(gif).toBeVisible();
  expect(state.mediaReads).toBe(reads);
  expect(remoteImages).toEqual([]);
  await page.screenshot({
    path: "../.local/sticker-acceptance/stickers-synthetic.png",
  });
});

test("cache query contract, pagination, manual refresh, and mobile empty states", async ({
  page,
}) => {
  const { state, settings } = await mockConversations(page);
  settings["synthetic-account-1"] = Object.fromEntries(
    Object.keys(defaultExclusions()).map((k) => [k, false]),
  ) as Exclusions;
  const firstQuery = page.waitForResponse("**/api/v1/conversations/query");
  await page.goto("/workspace/synthetic-account-1/conversations");
  const initialResponse = await firstQuery;
  expect(initialResponse.status()).toBe(200);
  expect(initialResponse.request().postDataJSON()).toEqual({
    account_id: "synthetic-account-1",
    cursor: null,
    page_size: 50,
  });
  await expect(page.locator(".conversation-row")).toHaveCount(50);
  expect(state.refreshes).toBe(0);
  const nextQuery = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/conversations/query") &&
      response.request().postDataJSON().cursor === "50",
  );
  await page.getByRole("button", { name: "加载更多" }).click();
  const nextResponse = await nextQuery;
  expect(nextResponse.status()).toBe(200);
  expect(nextResponse.request().postDataJSON()).toEqual({
    account_id: "synthetic-account-1",
    cursor: "50",
    page_size: 50,
  });
  await expect(page.locator(".conversation-row")).toHaveCount(61);
  const refresh = page.waitForResponse("**/api/v1/conversation-refreshes");
  await page.getByRole("button", { name: "刷新会话" }).click();
  const refreshResponse = await refresh;
  expect(refreshResponse.status()).toBe(202);
  expect(refreshResponse.request().postDataJSON()).toEqual({
    account_id: "synthetic-account-1",
  });
  await expect(page.getByRole("button", { name: "加载更多" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "刷新会话" })).toBeEnabled();
  await expect(page.locator(".conversation-row")).toHaveCount(61);
  expect(state.refreshes).toBe(1);
  state.fail = true;
  await page.getByRole("button", { name: "刷新会话" }).click();
  await expect(page.getByRole("alert")).toContainText("当前显示上次读取结果");
  await expect(page.locator(".conversation-row")).toHaveCount(61);
  state.fail = false;
  await page.getByRole("link", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "恢复默认", exact: true }).click();
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByText("设置已保存", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  state.allFiltered = true;
  await page.getByRole("link", { name: "会话", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "会话已全部过滤" }),
  ).toBeVisible();
  state.empty = true;
  await page.getByRole("button", { name: "刷新会话" }).click();
  await expect(page.getByRole("heading", { name: "还没有会话" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.local/framework-acceptance/conversations-mobile-synthetic.png",
    fullPage: true,
  });
});

test("JSON notification refreshes the database view and resync catches up", async ({
  page,
}) => {
  const { state, notify } = await mockConversations(page);
  await page.goto("/workspace/synthetic-account-1/conversations");
  await page.getByRole("button", { name: /合成会话 0 / }).click();
  await expect(page.locator(".message-row")).toHaveCount(5);
  state.extraMessage = true;
  notify({
    type: "changed",
    account_id: "synthetic-account-1",
    resources: ["messages"],
    revision: 2,
  });
  await expect(page.locator('[data-message-id="message-6"]')).toHaveCount(1);
  expect(state.historyReads).toBe(0);
  notify({ type: "resync" });
  await expect(page.locator(".message-row")).toHaveCount(6);
  expect(state.refreshes).toBe(0);
});
