import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const target = { instance_key: "target.synthetic", display_name: "Synthetic target", base_url: "https://target.synthetic", installation_marker: "install-target", credential_ref: "target-ref", ca_ref: "ca-ref", revision: 3, enabled: true, locally_approved: true };
const realm = { realm_key: "directory.synthetic", display_name: "Synthetic directory", external_marker: "directory-1" };
const source = { name: "synthetic-ldap", type: "ldaps", directoryRealm: realm.realm_key, host: "ldap.synthetic.example", port: 636, mode: "simple", securityProtocol: "ldaps", userDn: "uid=%s,ou=People,dc=example,dc=invalid", userFilter: "(&(objectClass=posixAccount)(uid=%s))", emailAttribute: "mail", usernameAttribute: "uid", caSecretRef: "trust/directory-ca", claimLookup: { searchBase: "ou=People,dc=example,dc=invalid", loginAttribute: "uid", externalIdAttribute: "employeeNumber", emailAttribute: "mail", groupSearchBase: "ou=Groups,dc=example,dc=invalid", groupMemberAttribute: "member" } };
const record = { instance_key: target.instance_key, source_name: source.name, realm_key: realm.realm_key, source, revision: 1, source_digest: "a".repeat(64), profile_revision: 3, status: "ready-for-target-local-bootstrap", prerequisites: ["simple", "TLS", "CA"], updated_at: "2026-01-01T00:00:00Z" };

