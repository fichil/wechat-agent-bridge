import { test, expect } from "@playwright/test";
import { mockAuthenticatedSession } from "./auth-fixture";
import { snapshot, syntheticAccount } from "../tests/fixtures";

test("poll timeout and disconnection invalidate readiness, then recover", async ({
  page,
}) => {
  let mode: "ready" | "timeout" | "offline" = "ready";
  let accountRequests = 0;
  await page.routeWebSocket("**/api/v1/events", (socket) =>
    socket.onMessage(() => {}),
  );
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/auth/session") {
      await mockAuthenticatedSession(route, path);
      return;
    }
    if (path === "/api/v1/accounts/query") accountRequests++;
    if (mode === "timeout") return;
    if (mode === "offline") {
      await route.abort();
      return;
    }
    if (path === "/api/v1/compatibility/query") {
      await route.fulfill({
        json: {
          enforced: true,
          held: false,
          reason: "",
          message: "已就绪",
          migration_pending: false,
          unresolved_instances: [],
          accounts: [
            {
              account_id: syntheticAccount.account_id,
              nickname: "合成账号",
              instances: [{ version: "4.2.0.9" }],
              capabilities: { account: true, messages: true, send_text: true },
              auto_state: "ready",
              message: "自动检查通过，已就绪。",
            },
          ],
        },
      });
    } else if (path === "/api/v1/accounts/query")
      await route.fulfill({ json: snapshot() });
    else throw new Error(`Unexpected route: ${path}`);
  });
  await page.clock.install();
  await page.goto("/accounts");
  const panel = page.getByRole("region", { name: "微信版本兼容" });
  await expect(panel.getByText("已就绪", { exact: true })).toBeVisible();
  mode = "timeout";
  await page.clock.fastForward(10_000);
  await expect.poll(() => accountRequests).toBeGreaterThan(1);
  await page.clock.fastForward(8001);
  await expect(panel).toContainText("后台响应超时");
  await expect(panel).toContainText("最后成功检查");
  await expect(panel).toContainText("以下为历史状态");
  await expect(panel.getByText("已就绪", { exact: true })).toHaveCount(0);
  await expect(page.getByText("历史状态", { exact: true })).toBeVisible();
  mode = "offline";
  await page.clock.fastForward(10_000);
  await expect(panel).toContainText("无法连接后台");
  mode = "ready";
  await page.clock.fastForward(10_000);
  await expect(panel.getByText("已就绪", { exact: true })).toBeVisible();
  await expect(panel).not.toContainText("连接中断");
  await expect(page.getByText("历史状态", { exact: true })).toHaveCount(0);
});
