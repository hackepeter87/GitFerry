<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  ApiError,
  createConnection,
  exportConnections,
  importConnections,
  listConnections,
  replaceConnectionInstallation,
  approveConnection,
  retireConnection,
  testConnection,
  updateConnection,
  type ConnectionProfile,
} from "./api";

const emit = defineEmits<{ (event: "session-expired"): void }>();
const profiles = ref<ConnectionProfile[]>([]);
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const notice = ref("");
const editingKey = ref<string | null>(null);
const form = ref({ display_name: "", base_url: "https://", installation_marker: "", credential_ref: "", ca_ref: "" });
const rebuildKey = ref<string | null>(null);
const rebuildMarker = ref("");
const importInput = ref<HTMLInputElement>();

onMounted(load);

async function load(): Promise<void> {
  loading.value = true; error.value = "";
  try { profiles.value = await listConnections(); }
  catch (cause) { handleError(cause, "Connections could not be loaded."); }
  finally { loading.value = false; }
}

function resetForm(): void {
  editingKey.value = null;
  form.value = { display_name: "", base_url: "https://", installation_marker: "", credential_ref: "", ca_ref: "" };
}

function edit(profile: ConnectionProfile): void {
  editingKey.value = profile.instance_key;
  form.value = { display_name: profile.display_name, base_url: profile.base_url, installation_marker: profile.installation_marker, credential_ref: profile.credential_ref, ca_ref: profile.ca_ref ?? "" };
}

async function save(): Promise<void> {
  error.value = ""; notice.value = ""; saving.value = true;
  try {
    const value = { display_name: form.value.display_name.trim(), credential_ref: form.value.credential_ref.trim(), ca_ref: form.value.ca_ref.trim() || null };
    const saved = editingKey.value
      ? await updateConnection(editingKey.value, { ...value, expected_revision: currentProfile()?.revision ?? 0 })
      : await createConnection({ ...value, base_url: form.value.base_url.trim(), installation_marker: form.value.installation_marker.trim() });
    replaceProfile(saved); notice.value = editingKey.value ? "Connection updated." : "Connection added."; resetForm();
  } catch (cause) { handleError(cause, "Connection could not be saved."); }
  finally { saving.value = false; }
}

async function retire(profile: ConnectionProfile): Promise<void> {
  if (!window.confirm(`Retire “${profile.display_name}”? It will no longer be available for transport.`)) return;
  error.value = ""; saving.value = true;
  try { replaceProfile(await retireConnection(profile.instance_key, profile.revision)); notice.value = "Connection retired."; }
  catch (cause) { handleError(cause, "Connection could not be retired."); }
  finally { saving.value = false; }
}

async function rebuild(profile: ConnectionProfile): Promise<void> {
  if (!rebuildMarker.value.trim()) return;
  error.value = ""; saving.value = true;
  try {
    await replaceConnectionInstallation(profile.instance_key, profile.revision, rebuildMarker.value.trim());
    await load();
    notice.value = "Installation identity replaced.";
    rebuildKey.value = null;
    rebuildMarker.value = "";
  }
  catch (cause) { handleError(cause, "Installation identity could not be replaced."); }
  finally { saving.value = false; }
}

async function verify(profile: ConnectionProfile): Promise<void> {
  error.value = ""; notice.value = ""; saving.value = true;
  try {
    const evidence = await testConnection(profile.instance_key, profile.revision);
    const details = `Gitea ${evidence.version} · account ${evidence.login} (${evidence.account_id})`;
    if (!evidence.requires_approval) {
      notice.value = `Identity re-checked: ${details}. No approval change was needed.`;
      return;
    }
    if (!window.confirm(`Connection evidence received: ${details}. Approve this identity for transport?`)) { notice.value = `Evidence received: ${details}. Approval remains pending.`; return; }
    replaceProfile(await approveConnection(profile.instance_key, profile.revision, evidence.evidence_digest));
    notice.value = "Connection evidence approved.";
  } catch (cause) { handleError(cause, "Connection test could not complete. Check the mounted credential and CA references, then try again."); }
  finally { saving.value = false; }
}

