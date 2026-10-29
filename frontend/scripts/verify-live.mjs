// Explicit live UI acceptance. Only counts/statuses are saved; no account fields or avatars.
import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

if (!process.argv.includes("--run")) {
  console.log(
    "Start scripts/dev.ps1 -NoBrowser, then run: node frontend/scripts/verify-live.mjs --run",
  );
  process.exit(0);
}
const root = fileURLToPath(new URL("../../", import.meta.url));
const destination = resolve(root, ".local/framework-acceptance");
await mkdir(destination, { recursive: true });
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const failures = [];
page.on("pageerror", () => failures.push("browser_runtime_error"));
const report = {
  completed: false,
  accountCount: 0,
  enteredAccounts: 0,
  coverageComplete: false,
  avatarCount: 0,
  browserErrors: failures,
};
try {
  await page.goto("http://127.0.0.1:5173");
  await page.waitForFunction(
    () => !document.querySelector('[role="status"]'),
    {},
    { timeout: 230_000 },
  );
  const response = await page.request.post(
    "http://127.0.0.1:5173/api/v1/accounts/query",
    { data: {} },
  );
  if (!response.ok()) throw new Error("account_snapshot_missing");
  const snapshot = await response.json();
  report.accountCount = snapshot.accounts.filter(
    (a) => a.account_id && a.wxid,
  ).length;
  report.onlineAccounts = snapshot.accounts.filter(
    (a) => a.source_state === "online",
  ).length;
  report.coverageComplete = snapshot.summary.coverage_complete;
  report.avatarCount = snapshot.summary.avatar_available_accounts;
  report.exitCode = snapshot.exit_code;
  report.clientVersions = [
    ...new Set(snapshot.client_instances.map((instance) => instance.version)),
  ];
  if (snapshot.exit_code === 1) throw new Error("probe_failed");
  const selectable = page.getByRole("button", { name: "进入工作台" });
  const count = await selectable.count();
  if (count !== report.accountCount)
    throw new Error("account_card_count_mismatch");
  for (let index = 0; index < count; index++) {
    await selectable.nth(index).click();
    await page
      .getByRole("heading", { name: "账号概览", exact: true })
      .waitFor();
    await page.getByRole("link", { name: "Agent", exact: true }).click();
    await page
      .getByRole("heading", { name: "功能待接入", exact: true })
      .waitFor();
    await page.getByRole("button", { name: "切换账号" }).click();
    await page.waitForURL("**/accounts");
    report.enteredAccounts++;
  }
  report.completed = failures.length === 0;
  if (!report.completed) throw new Error("browser_runtime_error");
  console.log(JSON.stringify(report));
} catch (error) {
  report.error = [
    "account_snapshot_missing",
    "account_card_count_mismatch",
    "probe_failed",
    "browser_runtime_error",
  ].includes(error.message)
    ? error.message
    : "live_ui_acceptance_failed";
  console.error(report.error);
  process.exitCode = 1;
} finally {
  await writeFile(
    resolve(destination, `live-${Date.now()}.json`),
    JSON.stringify(report, null, 2) + "\n",
    { flag: "wx" },
  );
  await browser.close();
}
