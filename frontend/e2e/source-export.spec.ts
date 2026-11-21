import { expect, test, type Page } from "@playwright/test";

type Profile = { instance_key: string; display_name: string; revision: number; enabled: boolean; locally_approved: boolean };
const source: Profile = { instance_key: "source-a", display_name: "Synthetic source A", revision: 7, enabled: true, locally_approved: true };
const recipient = { instance_key: "recipient-b", display_name: "Synthetic recipient B", installation_marker: "install-recipient-b" };
const inventoryClasses = ["git_refs", "lfs", "wiki", "organizations", "teams", "permissions", "identities", "issues", "labels", "comments", "attachments", "releases", "release_assets", "pull_requests", "pull_comments", "reviews", "review_comments", "timelines", "reactions", "milestones", "other_content"];

function inventoryDocument(inventoryId: string, requestedClasses = ["git_refs", "organizations", "teams", "permissions", "identities", "issues", "comments"]) {
  return {
    format: "gitferry.content-inventory", schema_version: 1, comparison_version: 1, snapshot_id: inventoryId,
    created_at: "2026-01-01T00:00:00Z", source_instance_key: source.instance_key, source_installation_marker: "install-source-a", gitea_version: "1.27.2", deletion_semantics: "absence-does-not-delete",
    repositories: [{ repository: "synthetic-org/first", native_id: "101", requested_classes: requestedClasses, git_size_estimate_bytes: 2048, size_assumptions: [], classes: inventoryClasses.map((content_class) => ({ content_class, status: requestedClasses.includes(content_class) ? "observed" : "excluded", count: requestedClasses.includes(content_class) ? 0 : null, objects: [], reason: requestedClasses.includes(content_class) ? "" : "Not requested", limitations: [] })) }],
  };
}
function inventoryJob(inventoryId: string, status: "queued" | "running" | "ready" | "blocked" | "failed" = "ready") {
  const requestedClasses = ["git_refs", "organizations", "teams", "permissions", "identities", "issues", "comments"];
  const document = status === "ready" ? inventoryDocument(inventoryId, requestedClasses) : null;
  return { inventory_id: inventoryId, request: { source_instance_key: source.instance_key, source_profile_revision: source.revision, repositories: [{ repository: "synthetic-org/first", global_id: "101" }], requested_classes: requestedClasses }, status, document, document_digest: document ? "d".repeat(64) : null, summary: document ? { inventory_complete: true, transfer_ready: true, full_transfer: false, blockers: [], excluded_classes: [], known_payload_bytes: 0, objects_without_size: 0, temporary_space_assumption: "Synthetic test estimate" } : null, error_code: status === "failed" ? "inventory_failed" : null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}

function job(export_id: string, status: "prepared" | "queued" | "running" | "ready" | "failed" = "prepared") {
  return {
    export_id,
    request: { version: 2, inventory_id: "inventory-saved", inventory_digest: "d".repeat(64), source_instance_key: source.instance_key, source_profile_revision: source.revision, recipient_instance_key: recipient.instance_key,
      repositories: [{ repository: "synthetic-org/first", global_id: "101" }], include_metadata: true, include_members: false },
    capture_status: status === "ready" ? "captured" : status === "running" ? "capturing" : status,
    status, archive_sha256: status === "ready" ? "a".repeat(64) : null, archive_size: status === "ready" ? 1234 : null,
    error_code: status === "failed" ? "export-delivery-not-confirmed" : null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  };
}

async function openWorkspace(page: Page, exports: unknown[] = [], exportHandler?: (route: import("@playwright/test").Route) => Promise<unknown> | unknown, recipientHandler?: (route: import("@playwright/test").Route) => Promise<unknown> | unknown): Promise<void> {
  const inventoryJobs = new Map<string, ReturnType<typeof inventoryJob>>();
  await page.route("**/api/v1/auth/status", (route) => route.fulfill({ json: { bootstrapRequired: false } }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { authenticated: true, principal: { username: "synthetic.admin", role: "admin" } } }));
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [source] }));
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/package-uploads**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/connections/source-a/browse", async (route) => {
    const body = route.request().postDataJSON() as { kind: string };
    await route.fulfill({ json: { instance_key: source.instance_key, profile_revision: source.revision, evidence_digest: "synthetic-evidence", kind: body.kind,
      organization: body.kind === "repositories" ? "synthetic-org" : null, page: 1,
      entries: body.kind === "organizations" ? [{ id: "42", name: "synthetic-org" }] : [{ id: "101", repository: "synthetic-org/first", private: false }],
      next_page: null, inventory_complete: false } });
  });
  await page.route("**/api/v1/inventories/capabilities", (route) => route.fulfill({ json: { classes: inventoryClasses, supported: ["git_refs", "organizations", "teams", "permissions", "identities", "issues", "comments"], schema_version: 1 } }));
  await page.route("**/api/v1/inventories", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: [...inventoryJobs.values()].map(({ document, request, summary, ...summaryJob }) => summaryJob) });
    const body = route.request().postDataJSON() as { inventory_id: string };
    const created = inventoryJob(body.inventory_id, "queued"); inventoryJobs.set(body.inventory_id, created);
    return route.fulfill({ status: 202, json: created });
  });
  await page.route("**/api/v1/inventories/inventory-*", (route) => {
    const id = route.request().url().split("/").at(-1) ?? "inventory-saved";
    const value = inventoryJobs.get(id) ?? inventoryJob(id, "ready");
    return route.fulfill({ json: { ...value, ...(value.status === "queued" ? inventoryJob(id, "ready") : {}) } });
  });
  await page.route("**/api/v1/exports/recipients", recipientHandler ?? ((route) => route.fulfill({ json: [recipient, { ...source, installation_marker: "install-source" }] })));
  await page.route("**/api/v1/exports", async (route) => {
    if (exportHandler) return exportHandler(route);
    if (route.request().method() === "GET") await route.fulfill({ json: exports }); else await route.continue();
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Choose a source" })).toBeVisible();
  await page.getByLabel("Source connection").selectOption(source.instance_key);
  await page.getByLabel("Organization", { exact: true }).selectOption("synthetic-org");
  await page.getByRole("checkbox", { name: "synthetic-org/first" }).check();
  await expect(page.getByRole("heading", { name: "Content inventory" })).toBeVisible();
  const inspect = page.getByRole("button", { name: "Inspect selected content" });
  if (await inspect.isVisible()) await inspect.click();
  await expect(page.getByRole("button", { name: "Use this inventory" })).toBeEnabled({ timeout: 6_000 });
  await page.getByRole("button", { name: "Use this inventory" }).click();
  await expect(page.getByRole("heading", { name: "Create a source export" })).toBeVisible();
}

test("submits the exact immutable source selection and recipient", async ({ page }) => {
  let submitted: { export_id: string; request: Record<string, unknown> } | undefined;
  const exportHandler = async (route: import("@playwright/test").Route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: job(submitted.export_id, "queued") });
  };
  await openWorkspace(page, [], exportHandler);
  await page.getByLabel("Recipient registration").selectOption(recipient.instance_key);
  await page.getByLabel("Include membership records").check();
  await expect(page.getByRole("button", { name: "Create and queue export" })).toBeDisabled();
  await page.getByRole("button", { name: "Use this inventory" }).click();
  const create = page.getByRole("button", { name: "Create and queue export" });
  await create.focus(); await page.keyboard.press("Enter");
  await expect(page.getByRole("status").filter({ hasText: "was queued" })).toBeVisible();
  expect(submitted?.export_id).toMatch(/^export-[0-9a-f-]{36}$/);
  expect(submitted?.request).toMatchObject({ version: 2, inventory_digest: "d".repeat(64), source_instance_key: "source-a", source_profile_revision: 7,
    recipient_instance_key: "recipient-b", repositories: [{ repository: "synthetic-org/first", global_id: "101" }], include_metadata: true, include_members: true });
  expect(submitted?.request).toHaveProperty("inventory_id", expect.stringMatching(/^inventory-[0-9a-f-]{36}$/));
});

