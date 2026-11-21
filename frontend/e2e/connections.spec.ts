import { expect, test, type Page } from "@playwright/test";

async function signIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Username").fill("synthetic.admin");
  await page.getByLabel("Password").fill("correct synthetic passphrase");
  const response = page.waitForResponse((value) => value.url().endsWith("/api/v1/auth/login"));
  await page.getByRole("button", { name: "Sign in" }).click();
  if ((await response).status() === 401) {
    await expect(page.getByRole("alert")).toHaveText("Invalid credentials");
    await page.getByLabel("Password").fill("replacement synthetic passphrase");
    await page.getByRole("button", { name: "Sign in" }).click();
  }
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();
}

test.describe("connection management", () => {
  test("creates, reloads, edits, and handles a stale revision", async ({ page }) => {
    await signIn(page);
    const name = `Browser connection ${Date.now()}`;
    await page.getByLabel("Display name").fill(name);
    await page.getByLabel("Gitea HTTPS URL").fill("https://gitea.synthetic.example");
    await page.getByLabel("Installation marker").fill(`install-${Date.now()}`);
    await page.getByLabel("Credential reference").fill("cred.synthetic");
    await page.getByRole("button", { name: "Add connection" }).click();
    await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
    await page.getByRole("listitem").filter({ hasText: name }).getByRole("button", { name: "Edit" }).click();
    await page.getByLabel("Display name").fill(`${name} edited`);

    const profile = await page.request.get("/api/v1/connections");
    const records = (await profile.json()) as Array<{ instance_key: string; revision: number; display_name: string; credential_ref: string; ca_ref: string | null }>;
    const record = records.find((value) => value.display_name === name)!;
    expect(record).toBeDefined();
    const csrf = (await page.context().cookies()).find((cookie) => cookie.name === "gitferry_csrf")?.value;
    expect(csrf).toBeTruthy();
    const bump = await page.request.patch(`/api/v1/connections/${record.instance_key}`, {
      data: { expected_revision: record.revision, display_name: record.display_name, credential_ref: record.credential_ref, ca_ref: record.ca_ref },
      headers: { "x-gitferry-csrf": csrf },
    });
    expect(bump.ok()).toBeTruthy();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("alert")).toContainText("changed elsewhere");

    await page.reload();
    const card = page.getByRole("listitem").filter({ hasText: name });
    await card.getByRole("button", { name: "Replace installation identity" }).click();
    await page.getByLabel("New installation marker").fill(`rotated-${Date.now()}`);
    await page.getByRole("button", { name: "Confirm replacement" }).click();
    await expect(page.getByText("Retired", { exact: true })).toBeVisible();
    await expect(page.getByText(/rotated-/)).toBeVisible();

    const active = page.getByRole("listitem").filter({ hasText: /rotated-/ });
    await page.on("dialog", (dialog) => dialog.accept());
    await active.getByRole("button", { name: "Retire" }).click();
    await expect(active.getByText("Retired", { exact: true })).toBeVisible();
    await expect(active.getByRole("button", { name: "Retire" })).toHaveCount(0);
  });

  test("exports, rejects malformed imports, and supports keyboard controls", async ({ page }) => {
    await signIn(page);
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export", exact: true }).click();
    expect((await download).suggestedFilename()).toBe("gitferry-connections.json");

    const chooser = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "Import" }).click();
    await (await chooser).setFiles({ name: "bad.json", mimeType: "application/json", buffer: Buffer.from("{}") });
    await expect(page.getByRole("alert")).toContainText("Import rejected");

    const name = page.getByLabel("Display name");
    await name.focus();
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Gitea HTTPS URL")).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(name).toBeFocused();
  });

  test("shows mocked certificate rotation evidence and keeps decline pending", async ({ page }) => {
    await signIn(page);
    const name = `Rotation evidence ${Date.now()}`;
    await page.getByLabel("Display name").fill(name);
    await page.getByLabel("Gitea HTTPS URL").fill("https://rotation.synthetic.example");
    await page.getByLabel("Installation marker").fill(`rotation-${Date.now()}`);
    await page.getByLabel("Credential reference").fill("cred.rotation.synthetic");
    await page.getByRole("button", { name: "Add connection" }).click();
    const card = page.getByRole("listitem").filter({ hasText: name });
    await expect(card).toBeVisible();

    const observed = {
      account_id: "42",
      login: "rotation.synthetic",
      ca_digest: "new-ca-fingerprint",
      certificate_sha256: "new-certificate-fingerprint",
    };
    const approved = {
      account_id: "42",
      login: "rotation.synthetic",
      ca_digest: "old-ca-fingerprint",
      certificate_sha256: "old-certificate-fingerprint",
    };
    await page.route("**/api/v1/connections/*/test", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          version: "1.27.2",
          account_id: "42",
          login: "rotation.synthetic",
          evidence_digest: "synthetic-rotation-evidence",
          requires_approval: true,
          checked_capabilities: ["version-read", "authenticated-account-read"],
          observed_identity: observed,
          approved_identity: approved,
          changed_fields: ["ca_digest", "certificate_sha256"],
        }),
      });
    });
    let approvals = 0;
    await page.route("**/api/v1/connections/*/approve", async (route) => {
      approvals += 1;
      await route.continue();
    });
    page.on("dialog", async (dialog) => {
      expect(dialog.message()).toContain("new-certificate-fingerprint");
      expect(dialog.message()).toContain("old-certificate-fingerprint");
      expect(dialog.message()).toContain("new-ca-fingerprint");
      expect(dialog.message()).toContain("old-ca-fingerprint");
      expect(dialog.message()).toContain("does not authorize writes");
      await dialog.dismiss();
    });
    await card.getByRole("button", { name: "Test connection" }).click();
    await expect(card.getByText("new-ca-fingerprint")).toBeVisible();
    await expect(card.getByText("old-ca-fingerprint")).toBeVisible();
    await expect(card.getByText("new-certificate-fingerprint")).toBeVisible();
    await expect(card.getByText("old-certificate-fingerprint")).toBeVisible();
    await expect(card.getByText("Changed fields: ca_digest, certificate_sha256")).toBeVisible();
    await expect(card.getByText("Approval pending", { exact: true })).toBeVisible();
    expect(approvals).toBe(0);
  });

  test("imports a synthetic configuration into the live profile list", async ({ page }) => {
    await signIn(page);
    const name = `Imported connection ${Date.now()}`;
    const chooser = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "Import" }).click();
    await (await chooser).setFiles({
      name: "synthetic-connections.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify({
        format: "gitferry-connections",
        version: 1,
        profiles: [{
          instance_key: `imported-${Date.now()}`,
          display_name: name,
          base_url: "https://import.synthetic.example",
          installation_marker: `import-${Date.now()}`,
          credential_ref: "cred.import.synthetic",
          ca_ref: null,
        }],
      })),
    });
    await expect(page.getByRole("listitem").filter({ hasText: name })).toBeVisible();
    await expect(page.getByRole("listitem").filter({ hasText: name }).getByText("Approval pending", { exact: true })).toBeVisible();
  });
});