async function downloadExport(): Promise<void> {
  try {
    const value = await exportConnections();
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = "gitferry-connections.json"; link.click(); URL.revokeObjectURL(url);
    notice.value = "Configuration exported.";
  } catch (cause) { handleError(cause, "Configuration could not be exported."); }
}

function chooseImport(): void { importInput.value?.click(); }
async function readImport(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]; if (!file) return;
  error.value = "";
  try {
    if (file.size > 256 * 1024) throw new Error("too large");
    const imported = await importConnections(await file.text());
    profiles.value = [...profiles.value, ...imported];
    notice.value = "Configuration imported. Local approval is pending for imported connections.";
  } catch (cause) {
    handleError(cause, "Choose a GitFerry connection export file.");
    if (error.value) error.value = `Import rejected. ${error.value}`;
  }
  finally { if (importInput.value) importInput.value.value = ""; }
}

function currentProfile(): ConnectionProfile | undefined { return profiles.value.find((item) => item.instance_key === editingKey.value); }
function replaceProfile(profile: ConnectionProfile): void {
  const index = profiles.value.findIndex((item) => item.instance_key === profile.instance_key);
  if (index >= 0) profiles.value[index] = profile;
  else profiles.value = [...profiles.value, profile];
}
function handleError(cause: unknown, fallback: string): void { if (cause instanceof ApiError && cause.status === 401) { emit("session-expired"); return; } error.value = cause instanceof ApiError && cause.status === 409 ? "This connection changed elsewhere. Reload and try again." : cause instanceof ApiError && cause.message ? cause.message : fallback; }
</script>

