import type { Route } from "@playwright/test";

export async function mockAuthenticatedSession(route: Route, path: string) {
  if (path === "/api/v1/updates/query") {
    await route.fulfill({
      json: {
        mode: "source",
        current_version: "source",
        current_commit: "source",
        can_install: false,
        releases_url: "https://github.com/fichil/wechat-agent-bridge/releases",
      },
    });
    return true;
  }
  if (path === "/api/v1/compatibility/query") {
    await route.fulfill({
      json: {
        enforced: false,
        held: false,
        reason: "",
        message: "合成测试不访问微信",
        migration_pending: false,
        accounts: [],
        unresolved_instances: [],
      },
    });
    return true;
  }
  if (path !== "/api/v1/auth/session") return false;
  await route.fulfill({
    json: {
      authenticated: true,
      initialized: true,
      username: "admin",
      csrf_token: "synthetic-csrf",
      expires_at: new Date(Date.now() + 86400000).toISOString(),
    },
  });
  return true;
}
