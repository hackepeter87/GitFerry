<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { ApiError, getPackageUpload, listPackageUploads, resumePackageUpload, uploadPackageArchive, type PackageUpload } from "./api";

const emit = defineEmits<{ (event: "session-expired"): void }>();
const uploads = ref<PackageUpload[]>([]);
const selected = ref<PackageUpload>();
const file = ref<File>();
const pendingId = ref("");
const pendingMeta = ref<{ name: string; size: number }>();
const busy = ref(false);
const loading = ref(true);
const error = ref("");
const notice = ref("");
const fileInput = ref<HTMLInputElement>();
const STORAGE_KEY = "gitferry.pending-package-upload";

let disposed = false;
let detailRequest = 0;
onMounted(() => { restorePending(); void load(); });
onUnmounted(() => { disposed = true; detailRequest++; });

function restorePending(): void {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "null") as { upload_id?: string; name?: string; size?: number } | null;
    if (typeof saved?.upload_id === "string" && /^upload-[0-9a-f-]{36}$/.test(saved.upload_id)
      && typeof saved.name === "string" && typeof saved.size === "number" && saved.size > 0) {
      pendingId.value = saved.upload_id; pendingMeta.value = { name: saved.name, size: saved.size };
    }
  } catch { /* Ignore malformed local state. */ }
}

async function load(): Promise<void> {
  loading.value = true; error.value = "";
  try {
    const values = await listPackageUploads();
    if (disposed) return;
    uploads.value = values;
    if (selected.value) await inspect(selected.value);
  }
  catch (cause) { handleError(cause, "Package uploads could not be loaded."); }
  finally { loading.value = false; }
}

function chooseFile(event: Event): void {
  const picked = (event.target as HTMLInputElement).files?.[0];
  if (!picked) return;
  file.value = picked;
  if (!pendingId.value || pendingMeta.value?.name !== picked.name || pendingMeta.value?.size !== picked.size)
    pendingId.value = `upload-${crypto.randomUUID()}`;
  try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ upload_id: pendingId.value, name: picked.name, size: picked.size })); }
  catch { /* The server receipt remains available through the upload list. */ }
  pendingMeta.value = { name: picked.name, size: picked.size };
  error.value = ""; notice.value = `Selected ${picked.name} (${formatBytes(picked.size)}).`;
}

async function submit(): Promise<void> {
  if (!file.value || !pendingId.value || busy.value) return;
  busy.value = true; detailRequest++; error.value = ""; notice.value = "Uploading package archive…";
  try {
    const value = await uploadPackageArchive(pendingId.value, file.value);
    if (disposed) return;
    replace(value); selected.value = value;
    try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Optional browser recovery state. */ }
    pendingId.value = ""; file.value = undefined; pendingMeta.value = undefined; if (fileInput.value) fileInput.value.value = "";
    notice.value = value.status === "ready" ? "Package received and validated. Review its scope below." : "Package received. Check its validation status below.";
  } catch (cause) { handleError(cause, "The package archive could not be received. You can retry with the same upload ID."); }
  finally { busy.value = false; }
}

async function inspect(upload: PackageUpload): Promise<void> {
  const request = ++detailRequest;
  selected.value = upload; error.value = "";
  try {
    const value = await getPackageUpload(upload.upload_id);
    if (disposed || request !== detailRequest) return;
    selected.value = value; replace(value);
  } catch (cause) { if (!disposed && request === detailRequest) handleError(cause, "Package details could not be loaded."); }
}

async function resume(): Promise<void> {
  if (!selected.value || busy.value) return;
  const request = ++detailRequest;
  busy.value = true; error.value = "";
  try {
    const value = await resumePackageUpload(selected.value.upload_id);
    if (disposed || request !== detailRequest) return;
    selected.value = value; replace(value);
    notice.value = value.status === "ready" ? "Package validation completed. Review its scope below." : "Validation checked. Review the current status below.";
  } catch (cause) { if (!disposed && request === detailRequest) handleError(cause, "Validation could not be resumed."); }
  finally { busy.value = false; }
}

