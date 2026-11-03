import { test, expect } from "@playwright/test";
import { mockAuthenticatedSession } from "./auth-fixture";
import { snapshot } from "../tests/fixtures";

test("upgrade is opt-in and reconnects to the selected version", async ({
  page,
}) => {
  let installed = false,
    installing = false,
    offline = false,
    checks = 0;
  const latest = {
    version: "0.4.1",
    release_id: 42,
    tag: "v0.4.1",
    published_at: "2026-11-03T00:00:00Z",
    notes: "合成更新说明",
    ready: true,
  };
  const state = () => ({
    mode: "portable",
    can_install: true,
    current_version: installed ? "0.4.1" : "0.4.0",
    current_commit: "abcd",
    releases_url: "https://github.com/fichil/wechat-agent-bridge/releases",
    checked_at: 1,
    latest: installed ? null : latest,
    task: installing
      ? {
          stage: installed ? "succeeded" : "switching",
          version: "0.4.1",
          message: installed ? "升级成功" : "正在切换程序版本",
        }
      : null,
  });
  await page.routeWebSocket("**/api/v1/events", (s) =>
    s.onMessage(() => s.send(JSON.stringify({ type: "ready" }))),
  );
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/updates/query")) {
      if (offline) return route.abort("connectionrefused");
      return route.fulfill({ json: state() });
    }
    if (path.endsWith("/updates/check")) {
      checks++;
      return route.fulfill({ json: state() });
    }
    if (path.endsWith("/updates/install")) {
      expect(route.request().postDataJSON()).toEqual({
        version: "0.4.1",
        release_id: 42,
      });
      installing = true;
      offline = true;
      return route.fulfill({ status: 202, json: state() });
    }
    if (await mockAuthenticatedSession(route, path)) return;
    if (path.endsWith("/accounts/query"))
      return route.fulfill({ json: snapshot() });
    return route.fulfill({ json: {} });
  });
  await page.goto("/settings/updates");
  await expect(page.getByText("合成更新说明")).toBeVisible();
  await page.getByRole("button", { name: "检查更新" }).click();
  expect(checks).toBe(1);
  await page.getByRole("button", { name: "一键升级" }).click();
  await page.getByRole("button", { name: "稍后" }).click();
  expect(installing).toBe(false);
  await page.getByRole("button", { name: "一键升级" }).click();
  await page.getByRole("button", { name: "确认升级" }).click();
  await expect(page.getByText(/正在等待后台重新连接/)).toBeVisible();
  offline = false;
  installed = true;
  await expect(
    page.getByRole("heading", { name: "当前版本：v0.4.1" }),
  ).toBeVisible({ timeout: 10000 });
});
