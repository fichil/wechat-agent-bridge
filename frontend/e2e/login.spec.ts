import { test, expect } from "@playwright/test";

test("uninitialized login offers only the server command", async ({ page }) => {
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ json: { initialized: false, authenticated: false } }),
  );
  await page.goto("/login");
  await expect(page.getByRole("status")).toContainText(
    "python -m backend.admin init",
  );
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toHaveCount(0);
});

test("login distinguishes request limits and network failure", async ({
  page,
}) => {
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ json: { initialized: true, authenticated: false } }),
  );
  let limited = true;
  await page.route("**/api/v1/auth/login", (route) =>
    limited
      ? route.fulfill({
          status: 429,
          json: {
            code: "login_rate_limited",
            message: "尝试次数过多，请稍后再试。",
          },
        })
      : route.abort(),
  );
  await page.goto("/login");
  await page.getByLabel("密码", { exact: true }).fill("synthetic-only");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "尝试次数过多，请稍后再试。",
  );
  limited = false;
  await page.getByLabel("密码", { exact: true }).fill("synthetic-only");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "无法连接后台，请检查网络后重试。",
  );
});
