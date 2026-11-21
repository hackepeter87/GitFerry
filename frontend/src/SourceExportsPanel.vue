<script setup lang="ts">
import InventoryPanel from "./InventoryPanel.vue";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  ApiError,
  createSourceExport,
  getSourceExport,
  listSourceExportRecipients,
  listSourceExports,
  queueSourceExport,
  sourceExportDownloadUrl,
  type BrowseEntry,
  type ConnectionProfile,
  type SourceExportJob,
  type SourceExportRecipient,
  type SourceExportRequest,
} from "./api";

const props = defineProps<{
  sourceProfile?: ConnectionProfile;
  selectedRepositories: Map<string, BrowseEntry>;
}>();
const emit = defineEmits<{ (event: "session-expired"): void }>();

const recipients = ref<SourceExportRecipient[]>([]);
const jobs = ref<SourceExportJob[]>([]);
const recipientKey = ref("");
const reviewedInventory = ref<{ inventory_id: string; digest: string } | null>(null);
const includeMetadata = ref(true);
const includeMembers = ref(false);
const loading = ref(true);
const saving = ref(false);
const message = ref("");
const error = ref("");
const activePolls = new Set<string>();
const pendingExportId = ref(sessionStorage.getItem("gitferry_pending_export_id") ?? "");
const pendingRequestJson = ref(sessionStorage.getItem("gitferry_pending_export_request") ?? "");
let disposed = false;

const availableRecipients = computed(() => recipients.value.filter((item) => item.instance_key !== props.sourceProfile?.instance_key));
const selectedCount = computed(() => props.selectedRepositories.size);
const canCreate = computed(() => Boolean(props.sourceProfile && recipientKey.value && selectedCount.value && reviewedInventory.value && !saving.value));

onMounted(load);
onUnmounted(() => { disposed = true; });
watch(() => props.sourceProfile?.instance_key, () => {
  reviewedInventory.value = null;
  if (!availableRecipients.value.some((item) => item.instance_key === recipientKey.value)) recipientKey.value = "";
});

async function load(): Promise<void> {
  loading.value = true; error.value = "";
  try {
    const [recipientValues, jobValues] = await Promise.all([listSourceExportRecipients(), listSourceExports()]);
    if (disposed) return;
    recipients.value = recipientValues;
    jobs.value = jobValues;
    for (const job of jobValues) schedulePoll(job);
  } catch (cause) { handleError(cause, "Source exports could not be loaded."); }
  finally { if (!disposed) loading.value = false; }
}

function makeRequest(): SourceExportRequest | null {
  const profile = props.sourceProfile;
  if (!profile || !recipientKey.value || !selectedCount.value || !reviewedInventory.value) return null;
  const repositories = [...props.selectedRepositories.values()].map((entry) => ({
    repository: entry.repository ?? entry.name ?? "",
    global_id: String(entry.id),
  })).sort((a, b) => a.repository.localeCompare(b.repository));
  if (repositories.some((entry) => !entry.repository || !entry.global_id)) return null;
  return {
    version: 2,
    inventory_id: reviewedInventory.value.inventory_id,
    inventory_digest: reviewedInventory.value.digest,
    source_instance_key: profile.instance_key,
    source_profile_revision: profile.revision,
    recipient_instance_key: recipientKey.value,
    repositories,
    include_metadata: includeMetadata.value,
    include_members: includeMembers.value,
  };
}

async function createAndQueue(): Promise<void> {
  const request = makeRequest();
  if (!request || saving.value) return;
  saving.value = true; error.value = ""; message.value = "";
  const requestJson = JSON.stringify(request);
  const exportId = pendingRequestJson.value === requestJson && pendingExportId.value
    ? pendingExportId.value : `export-${crypto.randomUUID()}`;
  pendingExportId.value = exportId;
  pendingRequestJson.value = requestJson;
  try {
    sessionStorage.setItem("gitferry_pending_export_id", exportId);
    sessionStorage.setItem("gitferry_pending_export_request", requestJson);
    const queued = await createSourceExport(exportId, request);
    clearPending();
    upsert(queued); schedulePoll(queued);
    message.value = `Export ${queued.export_id} was queued.`;
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) { emit("session-expired"); return; }
    // The ID is deterministic for this attempt; reload can discover a job after a lost response.
    await reloadJobs();
    error.value = cause instanceof ApiError ? cause.message : "The source export could not be queued.";
  } finally { saving.value = false; }
}

