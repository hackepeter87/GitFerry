import { basename, dirname, resolve } from "node:path";
import { rmSync } from "node:fs";
import { tmpdir } from "node:os";

export default function globalTeardown(): void {
  const configured = process.env.GITFERRY_E2E_ROOT;
  if (!configured) return;
  const target = resolve(configured);
  if (dirname(target) !== resolve(tmpdir()) || !basename(target).startsWith("gitferry-e2e-")) {
    throw new Error("Refusing to remove an unexpected browser-test directory");
  }
  rmSync(target, { recursive: true, force: true });
}
