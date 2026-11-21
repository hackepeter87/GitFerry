import { expect, test, type Page } from "@playwright/test";

type Profile = { instance_key: string; display_name: string; revision: number; enabled: boolean; locally_approved: boolean };

async function openMockedWorkspace(page: Page, profiles: Profile[] = [
  { instance_key: "source-a", display_name: "Synthetic source A", revision: 4, enabled: true, locally_approved: true },
  { instance_key: "disabled", display_name: "Disabled source", revision: 2, enabled: false, locally_approved: true },
], autoSelect = true): Promise<void> {
  await page.route("**/api/v1/auth/status", (route) => route.fulfill({ json: { bootstrapRequired: false } }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { authenticated: true, principal: { username: "synthetic.admin", role: "admin" } } }));
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: profiles }));
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/package-uploads**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/exports/recipients", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/exports", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/inventories", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/inventories/capabilities", (route) => route.fulfill({ json: { classes: ["git_refs", "organizations", "teams", "permissions"], supported: ["git_refs", "organizations", "teams", "permissions"], schema_version: 1 } }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Choose a source" })).toBeVisible();
  const selected = profiles.find((profile) => profile.enabled && profile.locally_approved);
  if (selected && autoSelect) await page.getByLabel("Source connection").selectOption(selected.instance_key);
}

test("selects repositories across mocked pages and preserves the summary", async ({ page }) => {
  const requests: Array<{ page: number; kind: string; organization: string | null; expected_revision: number }> = [];
  await page.route("**/api/v1/connections/source-a/browse", async (route) => {
    const body = route.request().postDataJSON() as { page: number; kind: string; organization: string | null; expected_revision: number };
    requests.push(body);
    const entries = body.kind === "organizations"
      ? [{ id: "42", name: "synthetic-org" }, { id: "77", name: "other-org" }]
      : body.organization === "other-org" ? [{ id: "9007199254740993", repository: "other-org/third", private: false }]
      : body.page === 1 ? [{ id: "101", repository: "synthetic-org/first", private: false }] : [{ id: "102", repository: "synthetic-org/second", private: true }];
    await route.fulfill({ json: { instance_key: "source-a", profile_revision: 4, evidence_digest: "synthetic-digest", kind: body.kind, organization: body.organization, page: body.page, entries, next_page: body.kind === "repositories" && body.page === 1 ? 2 : null, inventory_complete: false } });
  });
  await openMockedWorkspace(page);
  await page.getByLabel("Organization", { exact: true }).selectOption("synthetic-org");
  await page.getByRole("checkbox", { name: "synthetic-org/first" }).check();
  await page.getByRole("button", { name: "Load more repositories" }).click();
  await page.getByRole("checkbox", { name: "synthetic-org/second Private" }).check();
  await expect(page.getByText("2 repositories selected.", { exact: true })).toBeVisible();
  await page.getByLabel("Organization", { exact: true }).selectOption("other-org");
  await page.getByRole("checkbox", { name: "other-org/third" }).check();
  await expect(page.getByText("3 repositories selected.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Remove synthetic-org/first from selection" }).click();
  await expect(page.getByText("2 repositories selected.", { exact: true })).toBeVisible();
  expect(requests).toEqual(expect.arrayContaining([
    { page: 1, kind: "organizations", organization: null, expected_revision: 4 },
    { page: 1, kind: "repositories", organization: "synthetic-org", expected_revision: 4 },
    { page: 2, kind: "repositories", organization: "synthetic-org", expected_revision: 4 },
  ]));
});

test("reports browse errors and resets selection when the source changes", async ({ page }) => {
  await page.route("**/api/v1/connections/*/browse", async (route) => {
    if (route.request().url().includes("source-a")) return route.fulfill({ status: 500, json: { error: { code: "browse_failed", message: "Synthetic browse failure" } } });
    const body = route.request().postDataJSON() as { kind: string; organization: string | null; page: number };
    await route.fulfill({ json: { instance_key: "source-b", profile_revision: 3, evidence_digest: "synthetic", kind: body.kind, organization: body.organization, page: body.page, entries: [{ id: "77", name: "other-org" }], next_page: null, inventory_complete: false } });
  });
  await openMockedWorkspace(page, [
    { instance_key: "source-a", display_name: "Synthetic source A", revision: 1, enabled: true, locally_approved: true },
    { instance_key: "source-b", display_name: "Synthetic source B", revision: 3, enabled: true, locally_approved: true },
  ]);
  await expect(page.getByRole("alert")).toHaveText("Synthetic browse failure");
  await page.getByLabel("Source connection").selectOption("source-b");
  await expect(page.getByLabel("Organization", { exact: true }).locator("option", { hasText: "other-org" })).toBeAttached();
});

test("returns to sign-in on a mocked expired browse session", async ({ page }) => {
  await page.route("**/api/v1/connections/source-a/browse", (route) => route.fulfill({ status: 401, json: { error: { code: "authentication_required", message: "Session expired" } } }));
  await openMockedWorkspace(page);
  await expect(page.getByRole("heading", { name: "Sign in to GitFerry" })).toBeVisible();
});


test("source reads require selection and refresh cannot strand an in-flight page", async ({ page }) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let reads = 0;
  await page.route("**/api/v1/connections/source-a/browse", async (route) => {
    reads++;
    const body = route.request().postDataJSON();
    if (body.kind === "repositories") await gate;
    await route.fulfill({ json: {
      instance_key: "source-a", profile_revision: 4, evidence_digest: "synthetic",
      ...body, entries: body.kind === "organizations" ? [{ id: "42", name: "synthetic-org" }]
        : [{ id: "101", repository: "synthetic-org/first", private: false }],
      next_page: null, inventory_complete: false,
    } });
  });
  await openMockedWorkspace(page, undefined, false);
  await expect(page.getByLabel("Source connection").locator("option[value='source-a']")).toBeAttached();
  expect(reads).toBe(0);
  await page.getByLabel("Source connection").selectOption("source-a");
  await page.getByLabel("Organization", { exact: true }).selectOption("synthetic-org");
  await expect(page.getByText("Loading repositories…")).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh sources" })).toBeDisabled();
  release();
  await page.getByRole("checkbox", { name: "synthetic-org/first" }).check();
  await page.getByRole("button", { name: "Refresh sources" }).click();
  await expect(page.getByLabel("Organization", { exact: true })).toHaveValue("");
  await page.getByLabel("Organization", { exact: true }).selectOption("synthetic-org");
  await expect(page.getByRole("checkbox", { name: "synthetic-org/first" })).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Refresh sources" })).toBeEnabled();
});
