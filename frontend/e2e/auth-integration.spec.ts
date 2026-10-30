import { test, expect, type Page } from "@playwright/test";

const service = "http://127.0.0.1:18765";
const password = "synthetic-test-password-2026";
let accountId: string;
test.beforeEach(async ({ context }) => {
  const headers = { Origin: "http://127.0.0.1:15173" };
  const login = await context.request.post(service + "/api/v1/auth/login", {
    data: { username: "admin", password },
    headers,
  });
  expect(login.ok()).toBeTruthy();
  const authorized = {
    ...headers,
    "X-CSRF-Token": (await login.json()).csrf_token,
  };
  const reset = await context.request.post(service + "/test/reset", {
    data: {},
    headers: authorized,
  });
  accountId = (await reset.json())[0].account_id;
  await context.request.post(service + "/api/v1/auth/logout", {
    data: {},
    headers: authorized,
  });
});
async function signIn(page: Page) {
  await page.getByLabel("后台用户名").fill("admin");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/accounts/);
}

test("unauthenticated page never requests business data; wrong password and refresh login", async ({
  page,
}) => {
  const calls: string[] = [];
  page.on("request", (req) => {
    const path = new URL(req.url()).pathname;
    if (path.startsWith("/api/v1/")) calls.push(path);
  });
  await page.goto("/accounts");
  await expect(page).toHaveURL(/\/login/);
  expect(calls.every((path) => path === "/api/v1/auth/session")).toBeTruthy();
  await page.getByLabel("密码", { exact: true }).fill("incorrect");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("用户名或密码不正确。");
  await signIn(page);
  await page.reload();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  const cookies = await page.context().cookies();
  const session = cookies.find((c) => c.name === "bridge_dev_session")!;
  expect(session.httpOnly).toBeTruthy();
  expect(session.sameSite).toBe("Strict");
  expect(session.expires - Date.now() / 1000).toBeGreaterThan(6.9 * 86400);
});

test("logout clears both tabs, guards history and rejects old session", async ({
  page,
  context,
}) => {
  await page.goto("/login");
  await signIn(page);
  const second = await context.newPage();
  await second.goto("/accounts");
  const oldCookie = (await context.cookies()).find(
    (c) => c.name === "bridge_dev_session",
  )!;
  const state = await page.request.post(service + "/api/v1/auth/session", {
    data: {},
    headers: { Origin: "http://127.0.0.1:15173" },
  });
  const csrf = (await state.json()).csrf_token;
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login/);
  await expect(second).toHaveURL(/\/login/);
  const denied = await page.request.post(service + "/api/v1/accounts/query", {
    data: {},
    headers: {
      Origin: "http://127.0.0.1:15173",
      Cookie: `bridge_dev_session=${oldCookie.value}`,
      "X-CSRF-Token": csrf,
    },
  });
  expect(denied.status()).toBe(401);
  await page.goto("/accounts");
  await expect(page).toHaveURL(/\/login/);
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("bridge.selected-account"),
    ),
  ).toBeNull();
});

test("deep link returns after login and invalid external redirects stay on site", async ({
  page,
}) => {
  await page.goto("/login");
  await signIn(page);
  const state = await page.request.post(service + "/api/v1/auth/session", {
    data: {},
    headers: { Origin: "http://127.0.0.1:15173" },
  });
  const reset = await page.request.post(service + "/test/reset", {
    data: {},
    headers: {
      Origin: "http://127.0.0.1:15173",
      "X-CSRF-Token": (await state.json()).csrf_token,
    },
  });
  const ids = await reset.json();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login/);
  const target = `/workspace/${ids[0].account_id}/logs`;
  await page.goto(target);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(target);
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/login?next=https://evil.example");
  await signIn(page);
});

test("mobile login fits and password dialog does not retain canceled passwords", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await signIn(page);
  await page.getByRole("button", { name: "修改密码" }).click();
  await page.getByLabel("当前密码", { exact: true }).fill("a-sensitive-draft");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "修改密码" }).click();
  await expect(page.getByLabel("当前密码", { exact: true })).toHaveValue("");
});

test("revoked session redirects an active page without exposing a new event", async ({
  page,
}) => {
  await page.goto("/login");
  await signIn(page);
  const state = await page.request.post(service + "/api/v1/auth/session", {
    data: {},
    headers: { Origin: "http://127.0.0.1:15173" },
  });
  await page.request.post(service + "/api/v1/auth/logout", {
    data: {},
    headers: {
      Origin: "http://127.0.0.1:15173",
      "X-CSRF-Token": (await state.json()).csrf_token,
    },
  });
  await expect(page).toHaveURL(/\/login/);
});

test("logout bypasses unsaved draft guards and destroys sensitive drafts", async ({
  page,
  context,
}) => {
  await page.goto("/login");
  await signIn(page);
  await page.goto(`/workspace/${accountId}/agents`);
  await page
    .getByRole("button", { name: "新增 API 配置", exact: true })
    .click();
  await page
    .getByLabel("API 密钥 *", { exact: true })
    .fill("synthetic-sensitive-draft");
  const second = await context.newPage();
  await second.goto("/accounts");
  let dialog = false;
  page.on("dialog", async (value) => {
    dialog = true;
    await value.dismiss();
  });
  await second.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login/);
  expect(dialog).toBeFalsy();
  expect(
    await page.evaluate(
      () => JSON.stringify(localStorage) + JSON.stringify(sessionStorage),
    ),
  ).not.toContain("synthetic-sensitive-draft");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(`/workspace/${accountId}/agents`);
  await page
    .getByRole("button", { name: "新增 API 配置", exact: true })
    .click();
  await expect(page.getByLabel("API 密钥 *", { exact: true })).toHaveValue("");
});

test("password change logs out both devices and accepts only the new password", async ({
  page,
  browser,
}) => {
  await page.goto("/login");
  await signIn(page);
  const device = await browser.newContext({
    baseURL: "http://127.0.0.1:15173",
  });
  const second = await device.newPage();
  await second.goto("/login");
  await signIn(second);
  const replacement = password + "-changed";
  try {
    await page.getByRole("button", { name: "修改密码" }).click();
    await page.getByLabel("当前密码", { exact: true }).fill(password);
    await page.getByLabel("新密码", { exact: true }).fill(replacement);
    await page.getByLabel("确认新密码", { exact: true }).fill(replacement);
    await page.getByRole("button", { name: "保存密码" }).click();
    await expect(page).toHaveURL(/\/login/);
    await expect(second).toHaveURL(/\/login/);
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveText("用户名或密码不正确。");
    await page.getByLabel("密码", { exact: true }).fill(replacement);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page).toHaveURL(/\/accounts/);
  } finally {
    const login = await page.request.post(service + "/api/v1/auth/login", {
      data: { username: "admin", password: replacement },
      headers: { Origin: "http://127.0.0.1:15173" },
    });
    if (login.ok())
      await page.request.post(service + "/api/v1/auth/change-password", {
        data: { current_password: replacement, new_password: password },
        headers: {
          Origin: "http://127.0.0.1:15173",
          "X-CSRF-Token": (await login.json()).csrf_token,
        },
      });
    await device.close();
  }
});
