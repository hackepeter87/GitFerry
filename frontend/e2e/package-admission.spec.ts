import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const ready = { upload_id: "upload-01234567-89ab-cdef-0123-456789abcdef", status: "ready", archive_size: 2048, archive_sha256: "a".repeat(64), package_ref: "pkg.synthetic", package_digest: "b".repeat(64), error_code: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", summary: { package_id: "pkg.synthetic", sender: { instance_key: "source.synthetic", installation_marker: "install-source" }, recipient: { instance_key: "target.synthetic", installation_marker: "install-target" }, repositories: ["synthetic-org/repo"], metadata_present: true, inventory_complete: false, limitations: [] } };
const target = { instance_key: "target.synthetic", display_name: "Synthetic target", base_url: "https://target.synthetic", installation_marker: "install-target", credential_ref: "target-ref", ca_ref: null, revision: 3, enabled: true, locally_approved: true };
const prerequisites = { package_id: "pkg.synthetic", digest_sha256: "b".repeat(64), target_instance_key: "target.synthetic", profile_revision: 3, sender: { instance_key: "source.synthetic", installation_marker: "install-source", registered: true }, realms: [{ realm_key: "gitea", display_name: "Gitea", external_marker: "gitea", registered: true }], identities: [{ selector: { realm_key: "gitea", login: "author", external_id: "42", ou_path: null }, roles: ["author"], binding: { binding_id: "binding-1", target_login: "author", target_account_id: "9001", mapping_version: 1 } }], repositories: ["synthetic-org/repo"], bundle_bytes: 4096, metadata_present: true, limitations: [] };

async function open(page: import("@playwright/test").Page, overrides: { connections?: unknown[]; uploads?: unknown[] } = {}): Promise<void> {
  await page.route("**/api/v1/auth/status", (route) => route.fulfill({ json: { bootstrapRequired: false } }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { authenticated: true, principal: { username: "synthetic.admin", role: "admin" } } }));
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: overrides.connections ?? [target] }));
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/exports", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/exports/recipients", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/package-uploads", (route) => route.fulfill({ json: overrides.uploads ?? [ready] }));
  await page.route("**/api/v1/package-uploads/upload-01234567-89ab-cdef-0123-456789abcdef", (route) => route.fulfill({ json: ready }));
  await page.goto("/");
  await page.getByRole("button", { name: /upload-01234567/ }).click();
  await expect(page.getByRole("heading", { name: /Approve package pkg.synthetic/ })).toBeVisible();
}