async function open(page: import("@playwright/test").Page, directories: unknown[] = []): Promise<void> {
  await page.route("**/api/v1/auth/status", (route) => route.fulfill({ json: { bootstrapRequired: false } }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { authenticated: true, principal: { username: "synthetic.admin", role: "admin" } } }));
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [target] }));
  await page.route("**/api/v1/identities/realms", (route) => route.fulfill({ json: [realm] }));
  await page.route("**/api/v1/identities", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/directories", (route) => route.fulfill({ json: directories }));
  await page.route("**/api/v1/plans", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/package-uploads", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/exports", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/exports/recipients", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await page.getByRole("button", { name: "Manage directories" }).click();
  await expect(page.getByRole("heading", { name: "Manage directories" })).toBeVisible();
}

test("creates, edits with a revision, and exports a local directory blueprint", async ({ page }) => {
  let saveBody: Record<string, unknown> | undefined;
  await open(page);
  await page.route("**/api/v1/directories", async (route) => {
    if (route.request().method() === "POST") { saveBody = route.request().postDataJSON(); await route.fulfill({ json: record }); return; }
    await route.fulfill({ json: [] });
  });
  await page.route("**/api/v1/directories/*/*/export", (route) => route.fulfill({ json: { apiVersion: "gitferry.io/v1", kind: "InstanceBlueprint", metadata: { name: "target" }, spec: { gitea: { baseUrl: target.base_url, credentialSecretRef: target.credential_ref }, authenticationSources: [source] } } }));
  await page.getByLabel("Source name").fill(source.name);
  await page.getByLabel("Directory host").fill(source.host);
  await page.getByLabel("User DN template").fill(source.userDn);
  await page.getByLabel("User filter").fill(source.userFilter);
  await page.getByLabel("CA secret reference").fill(source.caSecretRef);
  await page.getByLabel("Enable claim lookup").check();
  await page.getByLabel("Claim search base").fill(source.claimLookup.searchBase);
  await page.getByLabel("Claim login attribute").fill(source.claimLookup.loginAttribute);
  await page.getByLabel("Claim external ID attribute").fill(source.claimLookup.externalIdAttribute);
  await page.getByLabel("Claim email attribute (optional)").fill(source.claimLookup.emailAttribute);
  await page.getByLabel("Claim group search base (optional)").fill(source.claimLookup.groupSearchBase);
  await page.getByLabel("Claim group member attribute (optional)").fill(source.claimLookup.groupMemberAttribute);
  await page.getByLabel("Source name").press("Tab");
  await expect(page.getByLabel("Registered realm")).toBeFocused();
  await page.getByRole("button", { name: "Save reviewed settings" }).click();
  expect(saveBody).toMatchObject({ instance_key: target.instance_key, expected_revision: null, source: { name: source.name, caSecretRef: source.caSecretRef, claimLookup: source.claimLookup } });
  await expect(page.getByText("Directory settings reviewed and saved locally.")).toBeVisible();
  await page.getByRole("button", { name: "Download blueprint" }).click();
  await expect(page.getByText("Versioned InstanceBlueprint downloaded.")).toBeVisible();
  expect(await new AxeBuilder({ page }).analyze()).toMatchObject({ violations: [] });
});

test("shows BindDN as manual-only and rejects oversized or secret-bearing imports", async ({ page }) => {
  await open(page, [record]);
  await page.getByRole("list", { name: "Saved directory settings" }).getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Authentication mode").selectOption("bindDN");
  let bindSaveBody: Record<string, any> | undefined;
  await page.route("**/api/v1/directories", async (route) => {
    if (route.request().method() === "POST") {
      bindSaveBody = route.request().postDataJSON();
      await route.fulfill({ json: { ...record, source: { ...record.source, bindSecretRef: "secrets/directory-bind", userSearchBase: "ou=People,dc=example,dc=invalid", groupSearchBase: "ou=Groups,dc=example,dc=invalid", mode: undefined } } });
      return;
    }
    await route.fulfill({ json: [record] });
  });
  await expect(page.getByText(/BindDN automation is unsupported/)).toBeVisible();
  await page.getByLabel("User search base").fill("ou=People,dc=example,dc=invalid");
  await page.getByLabel("Group search base", { exact: true }).fill("ou=Groups,dc=example,dc=invalid");
  await page.getByLabel("Bind secret reference").fill("secrets/directory-bind");
  await page.getByRole("button", { name: "Save revision" }).click();
  expect(bindSaveBody?.source).toMatchObject({ bindSecretRef: "secrets/directory-bind", userSearchBase: "ou=People,dc=example,dc=invalid", groupSearchBase: "ou=Groups,dc=example,dc=invalid", caSecretRef: source.caSecretRef });
  expect(bindSaveBody?.source).not.toHaveProperty("mode");
  expect(bindSaveBody?.source).not.toHaveProperty("bindDn");
  expect(bindSaveBody?.source).not.toHaveProperty("bindPasswordSecretRef");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Import blueprint" }).click();
  await (await chooser).setFiles({ name: "too-large.yaml", mimeType: "text/yaml", buffer: Buffer.alloc(64 * 1024 + 1, "x") });
  await expect(page.getByRole("alert")).toContainText("larger than 64 KiB");
  const secretChooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Import blueprint" }).click();
  await (await secretChooser).setFiles({ name: "secret.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ apiVersion: "gitferry.io/v1", kind: "InstanceBlueprint", metadata: { name: "target" }, spec: { gitea: { baseUrl: target.base_url, credentialSecretRef: target.credential_ref }, authenticationSources: [{ ...source, password: "do-not-send" }] } })) });
  await expect(page.getByRole("alert")).toBeVisible();
});

test("surfaces a stale revision and keeps target identity fields locked while editing", async ({ page }) => {
  await open(page, [record]);
  await page.route("**/api/v1/directories", async (route) => {
    if (route.request().method() === "POST") { await route.fulfill({ status: 409, json: { error: { code: "stale_revision", message: "changed elsewhere" } } }); return; }
    await route.fulfill({ json: [record] });
  });
  await page.getByRole("list", { name: "Saved directory settings" }).getByRole("button", { name: "Edit" }).click();
  await expect(page.getByLabel("Approved target connection")).toBeDisabled();
  await expect(page.getByLabel("Source name")).toBeDisabled();
  await page.getByLabel("Directory host").fill("ldap2.synthetic.example");
  await page.getByRole("button", { name: "Save revision" }).click();
  await expect(page.getByRole("alert")).toContainText("changed elsewhere");
});


test("keeps uncertain historical bootstrap separate from edited settings", async ({ page }) => {
  await open(page, [{ ...record, bootstrap: { status: "outcome_unknown", native_source_id: null, reason: "response lost" } }]);
  const card = page.locator(".directory-card");
  await expect(card.locator(".state")).toHaveText(record.status);
  await expect(card.locator(".bootstrap-status")).toContainText("Previous local bootstrap: outcome_unknown");
  await expect(card.locator(".bootstrap-status")).toContainText("do not repeat creation");
  await expect(card.getByRole("button", { name: /apply|retry|execute/i })).toHaveCount(0);
});
