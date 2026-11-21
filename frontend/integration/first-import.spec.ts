import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Locator } from "@playwright/test";
import { writeFileSync } from "node:fs";

const archive = process.env.GITFERRY_INTEGRATION_ARCHIVE;
const evidencePath = process.env.GITFERRY_INTEGRATION_EVIDENCE;
const sourceDownload = process.env.GITFERRY_INTEGRATION_SOURCE_DOWNLOAD;
let secureSessionChecked = false;
let keyboardInteractions = 0;

async function tabTo(page: Page, control: Locator): Promise<void> {
  await expect(control).toBeVisible();
  await expect(control).toBeEnabled();
  for (let count = 0; count < 240; count++) {
    await page.keyboard.press("Tab");
    if (await control.evaluate((element) => element === document.activeElement)) {
      expect(await control.evaluate((element) => element.matches(":focus-visible") && parseFloat(getComputedStyle(element).outlineWidth) > 0)).toBe(true);
      keyboardInteractions++;
      return;
    }
  }
  throw new Error("Control is not reachable by bounded keyboard navigation");
}
async function activate(page: Page, control: Locator): Promise<void> {
  await tabTo(page, control);
  await page.keyboard.press("Enter");
}
async function enter(page: Page, control: Locator, value: string): Promise<void> {
  await tabTo(page, control);
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.insertText(value);
}
async function choose(page: Page, control: Locator, value: string): Promise<void> {
  const optionIndex = () => control.locator("option").evaluateAll((options, selected) => options.filter((option) => !(option as HTMLOptionElement).disabled).findIndex((option) => (option as HTMLOptionElement).value === selected), value);
  await expect.poll(optionIndex).toBeGreaterThanOrEqual(0);
  await tabTo(page, control);
  const optionCount = await control.locator("option").count();
  const label = await control.locator("option").evaluateAll((options, selected) => options.find((option) => (option as HTMLOptionElement).value === selected)?.textContent?.trim() ?? "", value);
  expect(label.length).toBeGreaterThan(0);
  // Native popup arrows are platform-dependent in Chromium. Printable type-ahead
  // cycles same-initial options on macOS and Linux; verify the actual value.
  for (let step = 0; step <= optionCount && await control.inputValue() !== value; step++) {
    await expect(control).toBeEnabled();
    if (!(await control.evaluate((element) => element === document.activeElement))) await tabTo(page, control);
    await page.keyboard.type(label[0]);
  }
  await page.keyboard.press("Tab");
  await expect(control).toHaveValue(value);
}
async function check(page: Page, control: Locator): Promise<void> {
  await tabTo(page, control);
  if (!(await control.isChecked())) await page.keyboard.press("Space");
  await expect(control).toBeChecked();
}

function record(value: Record<string, unknown>): void {
  if (!evidencePath) throw new Error("browser evidence path is required");
  writeFileSync(evidencePath, JSON.stringify({ status: "passed", secure_session: secureSessionChecked, keyboard_interactions: keyboardInteractions, ...value }));
}
async function login(page: Page): Promise<void> {
  expect((await page.request.get("/api/v1/plans")).status()).toBe(401);
  await page.goto("/");
  await enter(page, page.getByLabel("Username"), "synthetic.admin");
  await enter(page, page.getByLabel("Password", { exact: true }), "synthetic integration passphrase");
  await activate(page, page.getByRole("button", { name: "Sign in", exact: true }));
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
  await activate(page, page.locator(".package-row").first());
  const panel = page.locator(".package-admission-panel");
  await choose(page, panel.getByLabel("Locally approved target"), "target");
  await activate(page, panel.getByRole("button", { name: "Load prerequisites" }));
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
  await activate(page, card.getByRole("button", { name: "Test connection" }));
  await expect(card.locator(".state")).toHaveText("Approved");
  await page.getByLabel("Package archive (.zip)").setInputFiles(archive);
  await activate(page, page.getByRole("button", { name: "Send package" }));
  await expect(page.locator(".package-row .package-status")).toHaveText("Ready for review", { timeout: 30_000 });
  await expect(page.locator(".package-row")).toHaveCount(1);
  const uploadId = (await page.locator(".package-row strong").innerText()).trim();
  expect(uploadId).toMatch(/^upload-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  record({ uploaded: true, upload_id: uploadId });
});