function clearPending(): void {
  pendingExportId.value = "";
  pendingRequestJson.value = "";
  sessionStorage.removeItem("gitferry_pending_export_id");
  sessionStorage.removeItem("gitferry_pending_export_request");
}

async function start(job: SourceExportJob): Promise<void> {
  if (saving.value || (job.status !== "prepared" && job.status !== "failed")) return;
  saving.value = true; error.value = "";
  try { const queued = await queueSourceExport(job.export_id); upsert(queued); schedulePoll(queued); }
  catch (cause) { handleError(cause, "The source export could not be queued."); }
  finally { saving.value = false; }
}

async function reloadJobs(): Promise<void> {
  try { const values = await listSourceExports(); jobs.value = values; values.forEach(schedulePoll); }
  catch (cause) { handleError(cause, "Saved source exports could not be refreshed."); }
}

function upsert(job: SourceExportJob): void {
  const index = jobs.value.findIndex((item) => item.export_id === job.export_id);
  if (index < 0) jobs.value = [job, ...jobs.value]; else jobs.value[index] = job;
}

function schedulePoll(job: SourceExportJob): void {
  if ((job.status !== "queued" && job.status !== "running") || activePolls.has(job.export_id)) return;
  activePolls.add(job.export_id);
  let attempts = 0;
  const poll = async (): Promise<void> => {
    if (disposed || attempts++ >= 40) { activePolls.delete(job.export_id); return; }
    try {
      const next = await getSourceExport(job.export_id);
      if (disposed) return;
      upsert(next);
      if (next.status === "queued" || next.status === "running") window.setTimeout(poll, 1500);
      else activePolls.delete(job.export_id);
    } catch (cause) { activePolls.delete(job.export_id); handleError(cause, "Export status could not be refreshed."); }
  };
  window.setTimeout(poll, 1200);
}

function handleError(cause: unknown, fallback: string): void {
  if (cause instanceof ApiError && cause.status === 401) { emit("session-expired"); return; }
  error.value = cause instanceof ApiError ? cause.message : fallback;
}

function formatSize(size: number | null): string {
  if (size === null) return "Unknown size";
  if (size < 1024) return `${size} bytes`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
  return `${(size / 1024 / 1024).toFixed(1)} MiB`;
}
</script>