function replace(value: PackageUpload): void {
  const index = uploads.value.findIndex((item) => item.upload_id === value.upload_id);
  if (index >= 0) uploads.value[index] = value; else uploads.value = [value, ...uploads.value];
}
function handleError(cause: unknown, fallback: string): void {
  if (disposed) return;
  if (cause instanceof ApiError && cause.status === 401) { emit("session-expired"); return; }
  error.value = cause instanceof ApiError && cause.message ? cause.message : fallback;
}
function formatBytes(value: number): string { return value < 1024 ? `${value} bytes` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1024 ** 2).toFixed(1)} MiB`; }
function statusLabel(value: PackageUpload["status"]): string { return ({ receiving: "Receiving", validating: "Validating", ready: "Ready for review", failed: "Failed" })[value]; }
</script>

<template>
  <section class="package-uploads-panel" aria-labelledby="package-uploads-title">
    <div class="package-heading"><div><p class="kicker">Target package intake</p><h2 id="package-uploads-title">Upload a migration package</h2><p class="supporting">GitFerry validates the package contents before any target connection or migration plan is used.</p></div><button type="button" :disabled="loading || busy" @click="load">Refresh uploads</button></div>
    <p v-if="error" class="alert" role="alert">{{ error }}</p><p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <form class="package-upload-form" @submit.prevent="submit">
      <label for="package-archive">Package archive (.zip)</label>
      <input id="package-archive" ref="fileInput" type="file" accept=".zip,application/zip" :disabled="busy" @change="chooseFile" />
      <p v-if="pendingId" class="supporting">Upload ID: <code>{{ pendingId }}</code><span v-if="file"> · {{ file.name }} · {{ formatBytes(file.size) }}</span><span v-else-if="pendingMeta"> · Saved file: {{ pendingMeta.name }} · {{ formatBytes(pendingMeta.size) }} (select it again to retry)</span></p>
      <p class="supporting">If the response is lost, this ID is retained so you can select the same file and retry explicitly.</p>
      <button class="primary" type="submit" :disabled="busy || !file || !pendingId">{{ busy ? "Sending…" : "Send package" }}</button>
    </form>
    <p v-if="loading" class="status-line" aria-live="polite">Loading package uploads…</p>
    <p v-else-if="!uploads.length" class="empty">No target packages have been uploaded.</p>
    <div v-else class="package-layout">
      <nav aria-label="Package uploads" class="package-list"><button v-for="upload in uploads" :key="upload.upload_id" type="button" class="package-row" :disabled="busy" :class="{ selected: selected?.upload_id === upload.upload_id }" @click="inspect(upload)"><span><strong>{{ upload.upload_id }}</strong><small>{{ formatBytes(upload.archive_size) }}</small></span><span class="package-status" :data-status="upload.status">{{ statusLabel(upload.status) }}</span></button></nav>
      <article v-if="selected" class="package-detail" aria-labelledby="package-detail-title"><div class="package-detail-heading"><div><p class="kicker">Selected upload</p><h3 id="package-detail-title">{{ selected.upload_id }}</h3><p class="supporting">Status: <strong>{{ statusLabel(selected.status) }}</strong></p></div><button v-if="selected.status === 'failed' || selected.status === 'validating'" type="button" :disabled="busy" @click="resume">Resume validation</button></div>
        <p v-if="selected.error_code" class="alert" role="alert">Validation failed: {{ selected.error_code }}</p>
        <dl class="package-facts"><div><dt>Archive size</dt><dd>{{ formatBytes(selected.archive_size) }}</dd></div><div><dt>Archive SHA-256</dt><dd><code>{{ selected.archive_sha256 ?? "Not available yet" }}</code></dd></div><div><dt>Package digest</dt><dd><code>{{ selected.package_digest ?? "Not available yet" }}</code></dd></div></dl>
        <section v-if="selected.summary" class="package-summary" aria-labelledby="package-summary-title"><h4 id="package-summary-title">Validated package scope</h4><p><strong>Recipient:</strong> {{ selected.summary.recipient.instance_key }} ({{ selected.summary.recipient.installation_marker }})</p><p><strong>Sender:</strong> {{ selected.summary.sender.instance_key }}</p><p><strong>Package:</strong> {{ selected.summary.package_id }}</p><p><strong>Selected repositories:</strong></p><ul><li v-for="repository in selected.summary.repositories" :key="repository">{{ repository }}</li></ul><p><strong>Metadata:</strong> {{ selected.summary.metadata_present ? "Present" : "Not present" }}</p><p class="supporting">The archive files have been validated. Local identity approval and transfer planning are still required; no target data has been written.</p><ul><li v-for="limitation in selected.summary.limitations" :key="limitation">{{ limitation }}</li></ul></section>
      </article>
    </div>
  </section>
</template>

<style scoped>
.package-uploads-panel { margin-top: 2rem; padding-top: 1.5rem; border-top: .0625rem solid #ccd4cd; }.package-heading, .package-detail-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:1rem; }.package-heading h2 { margin-bottom:.35rem; }.package-heading .supporting { max-width:48rem; margin-bottom:0; }.package-upload-form { display:grid; gap:.5rem; margin-top:1.25rem; padding:1rem; border: .0625rem solid #c6d2c8; background:#f4f7f2; }.package-upload-form label { font-weight:700; }.package-upload-form .primary { justify-self:start; margin-top:.5rem; }.package-layout { display:grid; grid-template-columns:minmax(13rem,18rem) minmax(0,1fr); gap:1rem; margin-top:1.25rem; }.package-list { display:grid; align-content:start; gap:.5rem; }.package-row { display:flex; align-items:flex-start; justify-content:space-between; gap:.75rem; padding:.75rem; text-align:left; }.package-row span:first-child { min-width:0; }.package-row strong, .package-row small { display:block; overflow-wrap:anywhere; }.package-row small { margin-top:.2rem; color:#5a695f; font-size:.8rem; }.package-row.selected { border-color:#1f6540; background:#eaf3eb; }.package-status { color:#315d43; font-size:.76rem; font-weight:750; white-space:nowrap; }.package-status[data-status="failed"] { color:#8d3b27; }.package-detail, .package-upload-form { overflow-wrap:anywhere; }.package-detail { min-width:0; padding:1rem; border:.0625rem solid #c6d2c8; background:#fbfcfa; }.package-detail h3 { margin:0 0 .35rem; font-size:1.4rem; }.package-facts { display:grid; gap:.4rem; margin:1rem 0; }.package-facts div { display:grid; grid-template-columns:minmax(8rem,auto) minmax(0,1fr); gap:.75rem; }.package-facts dt { color:#526057; font-weight:700; }.package-facts dd { margin:0; overflow-wrap:anywhere; }.package-summary { margin-top:1.25rem; padding-top:1rem; border-top:.0625rem solid #d7dfd8; }.package-summary h4 { margin:0 0 .55rem; }.package-summary ul { margin:0 0 .75rem; padding-left:1.2rem; }.empty, .status-line { margin:1.25rem 0 0; color:#526057; }.notice { color:#315d43; }@media (max-width:48rem) { .package-heading, .package-detail-heading { flex-direction:column; }.package-layout { grid-template-columns:1fr; } }
</style>
