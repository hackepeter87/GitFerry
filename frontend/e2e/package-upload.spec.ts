import { expect, test } from "@playwright/test";

const ready = {
  upload_id: "upload-01234567-89ab-cdef-0123-456789abcdef", status: "ready", archive_size: 2048, archive_sha256: "a".repeat(64), package_ref: "pkg.synthetic", package_digest: "b".repeat(64), error_code: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", summary: { package_id: "pkg.synthetic", sender: { instance_key: "source.synthetic", installation_marker: "install-source" }, recipient: { instance_key: "target.synthetic", installation_marker: "install-target" }, repositories: ["synthetic-org/repo"], metadata_present: true, inventory_complete: false, limitations: ["Files only; identity approval is separate"] },
};

async function open(page: import("@playwright/test").Page, list: unknown[] = []): Promise<void> {
  await page.route("**/api/v1/auth/status", (route) => route.fulfill({ json: { bootstrapRequired: false } }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { authenticated: true, principal: { username: "synthetic.admin", role: "admin" } } }));
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/exports/recipients", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/exports", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/package-uploads", (route) => route.fulfill({ json: list }));
  await page.route("**/api/v1/packages", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Upload a migration package" })).toBeVisible();
}

test("submits raw archive with immutable ID and size header", async ({ page }) => {
  let request: import("@playwright/test").Request | undefined;
  await page.route("**/api/v1/package-uploads/upload-*", async (route) => {
    request = route.request(); await route.fulfill({ json: { ...ready, upload_id: new URL(route.request().url()).pathname.split("/").at(-1), status: "validating", summary: null } });
  });
  await open(page);
  await page.getByLabel("Package archive (.zip)").setInputFiles({ name: "package.zip", mimeType: "application/zip", buffer: Buffer.from("synthetic zip") });
  await page.getByRole("button", { name: "Send package" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status")).toContainText("Package received");
  expect(request?.method()).toBe("PUT");
  expect(await request?.headerValue("content-type")).toBe("application/zip");
  expect(await request?.headerValue("x-gitferry-archive-size")).toBe(String(Buffer.from("synthetic zip").length));
  expect(new URL(request!.url()).pathname).toMatch(/^\/api\/v1\/package-uploads\/upload-[0-9a-f-]{36}$/);
});

test("reloads with GET only and shows ready files-only scope", async ({ page }) => {
  const methods: string[] = [];
  await page.route("**/api/v1/package-uploads", async (route) => { methods.push(route.request().method()); await route.fulfill({ json: [ready] }); });
  await page.route("**/api/v1/package-uploads/upload-01234567-89ab-cdef-0123-456789abcdef", async (route) => { methods.push(route.request().method()); await route.fulfill({ json: ready }); });
  await open(page, [ready]);
  await page.getByRole("button", { name: /upload-01234567-89ab-cdef-0123-456789abcdef/ }).click();
  await expect(page.getByText("The archive files have been validated.")).toBeVisible();
  await expect(page.getByText("synthetic-org/repo", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: test.info().outputPath("package-upload-01234567-89ab-cdef-0123-456789abcdef-narrow.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.reload();
  expect(methods).not.toContain("PUT"); expect(methods).not.toContain("POST");
});

test("resumes failed validation with the same ID and displays server conflict", async ({ page }) => {
  const failed = { ...ready, upload_id: "upload-failed", status: "failed", summary: null, error_code: "archive-conflict" };
  let resumed = false;
  await page.route("**/api/v1/package-uploads/upload-failed", async (route) => { await route.fulfill({ json: resumed ? { ...ready, upload_id: "upload-failed" } : failed }); });
  await page.route("**/api/v1/package-uploads/upload-failed/resume", async (route) => { resumed = true; await route.fulfill({ status: 409, json: { error: { code: "upload_conflict", message: "archive-conflict" } } }); });
  await open(page, [failed]);
  await page.getByRole("button", { name: /upload-failed/ }).click();
  await expect(page.getByRole("alert").first()).toContainText("archive-conflict");
  await page.getByRole("button", { name: "Resume validation" }).click();
  await expect(page.getByRole("alert").first()).toContainText("archive-conflict");
  expect(resumed).toBe(true);
});


test("a second completed file receives a new upload ID", async ({ page }) => {
  const ids: string[] = [];
  await page.route("**/api/v1/package-uploads/upload-*", async (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1)!;
    ids.push(id); await route.fulfill({ json: { ...ready, upload_id: id } });
  });
  await open(page);
  for (const name of ["first.zip", "second.zip"]) {
    await page.getByLabel("Package archive (.zip)").setInputFiles({ name, mimeType: "application/zip", buffer: Buffer.from("synthetic zip") });
    await page.getByRole("button", { name: "Send package" }).click();
    await expect(page.getByRole("status")).toContainText("received and validated");
    await expect(page.getByRole("button", { name: "Send package" })).toBeDisabled();
  }
  expect(ids).toHaveLength(2); expect(ids[0]).not.toBe(ids[1]);
});

test("lost reply preserves ID across reload and retries only on explicit submission", async ({ page }) => {
  const ids: string[] = [];
  await page.route("**/api/v1/package-uploads/upload-*", async (route) => {
    ids.push(new URL(route.request().url()).pathname.split("/").at(-1)!);
    if (ids.length === 1) await route.abort("failed");
    else await route.fulfill({ json: { ...ready, upload_id: ids[0] } });
  });
  await open(page);
  const archive = { name: "retry.zip", mimeType: "application/zip", buffer: Buffer.from("synthetic zip") };
  await page.getByLabel("Package archive (.zip)").setInputFiles(archive);
  await page.getByRole("button", { name: "Send package" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.reload();
  await expect(page.getByText(/Saved file: retry.zip/)).toBeVisible();
  expect(ids).toHaveLength(1);
  await page.getByLabel("Package archive (.zip)").setInputFiles(archive);
  await page.getByRole("button", { name: "Send package" }).click();
  await expect(page.getByRole("status")).toContainText("received and validated");
  expect(ids).toEqual([ids[0], ids[0]]);
});

test("late details cannot replace the current selection", async ({ page }) => {
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/package-uploads/upload-old", async (route) => {
    await delayed; await route.fulfill({ json: { ...ready, upload_id: "upload-old" } });
  });
  await page.route("**/api/v1/package-uploads/upload-01234567-89ab-cdef-0123-456789abcdef", (route) => route.fulfill({ json: ready }));
  await open(page, [{ ...ready, upload_id: "upload-old" }, ready]);
  await page.getByRole("button", { name: /upload-old/ }).click();
  await page.getByRole("button", { name: /upload-01234567-89ab-cdef-0123-456789abcdef/ }).click();
  await expect(page.getByRole("heading", { name: "upload-01234567-89ab-cdef-0123-456789abcdef", exact: true })).toBeVisible();
  const oldReply = page.waitForResponse("**/api/v1/package-uploads/upload-old");
  release(); await oldReply;
  await expect(page.getByRole("heading", { name: "upload-01234567-89ab-cdef-0123-456789abcdef", exact: true })).toBeVisible();
});
