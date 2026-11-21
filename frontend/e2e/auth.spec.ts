import { spawnSync } from "node:child_process";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

function contrastRatio(foreground: string, background: string): number {
  const luminance = (value: string): number => {
    const channels = value.match(/[\d.]+/g)?.slice(0, 3).map(Number);
    if (!channels || channels.length !== 3) throw new Error(`unsupported color: ${value}`);
    const linear = channels.map((channel) => {
      const normalized = channel / 255;
      return normalized <= 0.04045
        ? normalized / 12.92
        : ((normalized + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
  };
  const lighter = Math.max(luminance(foreground), luminance(background));
  const darker = Math.min(luminance(foreground), luminance(background));
  return (lighter + 0.05) / (darker + 0.05);
}

test("shows an explicit bootstrap prerequisite without a default password", async ({ page }) => {
  await page.goto("http://127.0.0.1:4174");

  await expect(page.getByRole("heading", { name: "Administrator bootstrap required" })).toBeVisible();
  await expect(page.getByText("--password-file")).toBeVisible();
  await expect(page.getByLabel("Password")).toHaveCount(0);
});

test("keeps login fields readable with the operating system dark preference", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  const colors = await page.getByLabel("Username").evaluate((input) => {
    const style = getComputedStyle(input);
    return { foreground: style.color, background: style.backgroundColor };
  });

  expect(contrastRatio(colors.foreground, colors.background)).toBeGreaterThanOrEqual(4.5);
});

test("supports keyboard login, denied credentials, authenticated state, and logout", async ({
  page,
}) => {
  await page.goto("/");
  const denied = await page.request.post("/api/v1/blueprints/validate", {
    data: "apiVersion: gitferry.io/v1alpha1\n",
    headers: { "content-type": "application/yaml" },
  });
  expect(denied.status()).toBe(401);

  const username = page.getByLabel("Username");
  const password = page.getByLabel("Password");

  await username.focus();
  await page.keyboard.press("Tab");
  await expect(password).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeFocused();

  await username.fill("synthetic.admin");
  await password.fill("wrong synthetic passphrase");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Invalid credentials");
  await expect(password).toBeFocused();

  await password.fill("correct synthetic passphrase");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Administration", exact: true, level: 1 })).toBeVisible();
  await expect(page.getByText("synthetic.admin")).toBeVisible();
  await expect(page.getByText("Administrator", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to GitFerry" })).toBeVisible();
});

test("surfaces an expired server-side session without granting access", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Username").fill("synthetic.admin");
  await page.getByLabel("Password").fill("correct synthetic passphrase");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Administration", exact: true, level: 1 })).toBeVisible();

  // Expire at the explicit request boundary: startup panel reads may otherwise
  // correctly sign out before the test can click the session-check button.
  await page.route("**/api/v1/auth/session", async (route) => {
    const expired = await page.request.post("http://127.0.0.1:8089/__test__/expire-sessions");
    expect(expired.ok()).toBeTruthy();
    const response = await route.fetch();
    expect(response.status()).toBe(401);
    await route.fulfill({ response });
  }, { times: 1 });
  await page.getByRole("button", { name: "Check session" }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "Your session expired or was revoked. Sign in again.",
  );
  await expect(page.getByLabel("Username")).toBeFocused();
});

test("surfaces revoked sessions and accepts the operator recovery password", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Username").fill("synthetic.admin");
  await page.getByLabel("Password").fill("correct synthetic passphrase");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Administration", exact: true, level: 1 })).toBeVisible();

  const runtimeRoot = process.env.GITFERRY_E2E_ROOT;
  if (!runtimeRoot) throw new Error("GITFERRY_E2E_ROOT is unavailable");
  const python = process.env.GITFERRY_TEST_PYTHON;
  const adminCli = process.env.GITFERRY_TEST_ADMIN_CLI;
  if (!python || !adminCli) throw new Error("browser test CLI paths are unavailable");
  await page.route("**/api/v1/auth/session", async (route) => {
    const recovery = spawnSync(python, [
      adminCli,
      "admin",
      "recover",
      "--database",
      join(runtimeRoot, "bootstrapped", "state.sqlite3"),
      "--password-file",
      join(runtimeRoot, "bootstrapped", "replacement-password"),
    ], { env: process.env, encoding: "utf8" });
    expect(
      recovery.status,
      `recovery failed: stdout=${recovery.stdout} stderr=${recovery.stderr}`,
    ).toBe(0);
    const response = await route.fetch();
    expect(response.status()).toBe(401);
    await route.fulfill({ response });
  }, { times: 1 });

  await page.getByRole("button", { name: "Check session" }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "Your session expired or was revoked. Sign in again.",
  );

  await page.getByLabel("Username").fill("synthetic.admin");
  await page.getByLabel("Password").fill("correct synthetic passphrase");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Invalid credentials");

  await page.getByLabel("Password").fill("replacement synthetic passphrase");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Administration", exact: true, level: 1 })).toBeVisible();
});
