import { spawnSync } from "node:child_process";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

test("shows an explicit bootstrap prerequisite without a default password", async ({ page }) => {
  await page.goto("http://127.0.0.1:4174");

  await expect(page.getByRole("heading", { name: "Administrator bootstrap required" })).toBeVisible();
  await expect(page.getByText("--password-file")).toBeVisible();
  await expect(page.getByLabel("Password")).toHaveCount(0);
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
  await expect(page.getByRole("heading", { name: "Administration" })).toBeVisible();
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
  await expect(page.getByRole("heading", { name: "Administration" })).toBeVisible();

  const expired = await page.request.post("http://127.0.0.1:8089/__test__/expire-sessions");
  expect(expired.ok()).toBeTruthy();
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
  await expect(page.getByRole("heading", { name: "Administration" })).toBeVisible();

  const runtimeRoot = process.env.GITFERRY_E2E_ROOT;
  if (!runtimeRoot) throw new Error("GITFERRY_E2E_ROOT is unavailable");
  const python = process.env.GITFERRY_TEST_PYTHON;
  const adminCli = process.env.GITFERRY_TEST_ADMIN_CLI;
  if (!python || !adminCli) throw new Error("browser test CLI paths are unavailable");
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
  await expect(page.getByRole("heading", { name: "Administration" })).toBeVisible();
});
