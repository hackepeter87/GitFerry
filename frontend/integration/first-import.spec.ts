import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";

const archive = process.env.GITFERRY_INTEGRATION_ARCHIVE;
const evidencePath = process.env.GITFERRY_INTEGRATION_EVIDENCE;
const sourceDownload = process.env.GITFERRY_INTEGRATION_SOURCE_DOWNLOAD;
let secureSessionChecked = false;

function record(value: Record<string, unknown>): void {
  if (!evidencePath) throw new Error("browser evidence path is required");
  writeFileSync(evidencePath, JSON.stringify({ status: "passed", secure_session: secureSessionChecked, ...value }));
}
async function login(page: Page): Promise<void> {
  expect((await page.request.get("/api/v1/plans")).status()).toBe(401);
  await page.goto("/");
  await page.getByLabel("Username").fill("synthetic.admin");
  await page.getByLabel("Password", { exact: true }).fill("synthetic integration passphrase");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Administration", exact: true })).toBeVisible();
  const cookies = await page.context().cookies();
  const session = cookies.find((cookie) => cookie.name === "__Host-gitferry_session");
  expect(session).toMatchObject({ secure: true, httpOnly: true, sameSite: "Strict", path: "/" });
  expect(await page.evaluate(async () => (await fetch("/api/v1/auth/logout", {
    method: "POST", headers: { "content-type": "application/json" }, body: "{}",
  })).status)).toBe(403);
  secureSessionChecked = true;
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

test("phase0 selects an approved repository and downloads its export", async ({ page }) => {
  if (!sourceDownload) throw new Error("source download path is required");
  await login(page);
  await page.getByLabel("Source connection").selectOption("source");
  await expect(page.getByLabel("Organization")).toBeVisible();
  await page.getByLabel("Organization").selectOption("source-export-lab");
  for (const name of ["source-export-lab/selected", "source-export-lab/selected-extra"]) {
    const repository = page.locator(".browse-grid label").filter({ has: page.locator("span", { hasText: new RegExp(`^${name}$`) }) }).getByRole("checkbox");
    await expect(repository).toBeVisible();
    await repository.check();
  }
  await page.getByLabel("Recipient registration").selectOption("target");
  await page.getByLabel("Include membership records").check();
  const queuedResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/exports");
  await page.getByRole("button", { name: "Create and queue export" }).click();
  const queued = await queuedResponse;
  expect(queued.ok()).toBeTruthy();
  const queuedJob = await queued.json();
  const row = page.locator(".export-list li").last();
  await expect(row.locator(".export-status")).toHaveText("ready", { timeout: 60_000 });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const downloadPromise = page.waitForEvent("download");
  const responsePromise = page.waitForResponse((response) => response.url().includes("/api/v1/exports/") && response.url().endsWith("/download"));
  await row.getByRole("link", { name: "Download archive" }).click();
  const download = await downloadPromise;
  const response = await responsePromise;
  await download.saveAs(sourceDownload);
  record({ source_capture: true, selected_repository: "source-export-lab/selected", downloaded: true,
    queue_status: queuedJob.status, export_id: queuedJob.export_id, download_header_sha256: response.headers()["x-archive-sha256"],
    download_header_bytes: Number(response.headers()["content-length"]) });
});

async function prepareTargetPlan(page: Page): Promise<{ blocked: boolean; missing: string | null }> {
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
  return { blocked, missing };
}

test("phase2 confirms local decisions and previews the real first import", async ({ page }) => {
  const { blocked, missing } = await prepareTargetPlan(page);
  const plans = page.locator(".plans-panel");
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeDisabled();
  await plans.getByRole("button", { name: "Request prerequisite preview" }).click();
  await expect(plans.locator(".preview-box dd").filter({ hasText: /^passed$/ })).toBeVisible({ timeout: 60_000 });
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeEnabled();
  record({ preview_ready: true, review_missing_bindings: Boolean(missing && !missing.endsWith("None")),
    blocked_without_bindings: blocked });
});

test("phase5 executes the reviewed real first import", async ({ page }) => {
  await login(page);
  const plans = page.locator(".plans-panel");
  await expect(plans.locator(".preview-box dd").filter({ hasText: /^passed$/ })).toBeVisible();
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeEnabled();
  await plans.getByRole("button", { name: "Start reviewed plan" }).focus();
  await page.keyboard.press("Enter");
  await expect(plans.locator(".plan-detail-heading")).toContainText("Confirmed", { timeout: 60_000 });
  await page.screenshot({ path: test.info().outputPath("real-first-import-confirmed.png"), fullPage: true });
  record({ confirmed: true });
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


test("phase4 rejects an occupied target before execution", async ({ page }) => {
  await prepareTargetPlan(page);
  const plans = page.locator(".plans-panel");
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeDisabled();
  await plans.getByRole("button", { name: "Request prerequisite preview" }).click();
  await expect(plans.locator(".preview-box dd").filter({ hasText: /^blocked$/ })).toBeVisible({ timeout: 60_000 });
  await expect(plans.locator(".preview-box")).toContainText("structure:");
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeDisabled();
  const conflict = plans.locator(".operation-list li").filter({ hasText: "Conflict — review required" });
  await expect(conflict).toHaveCount(1);
  await conflict.getByText("Operation details", { exact: true }).click();
  await expect(conflict).toContainText("already exists but is not bound to this origin");
  await page.setViewportSize({ width: 390, height: 844 });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  record({ collision_blocked: true });
});
