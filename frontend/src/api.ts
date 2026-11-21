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

export type BrowseKind = "organizations" | "repositories";

export interface BrowseEntry {
  id: string;
  name?: string;
  repository?: string;
  private?: boolean;
}

export interface BrowseResponse {
  instance_key: string;
  profile_revision: number;
  evidence_digest: string;
  kind: BrowseKind;
  organization: string | null;
  page: number;
  entries: BrowseEntry[];
  next_page: number | null;
  inventory_complete: false;
}

export type InventoryStatus = "queued" | "running" | "ready" | "blocked" | "failed";
export type InventoryClassStatus = "observed" | "not-supported" | "not-checked" | "excluded" | "blocked" | "failed";
export interface InventoryCapabilities { classes: string[]; supported: string[]; schema_version: number; }
export interface InventoryRepositoryRequest { repository: string; global_id: string; }
export interface InventoryRequest {
  source_instance_key: string;
  source_profile_revision: number;
  repositories: InventoryRepositoryRequest[];
  requested_classes: string[];
}
export interface InventoryObject {
  key: string;
  namespace: string;
  native_id?: string | null;
  issue_index?: string | null;
  parent?: { content_class: string; key: string } | null;
  author?: { account_id: string; login: string } | null;
  facts: Record<string, unknown>;
  size_bytes?: number | null;
}
export interface InventoryClassObservation {
  content_class: string;
  status: InventoryClassStatus;
  count: number | null;
  objects: InventoryObject[];
  reason: string;
  limitations: string[];
}
export interface InventoryRepository {
  repository: string;
  native_id: string;
  requested_classes: string[];
  classes: InventoryClassObservation[];
  git_size_estimate_bytes: number | null;
  size_assumptions: string[];
}
export interface InventoryDocument {
  format: "gitferry.content-inventory";
  schema_version: number;
  comparison_version: number;
  snapshot_id: string;
  created_at: string;
  source_instance_key: string;
  source_installation_marker: string;
  gitea_version: string;
  repositories: InventoryRepository[];
  deletion_semantics: string;
}
export interface InventoryBlocker { repository: string; content_class: string; reason: string; }
export interface InventorySummary {
  inventory_complete: boolean;
  transfer_ready: boolean;
  full_transfer: boolean;
  blockers: InventoryBlocker[];
  excluded_classes: InventoryBlocker[];
  known_payload_bytes: number;
  objects_without_size: number;
  temporary_space_assumption: string;
}
export interface InventoryCoverageClass {
  content_class: string;
  selected: boolean;
  status: InventoryClassStatus;
  count: number | null;
  reason: string;
  limitations: string[];
}
export interface InventoryCoverageRepository {
  repository: string;
  native_id: string;
  git_size_estimate_bytes: number | null;
  size_assumptions: string[];
  classes: InventoryCoverageClass[];
}
export interface InventoryCoverageReport {
  snapshot_id: string;
  content_digest: string;
  source_instance_key: string;
  comparison_version: number;
  summary: InventorySummary;
  repositories: InventoryCoverageRepository[];
}
export interface InventoryJobSummary {
  inventory_id: string;
  status: InventoryStatus;
  document_digest: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}
export interface InventoryJob extends InventoryJobSummary {
  request: InventoryRequest;
  document: InventoryDocument | null;
  summary: InventorySummary | null;
}

