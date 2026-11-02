import { test, expect } from "@playwright/test";
import { mockAuthenticatedSession } from "./auth-fixture";
import { snapshot } from "../tests/fixtures";
import type { NotificationSettings } from "../src/api";

test("global SMTP settings save without echoing secrets, preserve blank password and test delivery", async ({
  page,
}) => {
  let settings: NotificationSettings = {
    revision: 0,
    enabled: false,
    recipient: "",
    sender: "",
    host: "",
    port: 465,
    username: "",
    security: "ssl",
    has_password: false,
    password_readable: false,
    latest: null,
  };
  let last: Record<string, unknown> = {};
  let tests = 0;
  await page.routeWebSocket("**/api/v1/events", (socket) => {
    socket.onMessage(() => socket.send(JSON.stringify({ type: "ready" })));
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (await mockAuthenticatedSession(route, path)) return;
    if (path.endsWith("/accounts/query"))
      return route.fulfill({ json: snapshot() });
    if (path.endsWith("/notification-settings/query"))
      return route.fulfill({ json: settings });
    if (path.endsWith("/notification-settings/update")) {
      last = route.request().postDataJSON();
      const { password, expected_revision, ...fields } = last;
      expect(expected_revision).toBe(settings.revision);
      settings = {
        ...settings,
        ...fields,
        revision: settings.revision + 1,
        has_password: true,
        password_readable: true,
      };
      expect(JSON.stringify(settings)).not.toContain("smtp-test-secret");
      return route.fulfill({ json: settings });
    }
    if (path.endsWith("/notification-settings/test")) {
      tests++;
      settings.latest = {
        notification_id: "test-mail",
        status: "sent",
        attempts: 1,
        message: "SMTP 服务器已接受邮件。",
      };
      return route.fulfill({ status: 202, json: settings.latest });
    }
    throw new Error(path);
  });
  await page.goto("/settings/notifications");
  await page.getByLabel("收件邮箱", { exact: true }).fill("owner@example.test");
  await page
    .getByLabel("发件邮箱", { exact: true })
    .fill("bridge@example.test");
  await page
    .getByLabel("SMTP 服务器", { exact: true })
    .fill("smtp.example.test");
  await page.getByLabel("SMTP 用户名", { exact: true }).fill("bridge");
  await page
    .getByLabel("SMTP 授权码", { exact: true })
    .fill("smtp-test-secret");
  await page.getByLabel("启用掉线邮件通知").check();
  await page.getByRole("button", { name: "保存邮件设置" }).click();
  await expect(page.getByLabel("SMTP 授权码", { exact: true })).toHaveValue("");
  expect(last.password).toBe("smtp-test-secret");
  await page
    .getByLabel("收件邮箱", { exact: true })
    .fill("updated@example.test");
  await page.getByRole("button", { name: "保存邮件设置" }).click();
  await expect(
    page.getByText("邮件设置已保存。", { exact: true }),
  ).toBeVisible();
  expect(last).not.toHaveProperty("password");
  await page.getByRole("button", { name: "发送测试邮件" }).click();
  await expect(page.getByText(/最近投递：邮件已提交/)).toBeVisible();
  expect(tests).toBe(1);
  expect(
    await page.evaluate(() => JSON.stringify({ ...localStorage })),
  ).not.toContain("smtp-test-secret");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../.local/reliability-notifications-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
