import { expect, test, type Page } from "@playwright/test";

async function signIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Username").fill("synthetic.admin");
  await page.getByLabel("Password").fill("correct synthetic passphrase");
  const response = page.waitForResponse((value) => value.url().endsWith("/api/v1/auth/login"));
  await page.getByRole("button", { name: "Sign in" }).click();
  if ((await response).status() === 401) {
    await page.getByLabel("Password").fill("replacement synthetic passphrase");
    await page.getByRole("button", { name: "Sign in" }).click();
  }
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();
}

const plan = {
  plan_id: "a".repeat(64), digest: "b".repeat(64), target_instance_key: "target.synthetic",
  profile_revision: 2, evidence_digest: "c".repeat(64), round_key: "review-round", status: "draft",
  cancel_requested: false, reason: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  preview: { generation: 1, plan_digest: "b".repeat(64), status: "passed", reason: null, scope: "synthetic-org/synthetic-repo", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
  document: { required_bytes: 67121152, inventory: { request: { repositories: [{ organization: "synthetic-org", repository: "synthetic-org/synthetic-repo" }] }, inventory_complete: false, limitations: ["Selected explicit inputs only; not a complete remote inventory"] } },
  operations: [{ operation_id: "op.synthetic", operation_type: "create_repository", target_repository_scope: "synthetic-org/synthetic-repo", status: "prepared", target_global_id: null, error_reason: null }],
};

test.describe("transfer plan review (mocked API)", () => {
  test("renders scope, evidence, limitations, and queue action", async ({ page }) => {
    let current = { ...plan };
    await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [current] }));
    await page.route(/\/api\/v1\/plans\/a[^?]*$/, async (route) => {
      if (route.request().url().endsWith("/queue")) {
        expect(route.request().postDataJSON()).toEqual({ digest: plan.digest, preview_generation: 1 });
        current = { ...current, status: "queued" };
      } else if (route.request().url().endsWith("/cancel")) {
        current = { ...current, status: "cancelled" };
      }
      await route.fulfill({ json: current });
    });
    await signIn(page);
    await expect(page.getByRole("heading", { name: "Transfer plans" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Selected repository scope" }).getByText("synthetic-org/synthetic-repo", { exact: true })).toBeVisible();
    await expect(page.getByText("This is an explicit selection, not a complete source inventory.")).toBeVisible();
    await expect(page.locator(".storage-estimate")).toHaveText("67,121,152 bytes");
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.getByText("Connection and plan verification details", { exact: true }).click();
    await expect(page.getByText("Approved identity evidence")).toBeVisible();
    const start = page.getByRole("button", { name: "Start reviewed plan" });
    await start.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator(".plan-detail-heading")).toContainText("Queued");
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "Cancel plan" }).click();
    await expect(page.locator(".plan-detail-heading")).toContainText("Cancelled");
  });

  test("shows an empty state when no plans exist", async ({ page }) => {
    await page.route("**/api/v1/plans", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
    await signIn(page);
    await expect(page.getByText(/No transfer plans are available/)).toBeVisible();
  });
});

