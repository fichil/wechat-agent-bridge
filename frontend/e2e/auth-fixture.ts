import type { Route } from "@playwright/test";

export async function mockAuthenticatedSession(route: Route, path: string) {
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
