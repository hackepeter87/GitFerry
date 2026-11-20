import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

const runtimeRoot =
  process.env.GITFERRY_E2E_ROOT ?? mkdtempSync(join(tmpdir(), "gitferry-e2e-"));
process.env.GITFERRY_E2E_ROOT = runtimeRoot;
const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const python = process.env.GITFERRY_TEST_PYTHON ?? join(repositoryRoot, ".venv", "bin", "python");
const fixture = join(repositoryRoot, "tests", "browser", "auth_fixture_server.py");
const adminCli = join(repositoryRoot, "tests", "browser", "admin_cli.py");
const pythonPath = join(repositoryRoot, "backend", "src");
process.env.GITFERRY_TEST_PYTHON = python;
process.env.GITFERRY_TEST_ADMIN_CLI = adminCli;
process.env.PYTHONPATH = pythonPath;

function quoted(value: string): string {
  return JSON.stringify(value);
}

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: [
    {
      command: `${quoted(python)} ${quoted(fixture)} --mode bootstrapped --port 8089`,
      url: "http://127.0.0.1:8089/healthz",
      env: {
        ...process.env,
        PYTHONPATH: pythonPath,
        GITFERRY_E2E_ROOT: runtimeRoot,
      },
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `${quoted(python)} ${quoted(fixture)} --mode unbootstrapped --port 8090`,
      url: "http://127.0.0.1:8090/healthz",
      env: {
        ...process.env,
        PYTHONPATH: pythonPath,
        GITFERRY_E2E_ROOT: runtimeRoot,
      },
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 4173 --strictPort",
      url: "http://127.0.0.1:4173",
      env: { ...process.env, GITFERRY_API_TARGET: "http://127.0.0.1:8089" },
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 4174 --strictPort",
      url: "http://127.0.0.1:4174",
      env: { ...process.env, GITFERRY_API_TARGET: "http://127.0.0.1:8090" },
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
