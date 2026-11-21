import { request } from "./api";

export interface IdentityRealm {
  realm_key: string;
  display_name: string;
  external_marker: string;
}

export type IdentityStatus = "pending" | "approved" | "rejected" | "conflict";

export interface IdentitySelector {
  realm_key: string;
  login: string;
  external_id: string;
  ou_path: string;
}

export interface IdentityPrerequisites {
  issue_author: boolean;
  comment_author: boolean;
  team_member: boolean;
}

export interface IdentityCandidate {
  candidate_id: string;
  selector: IdentitySelector;
  email?: string | null;
  target: {
    instance_key: string;
    account_id: string;
    login: string;
    external_id: string | null;
    ou_path: string | null;
    native_id: string | null;
  };
  prerequisites: IdentityPrerequisites;
  status: IdentityStatus;
  revision: number;
  account_evidence?: { source_id: string | null; login_name: string | null };
  authority_digest: string;
  profile_revision: number;
  created_at: string;
  updated_at: string;
}

export interface IdentityAccountCheck {
  target_account_id: string;
  target_login: string;
  authority_digest: string;
  credential_checked: boolean;
}

export interface IdentityEvent {
  event_id?: string;
  event_type?: string;
  decision?: string;
  action?: string;
  actor?: string;
  revision?: number;
  created_at?: string;
  details?: Record<string, unknown>;
  [key: string]: unknown;
}

const json = (value: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });

export function listIdentityCandidates(): Promise<IdentityCandidate[]> {
  return request("/api/v1/identities");
}

export function listIdentityRealms(): Promise<IdentityRealm[]> {
  return request("/api/v1/identities/realms");
}

export function createIdentityRealm(value: IdentityRealm): Promise<IdentityRealm> {
  return request("/api/v1/identities/realms", json(value));
}

export function checkIdentityAccount(value: { target_instance_key: string; profile_revision: number; target_login: string; credential_ref?: string }): Promise<IdentityAccountCheck> {
  return request("/api/v1/identities/check-account", json(value));
}

export function createIdentityCandidate(value: {
  target_instance_key: string;
  profile_revision: number;
  authority_digest: string;
  selector: IdentitySelector;
  target_account_id: string;
  target_login: string;
  email?: string | null;
  prerequisites: IdentityPrerequisites;
}): Promise<IdentityCandidate> {
  return request("/api/v1/identities/candidates", json(value));
}

export function decideIdentityCandidate(candidateId: string, value: { expected_revision: number; decision: "approved" | "rejected" | "pending"; profile_revision: number; credential_ref?: string }): Promise<IdentityCandidate> {
  return request(`/api/v1/identities/${encodeURIComponent(candidateId)}/decision`, json(value));
}

export function listIdentityEvents(candidateId: string): Promise<IdentityEvent[]> {
  return request(`/api/v1/identities/${encodeURIComponent(candidateId)}/events`);
}
