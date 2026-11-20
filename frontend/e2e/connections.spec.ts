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
    await expect(page.getByText(name)).toBeVisible();

    await page.reload();
    await expect(page.getByText(name)).toBeVisible();
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
  });

  test("exports, rejects malformed imports, and supports keyboard controls", async ({ page }) => {
    await signIn(page);
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export" }).click();
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
});
