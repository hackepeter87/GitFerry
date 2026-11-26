export type InventoryPollAttempt<T> =
  | { kind: "value"; value: T }
  | { kind: "transport-retry" };

function isTransientTunnelFailure(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /(?:socket hang up|\bECONNRESET\b|\bECONNREFUSED\b|\bETIMEDOUT\b|\bEPIPE\b)/i.test(message);
}

export async function inventoryPollAttempt<T>(read: () => Promise<T>): Promise<InventoryPollAttempt<T>> {
  try {
    return { kind: "value", value: await read() };
  } catch (error) {
    if (isTransientTunnelFailure(error)) return { kind: "transport-retry" };
    throw error;
  }
}
