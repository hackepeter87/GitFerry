import { expect, test } from "@playwright/test";

const target = { instance_key: "target.synthetic", display_name: "Synthetic target", base_url: "https://target.synthetic", installation_marker: "install-target", credential_ref: "target-ref", ca_ref: null, revision: 3, enabled: true, locally_approved: true };
const realm = { realm_key: "directory.synthetic", display_name: "Synthetic directory", external_marker: "directory-1" };
const candidate = { candidate_id: "candidate-1", selector: { realm_key: realm.realm_key, login: "source.alice", external_id: "source-42", ou_path: "engineering" }, email: "alice@synthetic.example", target: { instance_key: target.instance_key, account_id: "9001", login: "target.alice", external_id: null, ou_path: null, native_id: null }, prerequisites: { issue_author: true, comment_author: false, team_member: true }, status: "pending", revision: 1, authority_digest: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", profile_revision: 3, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };

async function open(page: import("@playwright/test").Page, initialCandidates: unknown[] = []): Promise<void> {
  await page.route("**/api/v1/auth/status", (route) => route.fulfill({ json: { bootstrapRequired: false } }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { authenticated: true, principal: { username: "synthetic.admin", role: "admin" } } }));
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [target] }));
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/package-uploads", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/exports", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/exports/recipients", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/identities/realms", (route) => route.fulfill({ json: [realm] }));
  await page.route("**/api/v1/identities", (route) => route.fulfill({ json: initialCandidates }));
  await page.goto("/");
  await page.getByRole("button", {name:"Manage identities"}).click();
  await expect(page.getByRole("heading", { name: "Identity administration" })).toBeVisible();
}

test("checks an account before submitting a qualified candidate and keeps implications separate", async ({ page }) => {
  let checkBody: Record<string, unknown> | undefined;
  let candidateBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/identities/check-account", async (route) => { checkBody = route.request().postDataJSON(); await route.fulfill({ json: { target_account_id: "9001", target_login: "target.alice", authority_digest: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", credential_checked: true } }); });
  await page.route("**/api/v1/identities/candidates", async (route) => { candidateBody = route.request().postDataJSON(); await route.fulfill({ json: candidate }); });
  await open(page);
  await page.getByLabel("Target login").fill("target.alice");
  await page.getByLabel("Credential reference (optional)").fill("cred.synthetic");
  await page.getByRole("button", { name: "Check target account" }).click();
  await expect(page.getByText(/Checked target.alice/)).toBeVisible();
  await page.getByLabel("Source login").fill("source.alice");
  await page.getByLabel("External ID").fill("source-42");
  await page.getByLabel("OU path (optional)").fill("engineering");
  await page.getByLabel("Email (optional, informational)").fill("alice@synthetic.example");
  await page.getByLabel("Comment author").uncheck();
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByRole("heading", { name: "source.alice → target.alice" })).toBeVisible();
  expect(checkBody).toMatchObject({ target_instance_key: target.instance_key, profile_revision: 3, target_login: "target.alice", credential_ref: "cred.synthetic" });
  expect(candidateBody).toMatchObject({ target_instance_key: target.instance_key, profile_revision: 3, authority_digest: candidate.authority_digest, target_account_id: "9001", target_login: "target.alice", prerequisites: { issue_author: true, comment_author: false, team_member: true } });
});

test("binds decisions to the current revision and exposes event history", async ({ page }) => {
  let decisionBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/identities/*/decision", async (route) => { decisionBody = route.request().postDataJSON(); await route.fulfill({ json: { ...candidate, status: "approved", revision: 2 } }); });
  await page.route("**/api/v1/identities/*/events", (route) => route.fulfill({ json: [{ event_id: "event-1", event_type: "approved", created_at: "2026-01-01T01:00:00Z" }] }));
  await open(page, [candidate]);
  await page.getByRole("button", { name: "Approve" }).click();
  expect(decisionBody).toMatchObject({ expected_revision: 1, decision: "approved", profile_revision: 3 });
  await expect(page.getByText("approved", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "View events" }).click();
  await expect(page.getByText(/approved · 2026-01-01/)).toBeVisible();
});

test("reopens a conflict before approval and keeps the optional email empty", async ({ page }) => {
  const conflict = { ...candidate, status: "conflict" };
  const decisions: string[] = [];
  await page.route("**/api/v1/identities/*/decision", async (route) => {
    const body = route.request().postDataJSON() as { decision: string; expected_revision: number };
    decisions.push(body.decision);
    await route.fulfill({ json: { ...candidate, status: body.decision === "pending" ? "pending" : "approved", revision: body.expected_revision + 1 } });
  });
  await open(page, [conflict]);
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await page.getByRole("button", { name: "Reopen" }).click();
  await expect(page.getByText("Candidate pending.")).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Candidate approved.")).toBeVisible();
  expect(decisions).toEqual(["pending", "approved"]);
  expect(await page.getByLabel("Email (optional, informational)").inputValue()).toBe("");
  await page.getByLabel("Source login").focus();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("External ID")).toBeFocused();
});

test("keeps rejection available for a retired target and shows immutable account/audit evidence", async ({ page }) => {
  const retired = { ...candidate, target: { ...candidate.target, account_id: "9007199254740993", instance_key: "retired-target" } };
  let decisionBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/identities/*/decision", async route => {
    decisionBody = route.request().postDataJSON();
    await route.fulfill({json:{...retired,status:"rejected",revision:2}});
  });
  await page.route("**/api/v1/identities/*/events", route => route.fulfill({json:[{
    revision:2,decision:"rejected",actor:"local-admin-id",reason:"explicit-decision",created_at:"2026-01-01T01:00:00Z",
  }]}));
  await open(page,[retired]);
  await expect(page.getByText(/native account 9007199254740993/)).toBeVisible();
  await page.getByRole("button",{name:"Approve",exact:true}).click();
  await expect(page.getByRole("alert")).toContainText("connection is disabled");
  expect(decisionBody).toBeUndefined();
  await page.getByRole("button",{name:"Reject",exact:true}).click();
  expect(decisionBody).toMatchObject({decision:"rejected",expected_revision:1,profile_revision:3});
  await page.getByRole("button",{name:"View events"}).click();
  await expect(page.getByLabel("Candidate events")).toContainText("actor local-admin-id");
  await expect(page.getByLabel("Candidate events")).toContainText("explicit-decision");
});
