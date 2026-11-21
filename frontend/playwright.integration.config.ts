import { resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./integration",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: process.env.GITFERRY_INTEGRATION_APP_URL ?? "http://127.0.0.1:33003",
    // Real administrator sessions must not enter publishable network traces.
    trace: "off",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
  outputDir: process.env.GITFERRY_INTEGRATION_BROWSER_OUTPUT ?? resolve("../integration-results", "browser"),
});