export type PlanStatus = "draft" | "queued" | "preflighting" | "running" | "confirmed" | "blocked" | "failed" | "cancelled" | (string & {});
export type PlanPreviewStatus = "queued" | "checking" | "passed" | "blocked" | "failed";
export interface PlanPreview {
  generation: number;
  plan_digest: string;
  status: PlanPreviewStatus;
  reason: string | null;
  scope: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlanOperation {
  operation_id: string;
  step_index?: number;
  operation_type: string;
  target_repository_scope?: string | null;
  origin_id?: string | null;
  status: string;
  target_global_id?: string | null;
  error_reason?: string | null;
  intent?: Record<string, unknown>;
  bindings?: Record<string, unknown>;
  baseline?: Record<string, unknown> | null;
}

export interface TransferPlan {
  plan_id: string;
  digest: string;
  target_instance_key: string;
  profile_revision: number;
  evidence_digest: string;
  round_key: string;
  document: {
    inventory?: {
      request?: Record<string, unknown>;
      included_operations?: string[];
      inventory_complete?: boolean;
      limitations?: string[];
      content_inventory?: InventoryCoverageReport | null;
    };
    [key: string]: unknown;
  };
  status: PlanStatus;
  cancel_requested: boolean;
  reason: string | null;
  created_at: string;
  updated_at: string;
  preview?: PlanPreview | null;
  operations?: PlanOperation[];
  events?: Array<Record<string, unknown>>;
}

export type SourceExportCaptureStatus = "prepared" | "capturing" | "captured" | "failed";
export type SourceExportStatus = "prepared" | "queued" | "running" | "ready" | "failed";

export interface SourceExportRepository {
  repository: string;
  global_id: string;
}

export interface SourceExportRequest {
  version: 1 | 2;
  inventory_id?: string;
  inventory_digest?: string;
  source_instance_key: string;
  source_profile_revision: number;
  recipient_instance_key: string;
  repositories: SourceExportRepository[];
  include_metadata: boolean;
  include_members: boolean;
}

export interface SourceExportJob {
  export_id: string;
  request: SourceExportRequest;
  capture_status: SourceExportCaptureStatus;
  status: SourceExportStatus;
  archive_sha256: string | null;
  archive_size: number | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface SourceExportRecipient {
  instance_key: string;
  display_name: string;
  installation_marker: string;
}

export type PackageUploadStatus = "receiving" | "validating" | "ready" | "failed";
export interface PackageUploadSummary {
  package_id: string;
  sender: { instance_key: string; installation_marker: string };
  recipient: { instance_key: string; installation_marker: string };
  repositories: string[];
  metadata_present: boolean;
  inventory_complete: false;
  limitations: string[];
}
export interface PackageUpload {
  upload_id: string;
  status: PackageUploadStatus;
  archive_size: number;
  archive_sha256: string | null;
  package_ref: string | null;
  package_digest: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  summary: PackageUploadSummary | null;
}

export interface PackageAdmissionBase {
  target_instance_key: string;
  profile_revision: number;
}
export interface PackageAdmissionPrerequisite {
  package_id: string;
  digest_sha256: string;
  target_instance_key: string;
  profile_revision: number;
  sender: { instance_key: string; installation_marker: string; registered: boolean };
  realms: Array<{ realm_key: string; display_name: string; external_marker: string; registered: boolean }>;
  identities: Array<{
    selector: { realm_key: string; login: string; external_id: string; ou_path: string | null };
    roles: string[];
    binding: { binding_id: string; target_login: string; target_account_id: string; mapping_version: number } | null;
  }>;
  repositories: string[];
  bundle_bytes: number;
  metadata_present: boolean;
  limitations: string[];
}

export interface PackagePlanReview {
  digest_sha256: string;
  target_instance_key: string;
  profile_revision: number;
  review_digest: string;
  selection: {
    request: Record<string, unknown>;
    missing_bindings: string[];
    missing_credentials: string[];
    waiting_parents: string[];
    blocked_operations: string[];
    [key: string]: unknown;
  };
  content_inventory: InventoryCoverageReport | null;
  [key: string]: unknown;
}

export function packageAdmissionPrerequisites(packageRef: string, value: PackageAdmissionBase): Promise<PackageAdmissionPrerequisite> {
  return request(`/api/v1/packages/${encodeURIComponent(packageRef)}/prerequisites`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}
export function confirmPackageSender(packageRef: string, value: PackageAdmissionBase & { expected_digest: string; display_name: string; base_url: string }): Promise<PackageAdmissionPrerequisite> {
  return request(`/api/v1/packages/${encodeURIComponent(packageRef)}/confirm-sender`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}
export function confirmPackageRealm(packageRef: string, value: PackageAdmissionBase & { expected_digest: string; realm_key: string; display_name: string; external_marker: string }): Promise<PackageAdmissionPrerequisite> {
  return request(`/api/v1/packages/${encodeURIComponent(packageRef)}/confirm-realm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}
export function checkPackageAccount(packageRef: string, value: PackageAdmissionBase & { expected_digest: string; target_login: string; credential_ref: string | null }): Promise<{ target_login: string; target_account_id: string; credential_checked: boolean }> {
  return request(`/api/v1/packages/${encodeURIComponent(packageRef)}/check-account`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}
export function confirmPackageBinding(packageRef: string, value: PackageAdmissionBase & { expected_digest: string; selector: PackageAdmissionPrerequisite["identities"][number]["selector"]; target_login: string; target_account_id: string; credential_ref: string | null }): Promise<PackageAdmissionPrerequisite> {
  return request(`/api/v1/packages/${encodeURIComponent(packageRef)}/confirm-binding`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}
export function reviewPackage(packageRef: string, value: PackageAdmissionBase): Promise<Record<string, unknown>> {
  return request(`/api/v1/packages/${encodeURIComponent(packageRef)}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}
export function admitPackage(packageRef: string, value: PackageAdmissionBase & { expected_digest: string }): Promise<Record<string, unknown>> {
  return request(`/api/v1/packages/${encodeURIComponent(packageRef)}/admit`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}
export function reviewPackagePlan(packageRef: string, value: PackageAdmissionBase & { include_metadata: boolean; apply_members: boolean; author_credential_refs: Record<string, string> }): Promise<PackagePlanReview> {
  return request(`/api/v1/packages/${encodeURIComponent(packageRef)}/plan-review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}
export function preparePackagePlan(packageRef: string, value: PackageAdmissionBase & { expected_review_digest: string; include_metadata: boolean; apply_members: boolean; author_credential_refs: Record<string, string> }): Promise<TransferPlan> {
  return request(`/api/v1/packages/${encodeURIComponent(packageRef)}/prepare-plan`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
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

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
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

export function listPackageUploads(): Promise<PackageUpload[]> {
  return request("/api/v1/package-uploads");
}

export function getPackageUpload(uploadId: string): Promise<PackageUpload> {
  return request(`/api/v1/package-uploads/${encodeURIComponent(uploadId)}`);
}

export function uploadPackageArchive(uploadId: string, file: File): Promise<PackageUpload> {
  return request(`/api/v1/package-uploads/${encodeURIComponent(uploadId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/zip", "X-GitFerry-Archive-Size": String(file.size) },
    body: file,
  });
}

export function resumePackageUpload(uploadId: string): Promise<PackageUpload> {
  return request(`/api/v1/package-uploads/${encodeURIComponent(uploadId)}/resume`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
  });
}

export function browseConnection(
  key: string,
  value: { expected_revision: number; kind: BrowseKind; organization: string | null; page: number },
): Promise<BrowseResponse> {
  return request(`/api/v1/connections/${encodeURIComponent(key)}/browse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
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

export function preparePlan(selection: Record<string, unknown>): Promise<TransferPlan> {
  return request("/api/v1/plans/prepare", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection),
  });
}

export function listPlans(): Promise<TransferPlan[]> {
  return request("/api/v1/plans");
}

export function getPlan(planId: string): Promise<TransferPlan> {
  return request(`/api/v1/plans/${encodeURIComponent(planId)}`);
}

export function requestPlanPreview(planId: string, digest: string): Promise<TransferPlan> {
  return request(`/api/v1/plans/${encodeURIComponent(planId)}/preview`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ digest }),
  });
}

export function queuePlan(planId: string, digest: string, previewGeneration?: number): Promise<TransferPlan> {
  return request(`/api/v1/plans/${encodeURIComponent(planId)}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ digest, ...(previewGeneration === undefined ? {} : { preview_generation: previewGeneration }) }),
  });
}

export function cancelPlan(planId: string): Promise<TransferPlan> {
  return request(`/api/v1/plans/${encodeURIComponent(planId)}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}

export function listSourceExports(): Promise<SourceExportJob[]> {
  return request("/api/v1/exports");
}

export function listSourceExportRecipients(): Promise<SourceExportRecipient[]> {
  return request("/api/v1/exports/recipients");
}

export function createSourceExport(exportId: string, value: SourceExportRequest): Promise<SourceExportJob> {
  return request("/api/v1/exports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ export_id: exportId, request: value }),
  });
}

export function queueSourceExport(exportId: string): Promise<SourceExportJob> {
  return request(`/api/v1/exports/${encodeURIComponent(exportId)}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

export function getSourceExport(exportId: string): Promise<SourceExportJob> {
  return request(`/api/v1/exports/${encodeURIComponent(exportId)}`);
}

export function sourceExportDownloadUrl(exportId: string): string {
  return `/api/v1/exports/${encodeURIComponent(exportId)}/download`;
}

export function getInventoryCapabilities(): Promise<InventoryCapabilities> {
  return request("/api/v1/inventories/capabilities");
}

export function listInventories(): Promise<InventoryJobSummary[]> {
  return request("/api/v1/inventories");
}

export function getInventory(inventoryId: string): Promise<InventoryJob> {
  return request(`/api/v1/inventories/${encodeURIComponent(inventoryId)}`);
}

export function createInventory(inventoryId: string, value: InventoryRequest): Promise<InventoryJob> {
  return request("/api/v1/inventories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ inventory_id: inventoryId, request: value }),
  });
}

export function inventoryDocumentUrl(inventoryId: string): string {
  return `/api/v1/inventories/${encodeURIComponent(inventoryId)}/document`;
}

function readCookie(name: string): string | undefined {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const value = part.trim();
    if (value.startsWith(prefix)) return decodeURIComponent(value.slice(prefix.length));
  }
  return undefined;
}