test("reloads saved jobs without POST and polls to a streamed download link", async ({ page }) => {
  const saved = job("export-saved", "queued"); let reads = 0; let posts = 0;
  const exportHandler = async (route: import("@playwright/test").Route) => {
    if (route.request().method() === "POST") { posts++; return route.continue(); }
    await route.fulfill({ json: [saved] });
  };
  await page.route("**/api/v1/exports/export-saved", async (route) => {
    reads++;
    await route.fulfill({ json: reads === 1 ? { ...saved, status: "running", capture_status: "capturing" } : { ...saved, status: "ready", capture_status: "captured", archive_sha256: "b".repeat(64), archive_size: 2048 } });
  });
  await openWorkspace(page, [], exportHandler);
  await expect(page.getByText("export-saved", { exact: true })).toBeVisible();
  await page.reload();
  // A saved download must remain available without reconnecting to a source.
  await expect(page.getByRole("heading", { name: "Create a source export" })).toBeVisible();
  await expect(page.getByText("export-saved", { exact: true })).toBeVisible();
  expect(posts).toBe(0);
  await expect(page.getByText(/SHA-256:/)).toBeVisible({ timeout: 6_000 });
  await expect(page.getByRole("link", { name: "Download archive" })).toHaveAttribute("href", "/api/v1/exports/export-saved/download");
  await page.screenshot({ path: test.info().outputPath("source-export-ready.png"), fullPage: true });
});

