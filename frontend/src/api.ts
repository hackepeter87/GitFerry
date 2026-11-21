export interface Principal {
  username: string;
  role: string;
}

export interface SessionDocument {
  authenticated: true;
  principal: Principal;
  expiresAt?: string;
}

export interface ConnectionProfile {
  instance_key: string;
  display_name: string;
  base_url: string;
  installation_marker: string;
  credential_ref: string;
  ca_ref: string | null;
  revision: number;
  enabled: boolean;
  locally_approved: boolean;
}

export interface ConnectionConfiguration {
  format: "gitferry-connections";
  version: 1;
  profiles: Array<Pick<ConnectionProfile, "instance_key" | "display_name" | "base_url" | "installation_marker" | "credential_ref" | "ca_ref">>;
}

export interface ConnectionProbe {
  version: string;
  account_id: string;
  login: string;
  evidence_digest: string;
  requires_approval: boolean;
  checked_capabilities: string[];
  observed_identity: ConnectionIdentity;
  approved_identity: ConnectionIdentity | null;
  changed_fields: string[];
}

export interface ConnectionIdentity {
  account_id: string;
  login: string;
  ca_digest: string;
  certificate_sha256: string | null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const csrf = readCookie("gitferry_csrf");
  const headers = new Headers({ Accept: "application/json", ...init.headers });
  if (csrf && init.method && init.method !== "GET") headers.set("x-gitferry-csrf", csrf);
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
    headers,
  });
  if (!response.ok) {
    let code = "request_failed";
    let message = "GitFerry could not complete the request.";
    try {
      const body = (await response.json()) as { error?: { code?: unknown; message?: unknown } };
      if (typeof body.error?.code === "string") code = body.error.code;
      if (typeof body.error?.message === "string") message = body.error.message;
    } catch {
      // Empty and non-JSON errors still fail closed.
    }
    throw new ApiError(response.status, code, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function getAuthStatus(): Promise<{ bootstrapRequired: boolean }> {
  return request("/api/v1/auth/status");
}

export function getSession(): Promise<SessionDocument> {
  return request("/api/v1/auth/session");
}

export function login(username: string, password: string): Promise<SessionDocument> {
  return request("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export function logout(): Promise<void> {
  return request("/api/v1/auth/logout", { method: "POST" });
}

export function listConnections(): Promise<ConnectionProfile[]> {
  return request("/api/v1/connections");
}

export function createConnection(value: Pick<ConnectionProfile, "display_name" | "base_url" | "installation_marker" | "credential_ref" | "ca_ref">): Promise<ConnectionProfile> {
  return request("/api/v1/connections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}

export function updateConnection(key: string, value: { expected_revision: number; display_name: string; credential_ref: string; ca_ref: string | null }): Promise<ConnectionProfile> {
  return request(`/api/v1/connections/${encodeURIComponent(key)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}

export function retireConnection(key: string, expected_revision: number): Promise<ConnectionProfile> {
  return request(`/api/v1/connections/${encodeURIComponent(key)}/retire`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision }) });
}

export function replaceConnectionInstallation(key: string, expected_revision: number, installation_marker: string): Promise<ConnectionProfile> {
  return request(`/api/v1/connections/${encodeURIComponent(key)}/replace-installation`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision, installation_marker }) });
}

export function testConnection(key: string, expected_revision: number): Promise<ConnectionProbe> {
  return request(`/api/v1/connections/${encodeURIComponent(key)}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision }) });
}

export function approveConnection(key: string, expected_revision: number, evidence_digest: string): Promise<ConnectionProfile> {
  return request(`/api/v1/connections/${encodeURIComponent(key)}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision, evidence_digest }) });
}

export function exportConnections(): Promise<ConnectionConfiguration> {
  return request("/api/v1/connections/export");
}

export function importConnections(rawJson: string): Promise<ConnectionProfile[]> {
  return request("/api/v1/connections/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: rawJson });
}

function readCookie(name: string): string | undefined {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const value = part.trim();
    if (value.startsWith(prefix)) return decodeURIComponent(value.slice(prefix.length));
  }
  return undefined;
}
