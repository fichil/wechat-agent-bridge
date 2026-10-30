import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "*-integration.spec.ts",
  outputDir: "../.local/ai-config-acceptance/test-results",
  workers: 1,
  timeout: 30_000,
  use: {
    ...devices["Desktop Chrome"],
    channel: "msedge",
    baseURL: "http://127.0.0.1:15173",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        "..\\.venv-backend\\Scripts\\python.exe -B -X utf8 ..\\backend\\tests\\serve_ai_fixture.py",
      url: "http://127.0.0.1:18765/api/v1/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command:
        "node node_modules/vite/bin/vite.js --config e2e/vite.integration.config.ts",
      url: "http://127.0.0.1:15173",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