<template>
  <section class="connections-panel" aria-labelledby="connections-title">
    <div class="connections-heading"><div><p class="kicker">Transport endpoints</p><h2 id="connections-title">Connections</h2><p class="supporting">Store endpoint details and references to locally mounted credentials. Secret values never enter GitFerry.</p></div><div class="actions"><button type="button" @click="downloadExport">Export</button><button type="button" @click="chooseImport">Import</button><input ref="importInput" class="visually-hidden" type="file" accept="application/json,.json" @change="readImport"></div></div>
    <p v-if="error" class="alert" role="alert">{{ error }}</p><p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="loading" class="status-line" aria-live="polite">Loading connections…</p>
    <p v-else-if="!profiles.length" class="empty">No connections yet. Add one below.</p>
    <ul v-else class="connection-list" aria-label="Configured connections"><li v-for="profile in profiles" :key="profile.instance_key" class="connection-card" :class="{ retired: !profile.enabled }"><div class="connection-summary"><div><h3>{{ profile.display_name }}</h3><p>{{ profile.base_url }}</p><small>Identity {{ profile.installation_marker }} · Revision {{ profile.revision }}</small></div><span class="state">{{ profile.enabled ? profile.locally_approved ? "Approved" : "Approval pending" : "Retired" }}</span></div><p class="refs">Credential reference: <code>{{ profile.credential_ref }}</code><span v-if="profile.ca_ref"> · CA reference: <code>{{ profile.ca_ref }}</code></span></p><div v-if="profile.enabled" class="actions"><button type="button" :disabled="saving" @click="verify(profile)">{{ profile.locally_approved ? "Re-check identity" : "Test connection" }}</button><button type="button" :disabled="saving" @click="edit(profile)">Edit</button><button type="button" :disabled="saving" @click="rebuildKey = profile.instance_key; rebuildMarker = ''">Replace installation identity</button><button type="button" :disabled="saving" @click="retire(profile)">Retire</button></div><div v-if="rebuildKey === profile.instance_key" class="rebuild-box"><p><strong>Rebuild confirmation</strong></p><p>This preserves the old instance key and mappings, but records a new installation identity. Existing transport data will not be silently reused.</p><label :for="`rebuild-${profile.instance_key}`">New installation marker</label><input :id="`rebuild-${profile.instance_key}`" v-model="rebuildMarker" autocomplete="off" required @keyup.enter="rebuild(profile)"><div class="actions"><button type="button" class="primary" :disabled="saving || !rebuildMarker.trim()" @click="rebuild(profile)">Confirm replacement</button><button type="button" :disabled="saving" @click="rebuildKey = null">Cancel</button></div></div></li></ul>
    <form class="connection-form" aria-labelledby="connection-form-title" @submit.prevent="save">
      <h3 id="connection-form-title">{{ editingKey ? "Edit connection" : "Add connection" }}</h3>
      <div class="form-grid">
        <label for="connection-name">Display name</label>
        <input id="connection-name" v-model="form.display_name" required maxlength="120">
        <label for="connection-url">Gitea HTTPS URL</label>
        <input id="connection-url" v-model="form.base_url" type="url" required :readonly="Boolean(editingKey)">
        <p v-if="editingKey" class="supporting">The endpoint URL is fixed for this identity. Use “Replace installation identity” to move to a new Gitea installation.</p>
        <template v-if="!editingKey">
          <label for="connection-marker">Installation marker</label>
          <input id="connection-marker" v-model="form.installation_marker" required maxlength="64" autocomplete="off">
        </template>
        <label for="connection-credential">Credential reference</label>
        <input id="connection-credential" v-model="form.credential_ref" required maxlength="64" autocomplete="off">
        <label for="connection-ca">CA reference <span>(optional)</span></label>
        <input id="connection-ca" v-model="form.ca_ref" maxlength="64" autocomplete="off">
      </div>
      <p class="supporting">References point to operator-managed local mounts; enter aliases only, never tokens or passwords.</p>
      <div class="actions">
        <button class="primary" type="submit" :disabled="saving">{{ saving ? "Saving…" : editingKey ? "Save changes" : "Add connection" }}</button>
        <button v-if="editingKey" type="button" :disabled="saving" @click="resetForm">Cancel edit</button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.connections-panel { margin-top: 2rem; padding-top: 1.5rem; border-top: 0.0625rem solid #ccd4cd; }
.connections-heading, .connection-summary { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
.connections-heading h2 { margin-bottom: .35rem; font-size: 1.55rem; }
.connections-heading .supporting { max-width: 48rem; margin-bottom: 0; }
.actions { display: flex; flex-wrap: wrap; gap: .6rem; align-items: flex-start; }
.connection-list { display: grid; gap: .85rem; margin: 1.25rem 0 0; padding: 0; list-style: none; }
.connection-card { padding: 1rem; border: .0625rem solid #c6d2c8; border-radius: .25rem; background: #f7faf6; }
.connection-card.retired { opacity: .7; }
.connection-summary h3 { margin: 0; font-size: 1.08rem; }
.connection-summary p, .refs { margin: .2rem 0; overflow-wrap: anywhere; color: #526057; }
.connection-summary small { color: #5a695f; }
.state { padding: .25rem .5rem; border: .0625rem solid #b2c5b5; border-radius: 1rem; color: #21462f; font-size: .78rem; font-weight: 700; white-space: nowrap; }
.refs { margin-top: .75rem; font-size: .9rem; }
.refs code { display: inline; color: #402d1d; }
.connection-form, .rebuild-box { margin-top: 1.5rem; padding: 1rem; border: .0625rem solid #c6d2c8; background: #fbfcfa; }
.connection-form h3 { margin-top: 0; }
.form-grid { display: grid; gap: .45rem; }
.form-grid label { margin-top: .35rem; font-weight: 700; }
.form-grid label span { color: #5a695f; font-weight: 400; }
.rebuild-box { border-left: .25rem solid #c4691d; background: #f8f1e6; }
.rebuild-box p { margin-bottom: .65rem; }
.rebuild-box label { display: block; margin-bottom: .4rem; font-weight: 700; }
.empty, .status-line { margin: 1.25rem 0 0; color: #526057; }
.notice { margin: 1.25rem 0; padding: .7rem .9rem; border-left: .25rem solid #1f6540; background: #eaf3eb; color: #21462f; }
.visually-hidden { position: absolute; width: 1px; height: 1px; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media (max-width: 40rem) { .connections-heading, .connection-summary { flex-direction: column; } }
</style>
