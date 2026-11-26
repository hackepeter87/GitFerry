import { expect, test } from "@playwright/test";

import { inventoryPollAttempt } from "../integration/inventory-poll";

test("inventory polling retries only transient tunnel failures", async () => {
  let attempts = 0;
  const read = async () => {
    attempts++;
    if (attempts === 1) throw new Error("socket hang up (ECONNRESET)");
    return { status: "ready" };
  };

  await expect(inventoryPollAttempt(read)).resolves.toEqual({ kind: "transport-retry" });
  await expect(inventoryPollAttempt(read)).resolves.toEqual({
    kind: "value",
    value: { status: "ready" },
  });
  await expect(inventoryPollAttempt(async () => {
    throw new Error("HTTP 500 from inventory endpoint");
  })).rejects.toThrow("HTTP 500");
  await expect(inventoryPollAttempt(async () => {
    throw new Error("connect ECONNRESET");
  })).resolves.toEqual({ kind: "transport-retry" });
});
