import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";

const archive = process.env.GITFERRY_INTEGRATION_ARCHIVE;
const evidencePath = process.env.GITFERRY_INTEGRATION_EVIDENCE;

function record(value: Record<string, unknown>): void {
  if (!evidencePath) throw new Error("browser evidence path is required");
  writeFileSync(evidencePath, JSON.stringify({ status: "passed", ...value }));
}
async function login(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Username").fill("synthetic.admin");
  await page.getByLabel("Password", { exact: true }).fill("synthetic integration passphrase");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Administration", exact: true })).toBeVisible();
}
async function intake(page: Page): Promise<import("@playwright/test").Locator> {
  await page.locator(".package-row").first().click();
  const panel = page.locator(".package-admission-panel");
  await panel.getByLabel("Locally approved target").selectOption("target");
  await panel.getByRole("button", { name: "Load prerequisites" }).click();
  await expect(panel.getByRole("heading", { name: "Sender proposal" })).toBeVisible();
  return panel;
}

test("phase1 receives the actual source archive without admitting identities", async ({ page }) => {
  if (!archive) throw new Error("actual archive is required");
  await login(page);
  // A portable, non-secret target configuration is imported via the normal UI.
  // Its locally known installation key matches the source's explicitly selected recipient.
  const configuration = { format: "gitferry-connections", version: 1, profiles: [{
    instance_key: "target", display_name: "Gitea target", base_url: "https://localhost:34443",
    installation_marker: "target-export-install", credential_ref: "gitferry-admin", ca_ref: "trusted.pem",
  }] };
  await page.locator(".connections-panel input[type=file]").setInputFiles({
    name: "synthetic-target.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(configuration)),
  });
  const card = page.locator(".connection-card", { hasText: "Gitea target" });
  await expect(card).toContainText("Approval pending");
  page.once("dialog", (dialog) => dialog.accept());
  await card.getByRole("button", { name: "Test connection" }).click();
  await expect(card.locator(".state")).toHaveText("Approved");
  await page.getByLabel("Package archive (.zip)").setInputFiles(archive);
  await page.getByRole("button", { name: "Send package" }).click();
  await expect(page.locator(".package-row .package-status")).toHaveText("Ready for review", { timeout: 30_000 });
  await expect(page.locator(".package-row")).toHaveCount(1);
  const uploadId = (await page.locator(".package-row strong").innerText()).trim();
  expect(uploadId).toMatch(/^upload-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  record({ uploaded: true, upload_id: uploadId });
});

test("phase2 confirms local decisions and executes the real first import", async ({ page }) => {
  await login(page);
  const panel = await intake(page);
  await panel.getByLabel(/Local sender display name/).fill("Captured source");
  await panel.getByLabel(/Local sender base URL/).fill("https://source.invalid");
  await panel.getByRole("button", { name: "Confirm sender locally" }).click();
  await expect(panel.getByRole("button", { name: "Confirm sender locally" })).toHaveCount(0);
  const realm = panel.locator(".mapping-row");
  await expect(realm).toHaveCount(1);
  await realm.getByLabel("Display name", { exact: true }).fill("Observed native source accounts");
  await realm.getByLabel("External marker").fill("locally-reviewed-source-realm");
  await realm.getByRole("button", { name: "Confirm realm locally" }).click();
  await expect(realm.getByRole("button", { name: "Confirm realm locally" })).toHaveCount(0);

  await panel.getByLabel("Apply team/member changes").check();
  await panel.getByRole("button", { name: "Admit package locally" }).click();
  await panel.getByRole("button", { name: "Review plan selection" }).click();
  await expect(panel.getByRole("heading", { name: "Plan review", exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Prepare draft for Transfer plans" })).toBeDisabled();
  const blocked = await panel.getByRole("button", { name: "Prepare draft for Transfer plans" }).isDisabled();
  const missing = await panel.locator("dt", { hasText: "Missing bindings" }).locator("..").textContent();
  expect(missing).not.toBe("Missing bindingsNone");

  // Explicit synthetic allowlist. The application never derives bindings from equal names.
  for (const name of ["issue-author", "comment-author"]) {
    const row = panel.locator(".identity-row").filter({ has: page.locator("strong", { hasText: new RegExp(`^${name}$`) }) });
    await expect(row).toHaveCount(1);
    await row.getByLabel("Target login", { exact: true }).fill(name);
    await row.getByLabel(/Author credential reference/).fill(name);
    await row.getByRole("button", { name: "Check target account" }).click();
    await expect(row.getByRole("button", { name: "Confirm binding", exact: true })).toBeEnabled();
    await row.getByRole("button", { name: "Confirm binding", exact: true }).click();
    await expect(row).toContainText(`Already bound to ${name}`);
  }
  await panel.getByRole("button", { name: "Admit package locally" }).click();
  await panel.getByRole("button", { name: "Review plan selection" }).click();
  await expect(panel.getByRole("button", { name: "Prepare draft for Transfer plans" })).toBeEnabled();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await panel.getByRole("button", { name: "Prepare draft for Transfer plans" }).click();
  const plans = page.locator(".plans-panel");
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeEnabled();
  await plans.getByRole("button", { name: "Start reviewed plan" }).focus();
  await page.keyboard.press("Enter");
  await expect(plans.locator(".plan-detail-heading")).toContainText("Confirmed", { timeout: 60_000 });
  await page.screenshot({ path: test.info().outputPath("real-first-import-confirmed.png"), fullPage: true });
  record({ confirmed: true, review_missing_bindings: Boolean(missing && !missing.endsWith("None")),
    blocked_without_bindings: blocked });
});

test("phase3 observes the confirmed plan after an application process restart", async ({ page }) => {
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET" && /\/api\/v1\/(packages|package-uploads|plans)(\/|$)/.test(new URL(request.url()).pathname)) mutations.push(request.url());
  });
  await login(page);
  await expect(page.locator(".plan-detail-heading")).toContainText("Confirmed");
  await expect(page.locator(".package-row")).toHaveCount(1);
  await page.reload();
  await expect(page.locator(".plan-detail-heading")).toContainText("Confirmed");
  expect(mutations).toEqual([]);
  record({ read_only: true });
});