test("real backend/simulated remote: queues, survives browser close, and confirms", async ({ page, context }) => {
  const seeded = await page.request.post("http://127.0.0.1:8089/__test__/seed-plan");
  expect(seeded.ok()).toBeTruthy();
  const seededPlan = await seeded.json();
  await signIn(page);
  await expect(page.getByRole("button", { name: "Start reviewed plan" })).toBeDisabled();
  await page.getByRole("button", { name: "Request prerequisite preview" }).click();
  await expect(page.locator(".preview-box dd").filter({ hasText: /^passed$/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start reviewed plan" })).toBeEnabled();
  expect(await (await page.request.get("http://127.0.0.1:8089/__test__/plan-gate")).json()).toEqual({ started: false });
  await page.getByRole("button", { name: "Start reviewed plan" }).focus();
  await page.keyboard.press("Enter");
  await expect.poll(async () => (await page.request.get("http://127.0.0.1:8089/__test__/plan-gate")).json()).toMatchObject({ started: true });
  await page.close();
  const released = await context.request.post("http://127.0.0.1:8089/__test__/release-plan");
  expect(released.ok()).toBeTruthy();
  const reopened = await context.newPage();
  await reopened.goto("/");
  await expect(reopened.getByRole("heading", { name: "browser-disconnect" })).toBeVisible();
  await expect(reopened.locator(".plan-detail-heading")).toContainText("Confirmed");
  const plans = await reopened.request.get("/api/v1/plans");
  const result = (await plans.json()).find((item: { plan_id: string }) => item.plan_id === seededPlan.plan_id);
  expect(result.status).toBe("confirmed");
  expect(result.operations).toHaveLength(1);
  expect(result.operations[0].status).toBe("confirmed");
  await reopened.getByLabel("New plan round").fill("browser-copy");
  await reopened.getByRole("button", { name: "Prepare draft", exact: true }).click();
  await expect(reopened.getByRole("heading", { name: "browser-copy", exact: true })).toBeVisible();
  await expect(reopened.locator(".plan-detail-heading")).toContainText("Draft");
  const copied = (await (await reopened.request.get("/api/v1/plans")).json()).find((item: { round_key: string }) => item.round_key === "browser-copy");
  expect(copied.plan_id).not.toBe(seededPlan.plan_id);
  expect(copied.operations[0].operation_id).toBe(result.operations[0].operation_id);
  expect(copied.operations[0].status).toBe("confirmed");
  await reopened.locator(".plans-panel").screenshot({ path: test.info().outputPath("plans.png") });
  await reopened.close();
});


test("mocked unresolved parent and child stay distinct and cannot be requeued", async ({ page }) => {
  const blocked = { ...plan, status: "blocked", operations: [
    { ...plan.operations[0], operation_type: "create_issue", status: "outcome_unknown" },
    { ...plan.operations[0], operation_id: "child", operation_type: "create_comment", status: "blocked_parent" },
  ] };
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [blocked] }));
  await page.route(/\/api\/v1\/plans\/a[^?]*$/, (route) => route.fulfill({ json: blocked }));
  await signIn(page);
  await expect(page.getByText("Reconciliation required — may already be written", { exact: true })).toBeVisible();
  await expect(page.getByText("Waiting for parent — not sent", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open the operator recovery guide" })).toHaveAttribute("href", /docs\/transfer-plans\.md#recovery$/);
  await expect(page.getByRole("button", { name: "Start reviewed plan" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Cancel plan" })).toHaveCount(0);
});

test("mocked delayed detail cannot replace the newly selected plan", async ({ page }) => {
  const next = { ...plan, plan_id: "d".repeat(64), round_key: "newly-selected" };
  let releaseFirst!: () => void;
  const held = new Promise<void>((resolve) => { releaseFirst = resolve; });
  let firstReads = 0;
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [plan, next] }));
  await page.route(/\/api\/v1\/plans\/a[^?]*$/, async (route) => {
    if (++firstReads > 1) await held;
    await route.fulfill({ json: plan });
  });
  await page.route(/\/api\/v1\/plans\/d[^?]*$/, (route) => route.fulfill({ json: next }));
  await signIn(page);
  await expect(page.getByRole("heading", { name: "review-round", exact: true })).toBeVisible();
  await page.getByRole("navigation", { name: "Transfer plans" }).getByRole("button", { name: /review-round/ }).click();
  await page.getByRole("navigation", { name: "Transfer plans" }).getByRole("button", { name: /newly-selected/ }).click();
  await expect(page.getByRole("heading", { name: "newly-selected", exact: true })).toBeVisible();
  const staleResponse = page.waitForResponse((response) => response.url().endsWith(plan.plan_id));
  releaseFirst();
  await staleResponse;
  await expect(page.getByRole("heading", { name: "newly-selected", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "review-round", exact: true })).toHaveCount(0);
});

test("blocked prerequisite preview cannot queue a plan", async ({ page }) => {
  const blocked = { ...plan, preview: { ...plan.preview, status: "blocked", reason: "missing synthetic parent" } };
  let queued = false;
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [blocked] }));
  await page.route(/\/api\/v1\/plans\/a[^?]*$/, async (route) => {
    if (route.request().url().endsWith("/preview")) return route.fulfill({ json: blocked });
    if (route.request().url().endsWith("/queue")) queued = true;
    await route.fulfill({ json: blocked });
  });
  await signIn(page);
  await expect(page.getByRole("button", { name: "Start reviewed plan" })).toBeDisabled();
  await page.getByRole("button", { name: "Retry prerequisite preview" }).click();
  await expect(page.getByText("missing synthetic parent")).toBeVisible();
  expect(queued).toBe(false);
});

test("passes the exact preview generation and surfaces stale-generation conflicts", async ({ page }) => {
  let queueBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [plan] }));
  await page.route(/\/api\/v1\/plans\/a[^?]*$/, async (route) => {
    if (route.request().url().endsWith("/queue")) { queueBody = route.request().postDataJSON(); return route.fulfill({ status: 409, json: { error: { code: "plan_conflict", message: "Transfer plan cannot be queued. Refresh and review the plan and prerequisites." } } }); }
    await route.fulfill({ json: plan });
  });
  await signIn(page);
  await page.getByRole("button", { name: "Start reviewed plan" }).click();
  await expect(page.getByRole("alert")).toContainText("Refresh and review the plan and prerequisites.");
  expect(queueBody).toEqual({ digest: plan.digest, preview_generation: 1 });
});

test("reloads plans with GET only and does not request a preview", async ({ page }) => {
  const writes: string[] = [];
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [plan] }));
  await page.route(/\/api\/v1\/plans\/a[^?]*$/, async (route) => {
    if (route.request().method() !== "GET") writes.push(new URL(route.request().url()).pathname);
    await route.fulfill({ json: plan });
  });
  await signIn(page);
  await page.reload();
  expect(writes).toEqual([]);
});
