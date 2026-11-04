import { test, expect, type Page, type Route } from "@playwright/test";
import { mockAuthenticatedSession } from "./auth-fixture";
import { snapshot, syntheticAccount } from "../tests/fixtures";
import type { LogClearTask } from "../src/api";

const aid = syntheticAccount.account_id!;
const second = "synthetic-account-2";
const logsUrl = `/workspace/${aid}/logs`;
const button = (page: Page) =>
  page.getByRole("button", { name: "清空当前账号日志", exact: true });
const oldLog = {
  log_id: "old-log",
  account_id: aid,
  category: "ai",
  operation: "conversation",
  status: "completed",
  started_at: "2026-11-04T01:00:00Z",
  updated_at: "2026-11-04T01:00:00Z",
  count: 1,
};
const logPage = (items: object[]) => ({
  items,
  next_cursor: null,
  size_bytes: 100,
  limit_bytes: 536870912,
});

async function workspace(page: Page) {
  const state = {
    task: null as LogClearTask | null,
    starts: [] as { account_id: string; scope: "account" | "global" }[],
    failQuery: false,
    failRefresh: false,
    deleted: false,
    loseStartResponse: false,
    holdList: false,
    heldList: null as Route | null,
    holdDetail: false,
    heldDetail: null as Route | null,
  };
  await page.routeWebSocket("**/api/v1/events", (socket) => {
    socket.onMessage(() => socket.send(JSON.stringify({ type: "ready" })));
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (await mockAuthenticatedSession(route, path)) return;
    const body = route.request().postDataJSON();
    if (path.endsWith("/accounts/query"))
      return route.fulfill({
        json: snapshot([
          syntheticAccount,
          { ...syntheticAccount, account_id: second },
        ]),
      });
    if (path.endsWith("/accounts/get"))
      return route.fulfill({
        json: { ...syntheticAccount, account_id: body.account_id },
      });
    if (path.endsWith("/api-profiles/query"))
      return route.fulfill({ json: { items: [], presets: [] } });
    if (path.endsWith("/ai-runs/sessions/query"))
      return route.fulfill({ json: { items: [] } });
    if (path.endsWith("/logs/clear-tasks/query")) {
      if (state.failQuery) return route.abort();
      const task =
        state.task?.scope === body.scope &&
        (body.scope === "global" || state.task?.account_id === body.account_id)
          ? state.task
          : null;
      return route.fulfill({ json: { task } });
    }
    if (path.endsWith("/logs/clear-tasks")) {
      state.starts.push(body);
      state.task = {
        task_id: `task-${state.starts.length}`,
        account_id: body.scope === "global" ? null : body.account_id,
        scope: body.scope,
        status: "running",
        total_count: 400,
        processed_count: 200,
        deleted_count: 200,
        started_at: new Date().toISOString(),
      };
      if (state.loseStartResponse) return route.abort();
      return route.fulfill({ status: 202, json: state.task });
    }
    if (path.endsWith("/logs/query")) {
      if (state.holdList) {
        state.holdList = false;
        state.heldList = route;
        return;
      }
      if (state.failRefresh)
        return route.fulfill({
          status: 503,
          json: { code: "storage_unavailable", message: "合成刷新失败" },
        });
      return route.fulfill({ json: logPage(state.deleted ? [] : [oldLog]) });
    }
    if (path.endsWith("/logs/get")) {
      if (state.holdDetail) {
        state.heldDetail = route;
        return;
      }
      return route.fulfill({
        json: { ...oldLog, details: { note: "旧详情" } },
      });
    }
    throw new Error(`Unexpected route: ${path}`);
  });
  await page.goto(logsUrl);
  await expect(page.locator(".log-entry")).toHaveCount(1);
  return state;
}

async function start(page: Page) {
  await button(page).click();
  await page.getByRole("button", { name: "清空", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "正在后台清理" }),
  ).toContainText("200/400");
}

