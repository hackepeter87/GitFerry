import { expect, test, type Page } from "@playwright/test";

const source = { instance_key: "source-a", display_name: "Synthetic source A", revision: 7, enabled: true, locally_approved: true };
const classes = ["git_refs", "lfs", "wiki", "organizations", "teams", "permissions", "identities", "issues", "labels", "comments", "attachments", "releases", "release_assets", "pull_requests", "pull_comments", "reviews", "review_comments", "timelines", "reactions", "milestones", "other_content"];
const supported = ["git_refs", "organizations", "teams", "permissions", "identities", "issues", "comments"];

function documentFor(id: string, requested: string[], blocked: boolean) {
  return { format: "gitferry.content-inventory", schema_version: 1, comparison_version: 1, snapshot_id: id, created_at: "2026-01-01T00:00:00Z", source_instance_key: source.instance_key, source_installation_marker: "install-source-a", gitea_version: "1.27.2", deletion_semantics: "absence-does-not-delete", repositories: [{ repository: "synthetic-org/first", native_id: "101", requested_classes: requested, git_size_estimate_bytes: 2048, size_assumptions: [], classes: classes.map((content_class) => ({ content_class, status: requested.includes(content_class) ? blocked && content_class === "lfs" ? "not-supported" : "observed" : "excluded", count: requested.includes(content_class) && !(blocked && content_class === "lfs") ? 0 : null, objects: [], reason: requested.includes(content_class) ? blocked && content_class === "lfs" ? "Transfer for this class is not implemented" : "" : "Not requested", limitations: [] })) }] };
}

async function open(page: Page): Promise<void> {
  let lastRequest: { requested_classes: string[] } | undefined;
  const jobs = new Map<string, ReturnType<typeof makeJob>>();
  await page.route("**/api/v1/auth/status", (route) => route.fulfill({ json: { bootstrapRequired: false } }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { authenticated: true, principal: { username: "synthetic.admin", role: "admin" } } }));
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [source] }));
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/package-uploads**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/connections/source-a/browse", async (route) => {
    const body = route.request().postDataJSON() as { kind: string };
    await route.fulfill({ json: { instance_key: source.instance_key, profile_revision: source.revision, evidence_digest: "synthetic-evidence", kind: body.kind, organization: body.kind === "repositories" ? "synthetic-org" : null, page: 1, entries: body.kind === "organizations" ? [{ id: "42", name: "synthetic-org" }] : [{ id: "101", repository: "synthetic-org/first", private: false }], next_page: null, inventory_complete: false } });
  });
  await page.route("**/api/v1/inventories/capabilities", (route) => route.fulfill({ json: { classes, supported, schema_version: 1 } }));
  await page.route("**/api/v1/inventories", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    const body = route.request().postDataJSON() as { inventory_id: string; request: { requested_classes: string[] } };
    lastRequest = body.request;
    const blocked = body.request.requested_classes.includes("lfs");
    const value = makeJob(body.inventory_id, blocked ? "blocked" : "queued", body.request.requested_classes);
    jobs.set(body.inventory_id, value);
    return route.fulfill({ status: 202, json: value });
  });
  await page.route("**/api/v1/inventories/inventory-*", (route) => {
    const id = route.request().url().split("/").at(-1) ?? "inventory-test";
    const saved = jobs.get(id);
    const value = saved?.status === "blocked" ? saved : makeJob(id, "ready", saved?.request.requested_classes ?? lastRequest?.requested_classes ?? supported);
    jobs.set(id, value);
    return route.fulfill({ json: value });
  });
  await page.route("**/api/v1/exports/recipients", (route) => route.fulfill({ json: [{ instance_key: "recipient-b", display_name: "Synthetic recipient B", installation_marker: "install-recipient-b" }] }));
  await page.route("**/api/v1/exports", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await page.getByLabel("Source connection").selectOption(source.instance_key);
  await page.getByLabel("Organization", { exact: true }).selectOption("synthetic-org");
  await page.getByRole("checkbox", { name: "synthetic-org/first" }).check();
  await expect(page.getByRole("heading", { name: "Content inventory" })).toBeVisible();
}

function makeJob(id: string, status: "queued" | "ready" | "blocked", requested: string[]) {
  const blocked = status === "blocked";
  const document = documentFor(id, requested, blocked);
  return { inventory_id: id, request: { source_instance_key: source.instance_key, source_profile_revision: source.revision, repositories: [{ repository: "synthetic-org/first", global_id: "101" }], requested_classes: requested }, status, document: status === "queued" ? null : document, document_digest: status === "queued" ? null : "d".repeat(64), summary: status === "queued" ? null : { inventory_complete: !blocked, transfer_ready: !blocked, full_transfer: false, blockers: blocked ? [{ repository: "synthetic-org/first", content_class: "lfs", reason: "Transfer for this class is not implemented" }] : [], excluded_classes: [], known_payload_bytes: 0, objects_without_size: 0, temporary_space_assumption: "Synthetic estimate" }, error_code: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}

test("invalidates approval when the selected classes or options change", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Inspect selected content" }).click();
  await expect(page.getByRole("button", { name: "Use this inventory" })).toBeEnabled();
  await page.getByRole("button", { name: "Use this inventory" }).click();
  await page.getByLabel("LFS objects").check();
  await expect(page.getByRole("button", { name: "Use this inventory" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Create and queue export" })).toBeDisabled();
  await page.getByLabel("Include issues and normal comments").uncheck();
  await expect(page.getByRole("button", { name: "Use this inventory" })).toBeDisabled();
});

test("inspects unsupported content but does not allow exporting a blocked inventory", async ({ page }) => {
  await open(page);
  await page.getByLabel("LFS objects").check();
  await page.getByRole("button", { name: "Inspect selected content" }).click();
  await expect(page.getByRole("status")).toContainText("not transfer-ready");
  await expect(page.getByRole("button", { name: "Use this inventory" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Create and queue export" })).toBeDisabled();
});