test("phase0 selects an approved repository and downloads its export", async ({ page }) => {
  if (!sourceDownload) throw new Error("source download path is required");
  await login(page);
  await choose(page, page.getByLabel("Source connection"), "source");
  await expect(page.getByLabel("Organization", { exact: true })).toBeVisible();
  await choose(page, page.getByLabel("Organization", { exact: true }), "source-export-lab");
  for (const name of ["source-export-lab/selected", "source-export-lab/selected-extra"]) {
    const repository = page.locator(".browse-grid label").filter({ has: page.locator("span", { hasText: new RegExp(`^${name}$`) }) }).getByRole("checkbox");
    await expect(repository).toBeVisible();
    await check(page, repository);
  }
  await choose(page, page.getByLabel("Recipient registration"), "target");
  await check(page, page.getByLabel("Include membership records"));
  await check(page, page.getByLabel("Include Git LFS objects"));
  await check(page, page.getByLabel("Include native wiki history"));
  const inventoryQueuedResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/inventories");
  await activate(page, page.getByRole("button", { name: "Inspect selected content" }));
  const inventoryQueued = await inventoryQueuedResponse;
  expect(inventoryQueued.ok()).toBeTruthy();
  const inventoryId = (await inventoryQueued.json()).inventory_id;
  let inventoryState: Record<string, unknown> = {};
  await expect.poll(async () => {
    inventoryState = await (await page.request.get(`/api/v1/inventories/${inventoryId}`)).json();
    return inventoryState.status;
  }, { timeout: 120_000, intervals: [1000, 2000, 5000] }).toMatch(/^(ready|blocked|failed)$/);
  if (evidencePath) writeFileSync(`${evidencePath}.inventory.json`, JSON.stringify(inventoryState));
  expect(inventoryState.status, JSON.stringify(inventoryState.summary ?? inventoryState.error_code)).toBe("ready");
  await expect(page.getByRole("button", { name: "Use this inventory" })).toBeEnabled();
  await activate(page, page.getByRole("button", { name: "Use this inventory" }));
  const queuedResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/exports");
  await activate(page, page.getByRole("button", { name: "Create and queue export" }));
  const queued = await queuedResponse;
  expect(queued.ok()).toBeTruthy();
  const queuedJob = await queued.json();
  expect(queuedJob.request.version).toBe(3);
  expect(queuedJob.request.include_lfs).toBe(true);
  expect(queuedJob.request.include_wiki).toBe(true);
  expect(queuedJob.request.inventory_digest).toMatch(/^[0-9a-f]{64}$/);
  const row = page.locator(".export-list li").last();
  await expect(row.locator(".export-status")).toHaveText("ready", { timeout: 60_000 });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const downloadPromise = page.waitForEvent("download");
  const responsePromise = page.waitForResponse((response) => response.url().includes("/api/v1/exports/") && response.url().endsWith("/download"));
  await activate(page, row.getByRole("link", { name: "Download archive" }));
  const download = await downloadPromise;
  const response = await responsePromise;
  await download.saveAs(sourceDownload);
  record({ inventory_reviewed: true, inventory_request_version: queuedJob.request.version,
    inventory_id: queuedJob.request.inventory_id, inventory_digest: queuedJob.request.inventory_digest,
    source_capture: true, selected_repository: "source-export-lab/selected", downloaded: true,
    queue_status: queuedJob.status, export_id: queuedJob.export_id, download_header_sha256: response.headers()["x-archive-sha256"],
    download_header_bytes: Number(response.headers()["content-length"]) });
});

type PlanPreparationOptions = { structureOnly?: boolean; identityAdmin?: boolean };

