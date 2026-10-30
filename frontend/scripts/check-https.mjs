// Real Caddy + production build + synthetic API, bound only to loopback.
import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdir, writeFile, open } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url));
const caddy = process.env.BRIDGE_CADDY;
if (!caddy)
  throw new Error(
    "Set BRIDGE_CADDY to an official caddy.exe; build the frontend first.",
  );
const directory = path.join(
  root,
  ".local",
  "auth-acceptance",
  `https-${Date.now()}`,
);
await mkdir(directory, { recursive: true });
const log = await open(path.join(directory, "services.log"), "w");
const service = spawn(
  path.join(root, ".venv-backend/Scripts/python.exe"),
  [
    "-B",
    "-X",
    "utf8",
    path.join(root, "backend/tests/serve_https_fixture.py"),
    "--caddy",
    caddy,
    "--directory",
    directory,
  ],
  { cwd: root, windowsHide: true, stdio: ["ignore", log.fd, log.fd] },
);
const stopped = new Promise((resolve) => service.once("exit", resolve));
let browser;
try {
  browser = await chromium.launch({
    channel: "msedge",
    args: [
      "--host-resolver-rules=MAP bridge.example.test 127.0.0.1",
      "--no-proxy-server",
    ],
  });
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  const origin = "https://bridge.example.test:19443";
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (service.exitCode !== null)
      throw new Error("Acceptance server exited; see services.log");
    try {
      await page.goto(origin + "/login", { timeout: 1000 });
      ready = true;
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  assert.ok(ready, "HTTPS server ready");
  await page.getByLabel("后台用户名").waitFor();
  await page.screenshot({
    path: path.join(directory, "login-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(directory, "login-mobile.png"),
    fullPage: true,
  });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-https-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/accounts");
  const cookie = (await context.cookies()).find(
    (value) => value.name === "__Host-bridge_session",
  );
  assert.ok(cookie?.secure && cookie.httpOnly && cookie.sameSite === "Strict");
  assert.equal(cookie.path, "/");
  assert.equal(cookie.domain, "bridge.example.test");
  const websocket = await page.evaluate(
    () =>
      new Promise((resolve) => {
        const ws = new WebSocket(`wss://${location.host}/api/v1/events`);
        const timer = setTimeout(() => {
          ws.close();
          resolve("timeout");
        }, 3000);
        ws.onopen = () =>
          ws.send(JSON.stringify({ type: "subscribe", account_ids: [] }));
        ws.onmessage = (event) => {
          clearTimeout(timer);
          ws.close();
          resolve(JSON.parse(event.data).type);
        };
        ws.onerror = () => {
          clearTimeout(timer);
          resolve("error");
        };
      }),
  );
  assert.equal(websocket, "ready");
  await page.reload();
  await page.getByRole("button", { name: "退出登录" }).waitFor();
  const documents = await page.evaluate(async () => {
    const response = await fetch("/api/v1/../docs");
    return response.text();
  });
  assert.ok(!documents.includes("swagger-ui"));
  await page.getByRole("button", { name: "退出登录" }).click();
  await page.waitForURL("**/login?**");
  await page.goto(origin + "/workspace/synthetic/logs");
  await page.waitForURL("**/login?**");
  console.log(
    "PASS: Caddy template validation, static/deep routes, HTTPS Secure host cookie, login/refresh/logout, WSS, mobile layout.",
  );
  console.log("Screenshots and isolated service logs: " + directory);
} finally {
  await browser?.close();
  await writeFile(path.join(directory, "stop.request"), "stop\n");
  const timer = setTimeout(() => service.kill(), 20_000);
  await stopped;
  clearTimeout(timer);
  await log.close();
}
