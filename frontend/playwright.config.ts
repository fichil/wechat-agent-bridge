import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: "*-integration.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  use: {
    ...devices["Desktop Chrome"],
    channel: "msedge",
    baseURL: "http://127.0.0.1:15373",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 15373",
    url: "http://127.0.0.1:15373",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