<template>
  <section class="exports-panel" aria-labelledby="source-export-title">
    <div class="connections-heading">
      <div><p class="kicker">Export repositories</p><h3 id="source-export-title">Create a source export</h3><p class="supporting">Save selected repositories in an archive for the chosen recipient.</p></div>
      <button type="button" :disabled="loading || saving" @click="load">Refresh exports</button>
    </div>
    <p v-if="error" class="alert" role="alert">{{ error }}</p>
    <p v-if="message" class="status-line" role="status">{{ message }}</p>
    <p class="supporting">Current selection: {{ selectedCount }} repositories. Source profile revision {{ sourceProfile?.revision ?? "—" }} is captured in the immutable request.</p>
    <label for="export-recipient">Recipient registration</label>
    <select id="export-recipient" v-model="recipientKey" :disabled="loading || saving || !availableRecipients.length">
      <option value="">Select a recipient registration</option>
      <option v-for="recipient in availableRecipients" :key="recipient.instance_key" :value="recipient.instance_key">{{ recipient.display_name }} ({{ recipient.installation_marker }})</option>
    </select>
    <p v-if="!availableRecipients.length" class="empty">No other local recipient registrations are available. Registrations may be unapproved or offline; export only requires a local registration.</p>
    <fieldset class="export-options">
      <legend>Archive contents</legend>
      <label><input v-model="includeMetadata" type="checkbox" :disabled="saving"> Include issues and normal comments</label>
      <label><input v-model="includeMembers" type="checkbox" :disabled="saving"> Include membership records</label>
      <p class="supporting">The archive includes ordinary Git content and supported organization/team structure. LFS, wikis, attachments, releases and reviews are not included. Membership records are proposals; target identities and permissions need local approval.</p>
    </fieldset>
    <InventoryPanel v-if="sourceProfile" :source-profile="sourceProfile" :selected-repositories="selectedRepositories" :include-metadata="includeMetadata" :include-members="includeMembers" @reviewed="reviewedInventory = $event" @session-expired="emit('session-expired')" />
    <p v-if="!reviewedInventory" class="supporting">Inspect and review the current inventory before queueing an export.</p>
    <button class="primary" type="button" :disabled="!canCreate" @click="createAndQueue">{{ saving ? "Queueing export…" : "Create and queue export" }}</button>

    <div class="saved-exports" aria-labelledby="saved-exports-title">
      <h3 id="saved-exports-title">Saved exports</h3>
      <p v-if="loading" class="status-line" role="status">Loading saved exports…</p>
      <p v-else-if="!jobs.length" class="empty">No source exports have been prepared.</p>
      <ul v-else class="export-list">
        <li v-for="job in jobs" :key="job.export_id">
          <div><strong>{{ job.export_id }}</strong><span class="export-status" :data-status="job.status">{{ job.status === "prepared" ? "prepared, not queued" : job.status }}</span></div>
          <p class="supporting">{{ job.request.repositories.length }} repositories · {{ job.request.source_instance_key }} → {{ job.request.recipient_instance_key }}</p>
          <p v-if="job.error_code" class="alert">Export failed: {{ job.error_code }}</p>
          <button v-if="job.status === 'prepared' || job.status === 'failed'" type="button" :disabled="saving" @click="start(job)">{{ job.status === "failed" ? "Retry this export" : "Start export" }}</button>
          <template v-if="job.status === 'ready'">
            <p class="supporting">SHA-256: <code>{{ job.archive_sha256 }}</code> · {{ formatSize(job.archive_size) }}</p>
            <a :href="sourceExportDownloadUrl(job.export_id)">Download archive</a>
          </template>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.exports-panel { margin-top: 1.5rem; padding: 1.25rem; border: .0625rem solid #c6d2c8; background: #f8faf7; }
.exports-panel h3 { margin-top: 0; }
.exports-panel select { width: 100%; min-height: 2.9rem; margin: .35rem 0 1rem; padding: .65rem .75rem; border: .0625rem solid #829187; border-radius: .25rem; background: #fff; color: #18221c; }
.export-options { display: grid; gap: .6rem; margin: 1rem 0; padding: .75rem 1rem; border: .0625rem solid #d0d7cf; }
.export-options legend { padding: 0 .25rem; font-weight: 750; }
.export-options label { display: flex; gap: .5rem; align-items: center; }
.export-options input { width: auto; min-height: auto; }
.saved-exports { margin-top: 1.5rem; padding-top: 1rem; border-top: .0625rem solid #ccd4cd; }
.export-list { display: grid; gap: .75rem; margin: 0; padding: 0; list-style: none; }
.export-list li { padding: .8rem; border: .0625rem solid #d0d7cf; background: #fff; }
.export-list strong { overflow-wrap: anywhere; }
.export-status { margin-left: .75rem; color: #315d43; font-size: .8rem; font-weight: 750; text-transform: capitalize; }
.export-status[data-status="failed"] { color: #8d3b27; }
.export-list .alert { margin: .7rem 0; }
code { display: inline; overflow-wrap: anywhere; }
</style>