async function bindThroughIdentityAdmin(page: Page, panel: Locator): Promise<void> {
  const claims = await panel.locator(".identity-row").evaluateAll((rows) => rows.map((row) => ({
    login: row.querySelector("strong")?.textContent?.trim() ?? "",
    roles: row.querySelector("p")?.textContent?.match(/roles: ([^\n]+)/)?.[1]?.split(", ").map((item) => item.trim()) ?? [],
    externalId: row.querySelector("p")?.textContent?.match(/external ID: ([^ ]+)/)?.[1] ?? "",
    ouPath: row.querySelector("p")?.textContent?.match(/OU: ([^·]+)/)?.[1]?.trim() ?? "",
    realm: row.querySelector("p")?.textContent?.match(/· ([^·]+) · external ID:/)?.[1]?.trim() ?? "",
  })).filter((claim) => claim.login));
  if (!claims.length) throw new Error("package did not expose identity claims for explicit binding");
  const identity = page.locator(".identity-panel");
  await activate(page, identity.getByRole("button", { name: "Manage identities" }));
  await expect(identity.getByLabel("Approved target instance")).toBeVisible();
  await choose(page, identity.getByLabel("Approved target instance"), "target");
  for (const claim of claims) {
    const targetLogin = claim.login === "issue-author" ? "ldap-reader" : "ldap-writer";
    await enter(page, identity.getByLabel("Target login"), targetLogin);
    await enter(page, identity.getByLabel("Source login"), claim.login);
    await enter(page, identity.getByLabel("External ID"), claim.externalId);
    await enter(page, identity.getByLabel("OU path (optional)"), claim.ouPath);
    const realm = identity.getByLabel("Registered realm");
    await choose(page, realm, claim.realm);
    const directorySource = targetLogin === "ldap-writer" ? "synthetic-openldap-second" : "synthetic-openldap-simple";
    await choose(page, identity.getByLabel("Configured directory claim source"), directorySource);
    await enter(page, identity.getByLabel("Directory login"), "ldap-reader");
    await enter(page, identity.getByLabel("Expected Gitea auth source ID"), "");
    const credential = identity.getByLabel("Credential reference (optional)");
    await enter(page, credential, targetLogin);
    for (const [label, enabled] of [["Issue author", claim.roles.includes("author")], ["Comment author", claim.roles.includes("author")], ["Team member", claim.roles.includes("member")]] as const) {
      const checkbox = identity.getByLabel(label);
      if (enabled !== await checkbox.isChecked()) { await tabTo(page, checkbox); await page.keyboard.press("Space"); }
    }
    await activate(page, identity.getByRole("button", { name: "Check target account" }));
    await expect(identity.getByText(new RegExp(`Target account checked:.*${targetLogin}`))).toBeVisible();
    await activate(page, identity.getByRole("button", { name: "Submit for approval" }));
    const candidate = identity.locator(".identity-card", { hasText: `${claim.login} → ${targetLogin}` });
    await expect(candidate).toBeVisible();
    await activate(page, candidate.getByRole("button", { name: "Approve" }));
    await expect(candidate.getByText("approved", { exact: true })).toBeVisible();
  }
}

async function configureDirectory(page: Page): Promise<void> {
  const panel = page.locator(".directory-panel");
  await activate(page, panel.getByRole("button", { name: "Manage directories" }));
  const saveSource = async (sourceName: string, realm: string, base: string, newSettings: boolean): Promise<void> => {
    if (newSettings) await activate(page, panel.getByRole("button", { name: "New settings" }));
    await choose(page, panel.getByLabel("Approved target connection"), "target");
    await enter(page, panel.getByLabel("Source name"), sourceName);
    await choose(page, panel.getByLabel("Registered realm"), realm);
    await enter(page, panel.getByLabel("Directory host"), "openldap");
    await enter(page, panel.getByLabel("Directory port"), "636");
    await enter(page, panel.getByLabel("User DN template"), `uid=%s,${base}`);
    await enter(page, panel.getByLabel("User filter"), "(&(objectClass=posixAccount)(uid=%s)(!(employeeNumber=DISABLED)))");
    await enter(page, panel.getByLabel("CA secret reference"), "directory.pem");
    await check(page, panel.getByLabel("Enable claim lookup"));
    await enter(page, panel.getByLabel("Claim search base"), base);
    await enter(page, panel.getByLabel("Claim login attribute"), "uid");
    await enter(page, panel.getByLabel("Claim external ID attribute"), "employeeNumber");
    await enter(page, panel.getByLabel("Claim email attribute"), "mail");
    await enter(page, panel.getByLabel("Claim group search base"), "ou=Groups,dc=example,dc=invalid");
    await enter(page, panel.getByLabel("Claim group member attribute"), "member");
    await activate(page, panel.getByRole("button", { name: "Save reviewed settings" }));
    await expect(panel).toContainText("Directory settings reviewed and saved locally.");
  };
  await saveSource("synthetic-openldap-simple", "SYNTHETIC-OPENLDAP", "ou=People,dc=example,dc=invalid", false);
  await saveSource("synthetic-openldap-second", "SYNTHETIC-OPENLDAP-B", "ou=People-B,dc=example,dc=invalid", true);
}

