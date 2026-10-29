import { test, expect, type Page } from "@playwright/test";
import { snapshot, syntheticAccount, task } from "../tests/fixtures";
import type { Snapshot } from "../src/api";

async function mockApi(page: Page, initial = snapshot()) {
  const state = { snapshot: initial, fail: false, posts: 0, running: false };
  await page.routeWebSocket("**/api/v1/events", (socket) => {
    socket.onMessage(() =>
      socket.send(JSON.stringify({ type: "ready", revisions: {} })),
    );
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (state.fail) {
      await route.abort();
      return;
    }
    if (path === "/api/v1/account-scans") {
      state.posts++;
      await route.fulfill({
        status: 202,
        json: task(state.running ? "running" : "completed"),
      });
    } else if (path.startsWith("/api/v1/account-scans/")) {
      await route.fulfill({ json: task() });
    } else if (path === "/api/v1/accounts/query") {
      await route.fulfill({ json: state.snapshot });
    } else if (path.startsWith("/api/v1/accounts/")) {
      const account = state.snapshot.accounts.find(
        (a) => route.request().postDataJSON().account_id === a.account_id,
      );
      await route.fulfill({
        status: account ? 200 : 404,
        json: account ?? {
          code: "account_not_found",
          message: "账号已失效，请重新识别。",
        },
      });
    } else throw new Error(`Unexpected synthetic route: ${path}`);
  });
  return state;
}

test("cached accounts → workspace → switch, without source scans", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.running = true;
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "合成测试账号" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../.local/framework-acceptance/accounts-synthetic.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page).toHaveURL(/workspace\/synthetic-account-1\/overview/);
  await expect(page.getByText("微信版本", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "../.local/framework-acceptance/workspace-synthetic.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Agent", exact: true }).click();
  await expect(page.getByRole("heading", { name: "功能待接入" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "切换账号" }).click();
  await expect(page).toHaveURL(/\/accounts$/);
  expect(state.posts).toBe(0);
});

test("reload preserves cached selection; a genuinely missing archive returns to picker", async ({
  page,
}) => {
  const state = await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.reload();
  await expect(page.getByRole("region", { name: "账号概览" })).toBeVisible();
  expect(state.posts).toBe(0);
  state.snapshot = snapshot([
    { ...syntheticAccount, account_id: "new-backend-id" },
  ]);
  await page.reload();
  await expect(page).toHaveURL(/\/accounts$/);
  await expect(page.getByText(/账号已失效/)).toBeVisible();
  await page.getByRole("button", { name: "进入工作台" }).click();
  state.snapshot = snapshot([]);
  await page.reload();
  await expect(page).toHaveURL(/\/accounts$/);
  await expect(
    page.getByRole("heading", { name: "还没有发现已登录的微信" }),
  ).toBeVisible();
});

test("empty, partial and permission-denied states never pretend to be logged in", async ({
  page,
}) => {
  const state = await mockApi(page, snapshot([]));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "还没有发现已登录的微信" }),
  ).toBeVisible();
  const partial: Snapshot = snapshot([
    {
      ...syntheticAccount,
      account_id: null,
      wxid: null,
      nickname: null,
      status: "unavailable",
      reason: "process_metadata_access_denied",
    },
  ]);
  partial.exit_code = 2;
  partial.summary.coverage_complete = false;
  partial.issues = [{ reason: "process_metadata_access_denied", pid: 100 }];
  state.snapshot = partial;
  await page.getByRole("button", { name: "重新识别" }).click();
  await expect(page.getByRole("button", { name: "暂不可进入" })).toBeDisabled();
  await expect(
    page.getByText("部分账号信息尚未确认，已确认的账号仍可进入。"),
  ).toBeVisible();
  await expect(page.getByText(/没有权限读取/).first()).toBeVisible();
});

test("network failure can be retried and mobile layout remains usable", async ({
  page,
}) => {
  const state = await mockApi(page);
  state.fail = true;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByText(/无法连接本地服务/)).toBeVisible();
  state.fail = false;
  await page.getByRole("button", { name: "重新识别" }).click();
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("button", { name: "切换账号" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("selection is separate in two tabs", async ({ page, context }) => {
  const other = {
    ...syntheticAccount,
    account_id: "second-account",
    nickname: "合成第二账号",
    wxid: "wxid_synthetic_second",
  };
  await mockApi(page, snapshot([syntheticAccount, other]));
  const second = await context.newPage();
  await mockApi(second, snapshot([syntheticAccount, other]));
  await page.goto("/");
  await second.goto("/");
  await page
    .getByTestId("account-card")
    .filter({ hasText: "合成测试账号" })
    .getByRole("button", { name: "进入工作台" })
    .click();
  await second
    .getByTestId("account-card")
    .filter({ hasText: "合成第二账号" })
    .getByRole("button", { name: "进入工作台" })
    .click();
  await expect(page).toHaveURL(/synthetic-account-1/);
  await expect(second).toHaveURL(/second-account/);
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("bridge.selected-account"),
    ),
  ).toBe("synthetic-account-1");
  expect(
    await second.evaluate(() =>
      sessionStorage.getItem("bridge.selected-account"),
    ),
  ).toBe("second-account");
});
