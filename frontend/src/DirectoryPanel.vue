<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";
import { ApiError, listConnections, type ConnectionProfile } from "./api";
import { exportDirectory, importDirectory, listDirectories, saveDirectory, type DirectoryRecord, type DirectorySource } from "./directory-api";
import { listIdentityRealms, type IdentityRealm } from "./identity-api";

const emit = defineEmits<{ (event: "session-expired"): void }>();
const opened = ref(false);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const notice = ref("");
const records = ref<DirectoryRecord[]>([]);
const profiles = ref<ConnectionProfile[]>([]);
const realms = ref<IdentityRealm[]>([]);
const editing = ref<DirectoryRecord>();
const importInput = ref<HTMLInputElement>();
const form = ref({
  instance_key: "",
  source_name: "",
  directoryRealm: "",
  type: "ldaps" as "ldap" | "ldaps",
  mode: "simple" as "simple" | "bindDN",
  host: "",
  port: 636,
  userDn: "",
  userFilter: "",
  emailAttribute: "mail",
  usernameAttribute: "uid",
  caSecretRef: "",
  userSearchBase: "",
  groupSearchBase: "",
  bindSecretRef: "",
});
const securityProtocol = computed(() => form.value.type === "ldap" ? "starttls" : "ldaps");
let mounted = true;

onUnmounted(() => { mounted = false; });

async function openAdministration(): Promise<void> {
  opened.value = true;
  await load();
}

async function load(): Promise<void> {
  loading.value = true; resetMessages();
  try {
    const [directoryList, connectionList, realmList] = await Promise.all([listDirectories(), listConnections(), listIdentityRealms()]);
    if (!mounted) return;
    records.value = directoryList;
    profiles.value = connectionList.filter((profile) => profile.enabled && profile.locally_approved);
    realms.value = realmList;
    if (!form.value.instance_key && profiles.value[0]) form.value.instance_key = profiles.value[0].instance_key;
    if (!form.value.directoryRealm && realms.value[0]) form.value.directoryRealm = realms.value[0].realm_key;
  } catch (cause) { handleError(cause, "Directory settings could not be loaded."); }
  finally { if (mounted) loading.value = false; }
}

function resetMessages(): void { error.value = ""; notice.value = ""; }

function resetForm(): void {
  editing.value = undefined;
  form.value = { instance_key: profiles.value[0]?.instance_key ?? "", source_name: "", directoryRealm: realms.value[0]?.realm_key ?? "", type: "ldaps", mode: "simple", host: "", port: 636, userDn: "", userFilter: "", emailAttribute: "mail", usernameAttribute: "uid", caSecretRef: "", userSearchBase: "", groupSearchBase: "", bindSecretRef: "" };
}

function edit(record: DirectoryRecord): void {
  editing.value = record;
  const source = record.source;
  form.value = { instance_key: record.instance_key, source_name: source.name, directoryRealm: source.directoryRealm, type: source.type, mode: source.bindSecretRef && !source.mode ? "bindDN" : "simple", host: source.host, port: source.port, userDn: source.userDn ?? "", userFilter: source.userFilter ?? "", emailAttribute: source.emailAttribute ?? "mail", usernameAttribute: source.usernameAttribute ?? "", caSecretRef: source.caSecretRef ?? "", userSearchBase: source.userSearchBase ?? "", groupSearchBase: source.groupSearchBase ?? "", bindSecretRef: source.bindSecretRef ?? "" };
}

function sourceValue(): DirectorySource {
  const value: DirectorySource = { name: form.value.source_name.trim(), type: form.value.type, directoryRealm: form.value.directoryRealm, host: form.value.host.trim(), port: Number(form.value.port) };
  if (form.value.mode === "simple") {
    Object.assign(value, { mode: "simple", securityProtocol: securityProtocol.value, userDn: form.value.userDn.trim(), userFilter: form.value.userFilter.trim(), emailAttribute: form.value.emailAttribute.trim(), usernameAttribute: form.value.usernameAttribute.trim() || undefined, caSecretRef: form.value.caSecretRef.trim() });
  } else {
    Object.assign(value, { userSearchBase: form.value.userSearchBase.trim() || undefined, groupSearchBase: form.value.groupSearchBase.trim() || undefined, bindSecretRef: form.value.bindSecretRef.trim() });
  }
  return value;
}

async function save(): Promise<void> {
  resetMessages(); saving.value = true;
  try {
    const saved = await saveDirectory({ instance_key: form.value.instance_key, source: sourceValue(), expected_revision: editing.value?.revision ?? null });
    records.value = [...records.value.filter((item) => !(item.instance_key === saved.instance_key && item.source_name === saved.source_name)), saved];
    notice.value = editing.value ? "Directory settings updated locally." : "Directory settings reviewed and saved locally.";
    edit(saved);
  } catch (cause) { handleError(cause, "Directory settings could not be saved."); }
  finally { saving.value = false; }
}

function chooseImport(): void { importInput.value?.click(); }