test("admits a ready package and prepares a draft through plan review", async ({ page }) => {
  let planReviewCalls = 0;
  await page.route("**/api/v1/packages/pkg.synthetic/prerequisites", (route) => route.fulfill({ json: prerequisites }));
  await page.route("**/api/v1/packages/pkg.synthetic/review", (route) => route.fulfill({ json: { digest_sha256: prerequisites.digest_sha256 } }));
  await page.route("**/api/v1/packages/pkg.synthetic/admit", (route) => route.fulfill({ json: { status: "admitted-facts-only" } }));
  await page.route("**/api/v1/packages/pkg.synthetic/plan-review", (route) => { planReviewCalls++; return route.fulfill({ json: { ...prerequisites, review_digest: "review-1", selection: { request: { repositories: ["synthetic-org/repo"] }, missing_bindings: [], missing_credentials: [], waiting_parents: [], blocked_operations: [] } } }); });
  await page.route("**/api/v1/packages/pkg.synthetic/prepare-plan", (route) => route.fulfill({ json: { plan_id: "plan-1", digest: "plan-digest", target_instance_key: "target.synthetic", profile_revision: 3, evidence_digest: "e", round_key: "package-pkg.synthetic", document: { inventory: { request: { repositories: ["synthetic-org/repo"] } } }, status: "draft", cancel_requested: false, reason: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", operations: [] } }));
  await open(page);
  await page.getByLabel("Locally approved target").selectOption("target.synthetic");
  await page.getByRole("button", { name: "Load prerequisites" }).click();
  await expect(page.getByText(/bytes \(estimate, not full capacity\)/)).toBeVisible();
  await page.getByRole("button", { name: "Admit package locally" }).click();
  await page.getByRole("button", { name: "Review plan selection" }).click();
  await expect(page.getByText("Review digest: review-1")).toBeVisible();
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.getByRole("button", { name: "Prepare draft for Transfer plans" }).click();
  await expect(page.getByRole("heading", { name: "Transfer plans" })).toBeVisible();
  expect(planReviewCalls).toBe(1);
});

test("changed plan options invalidate the reviewed selection until explicitly reviewed again", async ({ page }) => {
  await page.route("**/api/v1/packages/pkg.synthetic/prerequisites", (route) => route.fulfill({ json: prerequisites }));
  await page.route("**/api/v1/packages/pkg.synthetic/review", (route) => route.fulfill({ json: { digest_sha256: prerequisites.digest_sha256 } }));
  await page.route("**/api/v1/packages/pkg.synthetic/admit", (route) => route.fulfill({ json: { status: "admitted-facts-only" } }));
  await page.route("**/api/v1/packages/pkg.synthetic/plan-review", (route) => route.fulfill({ json: { review_digest: "review-1", selection: { request: {}, missing_bindings: [], missing_credentials: [], waiting_parents: [], blocked_operations: [] } } }));
  await open(page); await page.getByLabel("Locally approved target").selectOption("target.synthetic"); await page.getByRole("button", { name: "Load prerequisites" }).click(); await page.getByRole("button", { name: "Admit package locally" }).click(); await page.getByRole("button", { name: "Review plan selection" }).click();
  await page.getByLabel("Include metadata").uncheck();
  await expect(page.getByText("Plan options changed. Review the selection again before preparing a draft.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare draft for Transfer plans" })).toHaveCount(0);
});

test("shows missing mapping blockers and routes auth expiry to sign-in", async ({ page }) => {
  const missing = { ...prerequisites, identities: [{ ...prerequisites.identities[0], binding: null }] };
  await page.route("**/api/v1/packages/pkg.synthetic/prerequisites", (route) => route.fulfill({ json: missing }));
  await open(page); await page.getByLabel("Locally approved target").selectOption("target.synthetic"); await page.getByRole("button", { name: "Load prerequisites" }).click();
  await page.route("**/api/v1/packages/pkg.synthetic/review", (route) => route.fulfill({ json: { digest_sha256: prerequisites.digest_sha256 } }));
  await page.route("**/api/v1/packages/pkg.synthetic/admit", (route) => route.fulfill({ json: { status: "admitted-facts-only" } }));
  await page.route("**/api/v1/packages/pkg.synthetic/plan-review", (route) => route.fulfill({ json: {
    review_digest: "review-missing", selection: { request: {}, missing_bindings: ["gitea/author"], missing_credentials: [], waiting_parents: [], blocked_operations: [] },
  } }));
  // Receipt/admission is not permission to write. Missing authors must block the executable plan.
  await page.getByRole("button", { name: "Admit package locally" }).click();
  await page.getByRole("button", { name: "Review plan selection" }).click();
  await expect(page.getByText("gitea/author", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare draft for Transfer plans" })).toBeDisabled();
  await page.route("**/api/v1/packages/pkg.synthetic/prerequisites", (route) => route.fulfill({ status: 401, json: { error: { code: "session_expired", message: "expired" } } }));
  await page.getByRole("button", { name: "Load prerequisites" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to GitFerry" })).toBeVisible();
});

test("sends an already-bound author's credential reference keyed by binding ID", async ({ page }) => {
  let reviewBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/packages/pkg.synthetic/prerequisites", (route) => route.fulfill({ json: prerequisites }));
  await page.route("**/api/v1/packages/pkg.synthetic/review", (route) => route.fulfill({ json: { digest_sha256: prerequisites.digest_sha256 } }));
  await page.route("**/api/v1/packages/pkg.synthetic/admit", (route) => route.fulfill({ json: { status: "admitted-facts-only" } }));
  await page.route("**/api/v1/packages/pkg.synthetic/plan-review", async (route) => { reviewBody = route.request().postDataJSON(); await route.fulfill({ json: { review_digest: "author-review", selection: { request: {}, missing_bindings: [], missing_credentials: [], waiting_parents: [], blocked_operations: [] } } }); });
  await open(page);
  await page.getByLabel("Locally approved target").selectOption("target.synthetic");
  await page.getByRole("button", { name: "Load prerequisites" }).click();
  await page.getByLabel(/Author credential reference/).fill("credential-ref-author");
  await page.getByRole("button", { name: "Admit package locally" }).click();
  await page.getByRole("button", { name: "Review plan selection" }).click();
  expect(reviewBody?.author_credential_refs).toEqual({ "binding-1": "credential-ref-author" });
});

test("allows structure and Git-only review without business mappings", async ({ page }) => {
  const structure = { ...prerequisites, identities: [], metadata_present: false };
  let reviewBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/packages/pkg.synthetic/prerequisites", (route) => route.fulfill({ json: structure }));
  await page.route("**/api/v1/packages/pkg.synthetic/review", (route) => route.fulfill({ json: { digest_sha256: structure.digest_sha256 } }));
  await page.route("**/api/v1/packages/pkg.synthetic/admit", (route) => route.fulfill({ json: { status: "admitted-facts-only" } }));
  await page.route("**/api/v1/packages/pkg.synthetic/plan-review", async (route) => { reviewBody = route.request().postDataJSON(); await route.fulfill({ json: { review_digest: "structure-review", selection: { request: { repositories: ["synthetic-org/repo"] }, missing_bindings: [], missing_credentials: [], waiting_parents: [], blocked_operations: [] } } }); });
  await page.route("**/api/v1/packages/pkg.synthetic/prepare-plan", (route) => route.fulfill({ json: { plan_id: "structure-plan", digest: "structure-digest", target_instance_key: "target.synthetic", profile_revision: 3, evidence_digest: "e", round_key: "structure", document: { inventory: { request: {} } }, status: "draft", cancel_requested: false, reason: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", operations: [] } }));
  await open(page); await page.getByLabel("Locally approved target").selectOption("target.synthetic"); await page.getByRole("button", { name: "Load prerequisites" }).click();
  await page.getByRole("button", { name: "Admit package locally" }).click();
  await page.getByLabel("Include metadata").uncheck(); await page.getByLabel("Apply team/member changes").uncheck();
  await page.getByRole("button", { name: "Review plan selection" }).click();
  await expect(page.getByText("structure-review")).toBeVisible();
  expect(reviewBody?.include_metadata).toBe(false); expect(reviewBody?.apply_members).toBe(false);
  await page.getByRole("button", { name: "Prepare draft for Transfer plans" }).click();
  await expect(page.getByText(/Draft prepared\. It is now available/)).toBeVisible();
});

test("keeps OU-distinct identity forms independent", async ({ page }) => {
  const ouDistinct = { ...prerequisites, identities: [
    { ...prerequisites.identities[0], selector: { ...prerequisites.identities[0].selector, ou_path: "engineering" }, binding: null },
    { ...prerequisites.identities[0], selector: { ...prerequisites.identities[0].selector, ou_path: "sales" }, binding: null },
  ] };
  await page.route("**/api/v1/packages/pkg.synthetic/prerequisites", (route) => route.fulfill({ json: ouDistinct }));
  await open(page); await page.getByLabel("Locally approved target").selectOption("target.synthetic"); await page.getByRole("button", { name: "Load prerequisites" }).click();
  const targets = page.getByLabel("Target login");
  await expect(targets).toHaveCount(2); await targets.nth(0).fill("engineering-alice"); await targets.nth(1).fill("sales-alice");
  await expect(targets.nth(0)).toHaveValue("engineering-alice"); await expect(targets.nth(1)).toHaveValue("sales-alice");
});

test("does not let stale prerequisites keep a changed package busy", async ({ page }) => {
  const second = { ...ready, upload_id: "upload-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", package_ref: "pkg.second", package_digest: "c".repeat(64) };
  let release!: () => void;
  let started!: () => void;
  const requested = new Promise<void>((resolve) => { started = resolve; });
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/package-uploads/upload-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", (route) => route.fulfill({ json: second }));
  await page.route("**/api/v1/packages/pkg.synthetic/prerequisites", async (route) => { started(); await delayed; await route.fulfill({ json: prerequisites }); });
  await page.route("**/api/v1/packages/pkg.second/prerequisites", (route) => route.fulfill({ json: { ...prerequisites, package_id: "pkg.second", digest_sha256: second.package_digest } }));
  await open(page, { uploads: [ready, second] }); await page.getByLabel("Locally approved target").selectOption("target.synthetic"); await page.getByRole("button", { name: "Load prerequisites" }).click();
  await requested;
  await page.getByRole("button", { name: /upload-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/ }).click();
  await expect(page.getByRole("heading", { name: /Approve package pkg.second/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Load prerequisites" })).toBeEnabled();
  const staleResponse = page.waitForResponse("**/api/v1/packages/pkg.synthetic/prerequisites");
  release();
  await staleResponse;
  await expect(page.locator(".package-admission-panel .admission-content")).toHaveCount(0);
  await page.getByLabel("Locally approved target").selectOption("target.synthetic");
  await page.getByRole("button", { name: "Load prerequisites" }).click();
  await expect(page.locator(".package-admission-panel")).toContainText(second.package_digest);
});