async function registerDirectoryRealm(page: Page): Promise<void> {
  const identity = page.locator(".identity-panel");
  await activate(page, identity.getByRole("button", { name: "Manage identities" }));
  for (const [key, name, marker] of [["SYNTHETIC-OPENLDAP", "Synthetic OpenLDAP", "synthetic-openldap-fixture"], ["SYNTHETIC-OPENLDAP-B", "Synthetic OpenLDAP B", "synthetic-openldap-fixture-b"]] as const) {
    await enter(page, identity.getByLabel("Realm key"), key);
    await enter(page, identity.getByLabel("Display name"), name);
    await enter(page, identity.getByLabel("External marker"), marker);
    await activate(page, identity.getByRole("button", { name: "Register realm" }));
    await expect(identity).toContainText("Identity realm registered.");
  }
}

async function prepareTargetPlan(page: Page, options: PlanPreparationOptions = {}): Promise<{ blocked: boolean; missing: string | null }> {
  await login(page);
  if (options.identityAdmin) {
    await registerDirectoryRealm(page);
    await configureDirectory(page);
  }
  let panel = await intake(page);
  if (await panel.getByRole("button", { name: "Confirm sender locally" }).isVisible()) {
  await enter(page, panel.getByLabel(/Local sender display name/), "Captured source");
  await enter(page, panel.getByLabel(/Local sender base URL/), "https://source.invalid");
  await activate(page, panel.getByRole("button", { name: "Confirm sender locally" }));
  await expect(panel.getByRole("button", { name: "Confirm sender locally" })).toHaveCount(0);
  }
  const realm = panel.locator(".mapping-row");
  if (await realm.getByRole("button", { name: "Confirm realm locally" }).isVisible()) {
  await expect(realm).toHaveCount(1);
  await enter(page, realm.getByLabel("Display name", { exact: true }), "Observed native source accounts");
  await enter(page, realm.getByLabel("External marker"), "locally-reviewed-source-realm");
  await activate(page, realm.getByRole("button", { name: "Confirm realm locally" }));
  await expect(realm.getByRole("button", { name: "Confirm realm locally" })).toHaveCount(0);

  }

  if (options.structureOnly) {
    const metadata = panel.getByLabel("Include metadata");
    if (await metadata.isChecked()) { await tabTo(page, metadata); await page.keyboard.press("Space"); }
  } else {
    await check(page, panel.getByLabel("Apply team/member changes"));
    await activate(page, panel.getByRole("button", { name: "Admit package locally" }));
    await activate(page, panel.getByRole("button", { name: "Review plan selection" }));
    await expect(panel.getByRole("heading", { name: "Plan review", exact: true })).toBeVisible();
    await expect(panel.locator(".coverage")).toContainText("Content inventory coverage");
  }
  let blocked = false;
  let missing: string | null = null;
  if (!options.structureOnly) {
    await expect(panel.getByRole("button", { name: "Prepare draft for Transfer plans" })).toBeDisabled();
    blocked = await panel.getByRole("button", { name: "Prepare draft for Transfer plans" }).isDisabled();
    missing = await panel.locator("dt", { hasText: "Missing bindings" }).locator("..").textContent();
    expect(missing).not.toBe("Missing bindingsNone");
  }

  if (options.identityAdmin) {
    await page.reload();
    panel = await intake(page);
    await bindThroughIdentityAdmin(page, panel);
    await page.reload();
    panel = await intake(page);
    for (const [name, targetLogin] of [["issue-author", "ldap-reader"], ["comment-author", "ldap-writer"]] as const) {
      const row = panel.locator(".identity-row").filter({ has: page.locator("strong", { hasText: new RegExp(`^${name}$`) }) });
      await expect(row).toContainText(`Already bound to ${targetLogin}`);
      await enter(page, row.getByLabel(/Author credential reference/), targetLogin);
    }
  } else if (!options.structureOnly) {
    // Immediate package-local binding remains covered for the collision preparation path.
    for (const name of ["issue-author", "comment-author"]) {
      const row = panel.locator(".identity-row").filter({ has: page.locator("strong", { hasText: new RegExp(`^${name}$`) }) });
      await expect(row).toHaveCount(1);
      await enter(page, row.getByLabel("Target login", { exact: true }), name);
      await enter(page, row.getByLabel(/Author credential reference/), name);
      await activate(page, row.getByRole("button", { name: "Check target account" }));
      await expect(row.getByRole("button", { name: "Confirm binding", exact: true })).toBeEnabled();
      await activate(page, row.getByRole("button", { name: "Confirm binding", exact: true }));
      await expect(row).toContainText(`Already bound to ${name}`);
    }
  }
  if (!options.structureOnly) {
    await check(page, panel.getByLabel("Include metadata"));
    await check(page, panel.getByLabel("Apply team/member changes"));
  }
  if (options.structureOnly) {
    await expect(panel.getByLabel("Include metadata")).not.toBeChecked();
    await expect(panel.getByLabel("Apply team/member changes")).not.toBeChecked();
  }
  await activate(page, panel.getByRole("button", { name: "Admit package locally" }));
  await activate(page, panel.getByRole("button", { name: "Review plan selection" }));
  await expect(panel.getByRole("button", { name: "Prepare draft for Transfer plans" })).toBeEnabled();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await activate(page, panel.getByRole("button", { name: "Prepare draft for Transfer plans" }));
  return { blocked, missing };
}