test("cleanup confirms, shows progress, survives navigation and reports zero results", async ({
  page,
}) => {
  const state = await workspace(page);
  await button(page).click();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  expect(state.starts).toHaveLength(0);
  await start(page);
  await expect(button(page)).toBeDisabled();
  await page.goto("/accounts");
  await page.goto(logsUrl);
  await expect(
    page.getByRole("status").filter({ hasText: "正在后台清理" }),
  ).toBeVisible();
  expect(state.starts).toHaveLength(1);
  state.deleted = true;
  Object.assign(state.task!, {
    status: "completed",
    processed_count: 400,
    deleted_count: 400,
  });
  await expect(
    page.getByRole("status").filter({ hasText: "已删除 400 条日志" }),
  ).toBeVisible();
  await expect(page.locator(".log-entry")).toHaveCount(0);
  await expect(button(page)).toBeEnabled();
  await start(page);
  Object.assign(state.task!, {
    status: "completed",
    total_count: 0,
    processed_count: 0,
    deleted_count: 0,
  });
  await expect(page.getByText(/没有可清理的已结束日志/)).toBeVisible();
});

test("cleanup progress query failure recovers and partial failure retains its count", async ({
  page,
}) => {
  const state = await workspace(page);
  await start(page);
  state.failQuery = true;
  await expect(page.getByRole("alert")).toContainText("进度暂时无法查询");
  await expect(button(page)).toBeDisabled();
  state.failQuery = false;
  Object.assign(state.task!, {
    status: "failed",
    error: "log_clear_failed",
    message: "清理失败，已完成的删除已保留，可重新发起清理。",
  });
  await expect(page.getByRole("alert")).toContainText("已删除 200 条");
  await expect(button(page)).toBeEnabled();
  expect(state.starts).toHaveLength(1);
});

test("lost start response discovers the accepted task without submitting again", async ({
  page,
}) => {
  const state = await workspace(page);
  state.loseStartResponse = true;
  await button(page).click();
  await page.getByRole("button", { name: "清空", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("未能确认清理是否已启动");
  await expect(
    page.getByRole("status").filter({ hasText: "正在后台清理" }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(state.starts).toHaveLength(1);
});

test("cleanup completion fences old list and detail responses", async ({
  page,
}) => {
  const state = await workspace(page);
  state.holdDetail = true;
  await page.locator(".log-entry > summary").click();
  await expect.poll(() => !!state.heldDetail).toBe(true);
  state.holdList = true;
  await page.getByRole("button", { name: "刷新日志", exact: true }).click();
  await expect.poll(() => !!state.heldList).toBe(true);
  await start(page);
  state.deleted = true;
  Object.assign(state.task!, {
    status: "completed",
    processed_count: 400,
    deleted_count: 400,
  });
  await expect(page.locator(".log-entry")).toHaveCount(0);
  await state.heldList!.fulfill({ json: logPage([oldLog]) });
  await state.heldDetail!.fulfill({
    status: 404,
    json: { code: "log_not_found", message: "旧详情错误" },
  });
  await expect(page.locator(".log-entry")).toHaveCount(0);
  await expect(page.getByText("旧详情错误")).toHaveCount(0);
});

test("cleanup success remains distinct from a failed list refresh", async ({
  page,
}) => {
  const state = await workspace(page);
  await start(page);
  state.failRefresh = true;
  Object.assign(state.task!, {
    status: "completed",
    processed_count: 400,
    deleted_count: 400,
  });
  await expect(
    page.getByRole("status").filter({ hasText: "已删除 400 条日志" }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toContainText(
    "清理已结束，但日志列表刷新失败",
  );
});

test("changing accounts while a confirmation is open never clears the new account", async ({
  page,
}) => {
  const state = await workspace(page);
  await button(page).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.evaluate((url) => {
    history.pushState({}, "", url);
    dispatchEvent(new PopStateEvent("popstate"));
  }, `/workspace/${second}/logs`);
  await expect(page).toHaveURL(new RegExp(second));
  await page.getByRole("button", { name: "清空", exact: true }).click();
  await expect(button(page)).toBeEnabled();
  expect(state.starts).toHaveLength(0);
});