async function readImport(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  resetMessages();
  try {
    if (file.size > 64 * 1024) throw new Error("The blueprint is larger than 64 KiB.");
    if (!form.value.instance_key) throw new Error("Choose an approved target connection before importing.");
    const content = await file.text();
    if (/\b(?:password|bindPassword|token)\s*:/i.test(content)) throw new Error("Blueprint contains a password or token value; only secret references are accepted.");
    const imported = await importDirectory({ instance_key: form.value.instance_key, content, expected_revision: editing.value?.revision ?? null });
    records.value = [...records.value.filter((item) => !(item.instance_key === imported.instance_key && item.source_name === imported.source_name)), imported];
    edit(imported);
    notice.value = "Blueprint imported and saved locally. No remote Gitea settings were changed.";
  } catch (cause) { handleError(cause, "Blueprint import was rejected."); }
  finally { if (importInput.value) importInput.value.value = ""; }
}

async function download(record: DirectoryRecord): Promise<void> {
  resetMessages();
  try {
    const blueprint = await exportDirectory(record.instance_key, record.source_name);
    const url = URL.createObjectURL(new Blob([JSON.stringify(blueprint, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `${record.source_name}-instance-blueprint.json`; link.click(); URL.revokeObjectURL(url);
    notice.value = "Versioned InstanceBlueprint downloaded.";
  } catch (cause) { handleError(cause, "Blueprint could not be exported."); }
}

function handleError(cause: unknown, fallback: string): void {
  if (cause instanceof ApiError && cause.status === 401) { emit("session-expired"); return; }
  error.value = cause instanceof ApiError && cause.status === 409 ? "These directory settings changed elsewhere. Reload and edit the current revision." : cause instanceof ApiError && cause.message ? cause.message : cause instanceof Error ? cause.message : fallback;
}

function statusDetail(record: DirectoryRecord): string {
  if (record.bootstrap?.status === "confirmed") return "Confirmed locally: CLI readback verified source ID, name, type, and enabled state. Native login and full configuration remain separately qualified.";
  if (record.bootstrap?.status === "executing" || record.bootstrap?.status === "outcome_unknown") return "Stop and inspect the target locally before retrying; do not repeat creation.";
  return record.bootstrap?.reason ?? "Persisted local journal status; remote Gitea was not changed by this panel.";
}
</script>

<template>
  <section class="directory-panel" aria-labelledby="directories-title">
    <div class="directory-heading"><div><p class="kicker">Target-local authentication settings</p><h2 id="directories-title">Manage directories</h2><p class="supporting">Review source configuration for an approved connection. This panel never applies settings to remote Gitea.</p></div><div class="actions"><button type="button" :disabled="loading || saving" @click="openAdministration">{{ opened ? "Reload directories" : "Manage directories" }}</button><button v-if="opened" type="button" :disabled="saving" @click="chooseImport">Import blueprint</button><input ref="importInput" class="visually-hidden" type="file" accept="application/json,.json,.yaml,.yml,text/yaml" aria-label="InstanceBlueprint file" @change="readImport"></div></div>
    <p v-if="error" class="alert" role="alert">{{ error }}</p><p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="loading" class="status-line" aria-live="polite">Loading directory settings…</p>
    <template v-else-if="opened">
      <ul v-if="records.length" class="directory-list" aria-label="Saved directory settings"><li v-for="record in records" :key="`${record.instance_key}:${record.source_name}`" class="directory-card"><div class="directory-summary"><div><h3>{{ record.source_name }}</h3><p>{{ record.instance_key }} · {{ record.realm_key }} · Revision {{ record.revision }}</p><small v-if="record.source">{{ record.source.type.toUpperCase() }} / {{ record.source.bindSecretRef && !record.source.mode ? "bindDN" : "simple" }} · {{ record.source.host }}:{{ record.source.port }}</small></div><span class="state">{{ record.status }}</span></div><p v-if="record.bootstrap" class="bootstrap-status">Previous local bootstrap: {{ record.bootstrap.status }}. {{ statusDetail(record) }} This does not approve a later settings revision.</p><p v-if="record.prerequisites.length" class="prerequisites">Prerequisites: {{ record.prerequisites.join(" · ") }}</p><div class="actions"><button type="button" :disabled="saving" @click="edit(record)">Edit</button><button type="button" :disabled="saving" @click="download(record)">Download blueprint</button></div></li></ul><p v-else class="empty">No directory settings have been reviewed locally.</p>
      <form class="directory-form" aria-labelledby="directory-form-title" @submit.prevent="save"><h3 id="directory-form-title">{{ editing ? "Edit directory settings" : "Review directory settings" }}</h3><p class="supporting">Only secret references are accepted. Never enter a password value.</p><div class="form-grid"><label for="directory-instance">Approved target connection</label><select id="directory-instance" v-model="form.instance_key" :disabled="saving || Boolean(editing)" required><option value="" disabled>Select a connection</option><option v-for="profile in profiles" :key="profile.instance_key" :value="profile.instance_key">{{ profile.display_name }} ({{ profile.instance_key }})</option></select><label for="directory-source-name">Source name</label><input id="directory-source-name" v-model="form.source_name" :disabled="saving || Boolean(editing)" required maxlength="128" autocomplete="off"><label for="directory-realm">Registered realm</label><select id="directory-realm" v-model="form.directoryRealm" :disabled="saving || Boolean(editing)" required><option value="" disabled>Select a realm</option><option v-for="realm in realms" :key="realm.realm_key" :value="realm.realm_key">{{ realm.display_name }} ({{ realm.realm_key }})</option></select><label for="directory-type">Directory transport</label><select id="directory-type" v-model="form.type" :disabled="saving"><option value="ldaps">LDAPS</option><option value="ldap">LDAP with StartTLS</option></select><label for="directory-security">Security protocol</label><select id="directory-security" :value="securityProtocol" disabled><option value="ldaps">LDAPS</option><option value="starttls">StartTLS</option></select><label for="directory-mode">Authentication mode</label><select id="directory-mode" v-model="form.mode" :disabled="saving"><option value="simple">Simple</option><option value="bindDN">BindDN (manual Gitea UI)</option></select><label for="directory-host">Directory host</label><input id="directory-host" v-model="form.host" :disabled="saving" required autocomplete="off"><label for="directory-port">Directory port</label><input id="directory-port" v-model.number="form.port" :disabled="saving" type="number" min="1" max="65535" required>
      <template v-if="form.mode === 'simple'"><label for="directory-user-dn">User DN template</label><input id="directory-user-dn" v-model="form.userDn" :disabled="saving" required autocomplete="off"><label for="directory-user-filter">User filter</label><input id="directory-user-filter" v-model="form.userFilter" :disabled="saving" required autocomplete="off"><label for="directory-email">Email attribute</label><input id="directory-email" v-model="form.emailAttribute" :disabled="saving" required autocomplete="off"><label for="directory-username">Username attribute <span>(optional mapping)</span></label><input id="directory-username" v-model="form.usernameAttribute" :disabled="saving" autocomplete="off"><label for="directory-ca">CA secret reference</label><input id="directory-ca" v-model="form.caSecretRef" :disabled="saving" required maxlength="128" autocomplete="off"></template>
      <template v-else><label for="directory-user-search-base">User search base</label><input id="directory-user-search-base" v-model="form.userSearchBase" :disabled="saving" autocomplete="off"><label for="directory-group-search-base">Group search base</label><input id="directory-group-search-base" v-model="form.groupSearchBase" :disabled="saving" autocomplete="off"><label for="directory-bind-secret">Bind secret reference</label><input id="directory-bind-secret" v-model="form.bindSecretRef" :disabled="saving" required maxlength="128" autocomplete="off"></template></div><p v-if="form.mode === 'bindDN'" class="alert">BindDN automation is unsupported. Add this source manually in Gitea’s authenticated admin UI over HTTPS; the bind secret is never entered here.</p><p v-else class="supporting">Simple LDAP is ready after the operator performs the reviewed bootstrap on the target host using its local Gitea server account access. GitFerry does not use an API token here.</p><div class="actions"><button class="primary" type="submit" :disabled="saving || !profiles.length">{{ saving ? "Saving…" : editing ? "Save revision" : "Save reviewed settings" }}</button><button v-if="editing" type="button" :disabled="saving" @click="resetForm">New settings</button></div></form>
    </template>
  </section>
</template>

<style scoped>
.directory-panel { margin-top: 2rem; padding-top: 1.5rem; border-top: .0625rem solid #ccd4cd; }
.directory-heading, .directory-summary { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
.directory-heading h2 { margin-bottom: .35rem; font-size: 1.55rem; }
.directory-heading .supporting { max-width: 50rem; }
.directory-list { display: grid; gap: .85rem; margin: 1rem 0 0; padding: 0; list-style: none; }
.directory-card, .directory-form { padding: 1rem; border: .0625rem solid #c6d2c8; background: #f7faf6; }
.directory-form { margin-top: 1rem; background: #fbfcfa; }
.directory-form h3 { margin-top: 0; }
.directory-summary h3 { margin: 0; font-size: 1.08rem; }
.directory-summary p, .directory-summary small, .prerequisites { margin: .2rem 0; color: #526057; overflow-wrap: anywhere; }
.state { padding: .25rem .5rem; border: .0625rem solid #b2c5b5; border-radius: 1rem; color: #21462f; font-size: .78rem; font-weight: 700; white-space: nowrap; }
.form-grid { display: grid; gap: .45rem; }
.form-grid label { margin-top: .35rem; font-weight: 700; }
.form-grid label span { color: #5a695f; font-weight: 400; }
.empty, .status-line { margin: 1.25rem 0 0; color: #526057; }
.notice { margin: 1.25rem 0; padding: .7rem .9rem; border-left: .25rem solid #1f6540; background: #eaf3eb; color: #21462f; }
.visually-hidden { position: absolute; width: 1px; height: 1px; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media (max-width: 40rem) { .directory-heading, .directory-summary { flex-direction: column; } }
</style>