test("phase2 confirms local decisions and previews the real first import", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  const { blocked, missing } = await prepareTargetPlan(page, { identityAdmin: true });
  const plans = page.locator(".plans-panel");
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeDisabled();
  await activate(page, plans.getByRole("button", { name: "Request prerequisite preview" }));
  await expect(plans.locator(".preview-box dd").filter({ hasText: /^passed$/ })).toBeVisible({ timeout: 60_000 });
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeEnabled();
  const savedPlans = await (await page.request.get("/api/v1/plans")).json();
  expect(savedPlans).toHaveLength(2);
  const fullPlan = savedPlans.find((plan: { document: { inventory?: { request?: { metadata?: unknown[] } } } }) => Array.isArray(plan.document.inventory?.request?.metadata) && plan.document.inventory.request.metadata.length > 0)!;
  expect(fullPlan).toBeDefined();
  const requiredBytes = fullPlan.document.required_bytes;
  expect(requiredBytes).toBeGreaterThan(64 * 1024 * 1024);
  await expect(plans.locator(".storage-estimate")).toHaveText(`${requiredBytes.toLocaleString()} bytes`);
  const layout = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth, viewport: window.innerWidth,
    overflow: [...document.querySelectorAll("body *")].map((element) => ({
      tag: element.tagName, id: element.id, class: element.className,
      right: element.getBoundingClientRect().right,
    })).filter((element) => element.right > window.innerWidth).slice(0, 30),
  }));
  expect(layout.width, JSON.stringify(layout)).toBeLessThanOrEqual(layout.viewport);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  record({ storage_estimate_reviewed: true, preview_ready: true, review_missing_bindings: Boolean(missing && !missing.endsWith("None")),
    blocked_without_bindings: blocked });
});

test("structure phase executes Git-only before identity administration bindings", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await prepareTargetPlan(page, { structureOnly: true });
  const plans = page.locator(".plans-panel");
  await activate(page, plans.getByRole("button", { name: "Request prerequisite preview" }));
  await expect(plans.locator(".preview-box dd").filter({ hasText: /^passed$/ })).toBeVisible({ timeout: 60_000 });
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeEnabled();
  await activate(page, plans.getByRole("button", { name: "Start reviewed plan" }));
  await expect(plans.locator(".plan-detail-heading")).toContainText("Confirmed", { timeout: 60_000 });
  record({ structure_only: true, structure_plan_confirmed: true });
});

