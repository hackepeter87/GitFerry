import { request } from "./api";

export interface DirectorySource {
  name: string;
  type: "ldap" | "ldaps";
  directoryRealm: string;
  host: string;
  port: number;
  securityProtocol?: "ldaps" | "starttls";
  mode?: "simple";
  userDn?: string;
  userFilter?: string;
  emailAttribute?: string;
  usernameAttribute?: string;
  caSecretRef?: string;
  userSearchBase?: string;
  groupSearchBase?: string;
  bindSecretRef?: string;
}

export interface DirectoryRecord {
  instance_key: string;
  source_name: string;
  realm_key: string;
  source: DirectorySource;
  revision: number;
  source_digest: string;
  profile_revision: number;
  status: string;
  bootstrap?: { status: string; native_source_id: string | null; reason: string | null } | null;
  prerequisites: string[];
  updated_at: string;
}

export interface InstanceBlueprint {
  apiVersion: "gitferry.io/v1";
  kind: "InstanceBlueprint";
  metadata: { name: string };
  spec: {
    gitea: Record<string, string>;
    authenticationSources: DirectorySource[];
  };
}

export function listDirectories(): Promise<DirectoryRecord[]> {
  return request("/api/v1/directories");
}

export function saveDirectory(value: { instance_key: string; source: DirectorySource; expected_revision: number | null }): Promise<DirectoryRecord> {
  return request("/api/v1/directories", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}

export function importDirectory(value: { instance_key: string; content: string; expected_revision: number | null }): Promise<DirectoryRecord> {
  return request("/api/v1/directories/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}

export function exportDirectory(instanceKey: string, sourceName: string): Promise<InstanceBlueprint> {
  return request(`/api/v1/directories/${encodeURIComponent(instanceKey)}/${encodeURIComponent(sourceName)}/export`);
}