test("retries a lost create response after reload with the same immutable ID", async ({ page }) => {
  const submitted: Array<{ export_id: string; request: unknown }> = [];
  const remoteJobs = new Map<string, ReturnType<typeof job>>();
  const exportHandler = async (route: import("@playwright/test").Route) => {
    // A stale/unavailable listing must not be taken as proof that POST failed.
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    const body = route.request().postDataJSON();
    submitted.push(body);
    remoteJobs.set(body.export_id, job(body.export_id, "queued"));
    if (submitted.length === 1) return route.abort("failed");
    return route.fulfill({ status: 202, json: remoteJobs.get(body.export_id) });
  };
  await openWorkspace(page, [], exportHandler);
  await page.getByLabel("Recipient registration").selectOption(recipient.instance_key);
  await page.getByRole("button", { name: "Create and queue export" }).click();
  await expect(page.getByRole("alert")).toContainText("could not be queued");
  await page.reload();
  await page.getByLabel("Source connection").selectOption(source.instance_key);
  await page.getByLabel("Organization", { exact: true }).selectOption("synthetic-org");
  await page.getByRole("checkbox", { name: "synthetic-org/first" }).check();
  await expect(page.getByRole("heading", { name: "Content inventory" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Use this inventory" })).toBeEnabled({ timeout: 6_000 });
  await page.getByRole("button", { name: "Use this inventory" }).click();
  await page.getByLabel("Recipient registration").selectOption(recipient.instance_key);
  expect(submitted).toHaveLength(1); // Reload only reads durable state.
  await page.getByRole("button", { name: "Create and queue export" }).click();
  await expect(page.getByRole("status").filter({ hasText: "was queued" })).toBeVisible();
  expect(submitted).toHaveLength(2);
  expect(submitted[1]).toEqual(submitted[0]);
  expect(remoteJobs.size).toBe(1);
});

test("only explicitly queues prepared and failed jobs", async ({ page }) => {
  const prepared = job("export-prepared", "prepared"); const failed = job("export-failed", "failed"); const queued: string[] = [];
  const exportHandler = (route: import("@playwright/test").Route) => route.fulfill({ json: [prepared, failed] });
  await page.route("**/api/v1/exports/*/queue", async (route) => {
    queued.push(route.request().url().split("/").at(-2) ?? ""); await route.fulfill({ json: job(queued.at(-1)!, "queued") });
  });
  await openWorkspace(page, [prepared, failed], exportHandler);
  await expect(page.getByText("prepared, not queued", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Start export" }).click();
  await expect(page.getByRole("button", { name: "Retry this export" })).toBeEnabled();
  await page.getByRole("button", { name: "Retry this export" }).click();
  await expect.poll(() => queued).toEqual(["export-prepared", "export-failed"]);
});

test("returns to sign-in when recipient loading expires the session", async ({ page }) => {
  await page.route("**/api/v1/auth/status", (route) => route.fulfill({ json: { bootstrapRequired: false } }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { authenticated: true, principal: { username: "synthetic.admin", role: "admin" } } }));
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [source] }));
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/package-uploads**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/exports/recipients", (route) => route.fulfill({ status: 401, json: { error: { code: "authentication_required", message: "Session expired" } } }));
  await page.route("**/api/v1/exports", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in to GitFerry" })).toBeVisible();
});