test("phase5 executes the reviewed real first import", async ({ page }) => {
  await login(page);
  const plans = page.locator(".plans-panel");
  const draft = plans.locator(".plan-row").filter({ has: page.locator('.plan-status[data-status="draft"]') });
  await expect(draft).toHaveCount(1);
  await activate(page, draft);
  await expect(plans.locator(".preview-box dd").filter({ hasText: /^passed$/ })).toBeVisible();
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeEnabled();
  await activate(page, plans.getByRole("button", { name: "Start reviewed plan" }));
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
  await expect(page.locator('.plan-row .plan-status[data-status="confirmed"]')).toHaveCount(2);
  await activate(page, page.locator(".plan-row").last());
  await expect(page.locator(".plan-detail-heading")).toContainText("Confirmed");
  await expect(page.locator(".operation-list")).toContainText("create comment");
  await expect(page.locator(".package-row")).toHaveCount(1);
  await page.reload();
  await expect(page.locator('.plan-row .plan-status[data-status="confirmed"]')).toHaveCount(2);
  await activate(page, page.locator(".plan-row").last());
  await expect(page.locator(".plan-detail-heading")).toContainText("Confirmed");
  expect(mutations).toEqual([]);
  record({ read_only: true });
});


test("phase4 rejects an occupied target before execution", async ({ page }) => {
  await prepareTargetPlan(page);
  const plans = page.locator(".plans-panel");
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeDisabled();
  await activate(page, plans.getByRole("button", { name: "Request prerequisite preview" }));
  await expect(plans.locator(".preview-box dd").filter({ hasText: /^blocked$/ })).toBeVisible({ timeout: 60_000 });
  await expect(plans.locator(".preview-box")).toContainText("structure:");
  await expect(plans.getByRole("button", { name: "Start reviewed plan" })).toBeDisabled();
  const conflict = plans.locator(".operation-list li").filter({ hasText: "Conflict — review required" });
  await expect(conflict).toHaveCount(1);
  await activate(page, conflict.getByText("Operation details", { exact: true }));
  await expect(conflict).toContainText("already exists but is not bound to this origin");
  await page.setViewportSize({ width: 1440, height: 900 });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  record({ collision_blocked: true });
});

test("phase6 reopens and approves restored directory conflicts", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await login(page);
  const identity = page.locator(".identity-panel");
  await activate(page, identity.getByRole("button", { name: "Manage identities" }));
  await expect(identity.getByRole("heading", { name: "Identity administration" })).toBeVisible();

  const bindings = [
    ["issue-author", "ldap-reader"],
    ["comment-author", "ldap-writer"],
  ] as const;
  for (const [sourceLogin, targetLogin] of bindings) {
    const card = identity.locator(".identity-card", { hasText: `${sourceLogin} → ${targetLogin}` });
    await expect(card).toHaveCount(1);
    await expect(card.getByText("conflict", { exact: true })).toBeVisible();
    await expect(card).toContainText("Target directory evidence");
    await expect(card).toContainText("native source");
    await enter(page, card.getByLabel(`Credential reference for ${sourceLogin}`), targetLogin);
    await activate(page, card.getByRole("button", { name: "Reopen" }));
    await expect(card.getByText("pending", { exact: true })).toBeVisible();
    await expect(card.getByRole("button", { name: "Approve" })).toBeEnabled();
    await activate(page, card.getByRole("button", { name: "Approve" }));
    await expect(card.getByText("approved", { exact: true })).toBeVisible();
    await activate(page, card.getByRole("button", { name: "View events" }));
    await expect(card.getByLabel("Candidate events")).toContainText("approved");
  }

  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.reload();
  await activate(page, page.locator(".identity-panel").getByRole("button", { name: "Manage identities" }));
  const persisted = page.locator(".identity-panel");
  for (const [sourceLogin, targetLogin] of bindings) {
    const card = persisted.locator(".identity-card", { hasText: `${sourceLogin} → ${targetLogin}` });
    await expect(card.getByText("approved", { exact: true })).toBeVisible();
    await expect(card).toContainText("Target directory evidence");
  }
  expect(keyboardInteractions).toBeGreaterThan(0);
  record({ status: "passed", explicit_review: bindings.length, persisted_approved: bindings.length });
});
