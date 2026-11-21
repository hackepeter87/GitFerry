<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  ApiError, createInventory, getInventory, getInventoryCapabilities, inventoryDocumentUrl,
  listInventories, type BrowseEntry, type ConnectionProfile, type InventoryCapabilities,
  type InventoryJob, type InventoryJobSummary,
  type InventoryRequest,
} from "./api";

const props = defineProps<{
  sourceProfile?: ConnectionProfile;
  selectedRepositories: Map<string, BrowseEntry>;
  includeMetadata: boolean;
  includeMembers: boolean;
}>();
const emit = defineEmits<{
  (event: "session-expired"): void;
  (event: "reviewed", value: { inventory_id: string; digest: string } | null): void;
}>();

const FALLBACK_CLASSES = ["git_refs", "lfs", "wiki", "organizations", "teams", "permissions", "identities", "issues", "labels", "comments", "attachments", "releases", "release_assets", "pull_requests", "pull_comments", "reviews", "review_comments", "timelines", "reactions", "milestones", "other_content"];
const LABELS: Record<string, string> = {
  git_refs: "Git refs", lfs: "LFS objects", wiki: "Wiki", organizations: "Organizations", teams: "Teams",
  permissions: "Permissions", identities: "Identities", issues: "Issues", labels: "Labels", comments: "Comments",
  attachments: "Attachments", releases: "Releases", release_assets: "Release assets", pull_requests: "Pull requests",
  pull_comments: "Pull request comments", reviews: "Reviews", review_comments: "Review comments", timelines: "Timelines",
  reactions: "Reactions", milestones: "Milestones", other_content: "Other content",
};
const CORE = ["git_refs", "organizations", "teams", "permissions"];
const metadataClasses = ["issues", "comments"];

const capabilities = ref<InventoryCapabilities>({ classes: FALLBACK_CLASSES, supported: CORE, schema_version: 1 });
const selectedClasses = ref<string[]>([]);
const jobs = ref<InventoryJobSummary[]>([]);
const selectedId = ref("");
const selectedJob = ref<InventoryJob | null>(null);
const loading = ref(true);
const detailLoading = ref(false);
const saving = ref(false);
const error = ref("");
const notice = ref("");
let disposed = false;
let pollTimer: ReturnType<typeof window.setTimeout> | undefined;
let pollCount = 0;
let polling = false;
let generation = 0;

const selectedCount = computed(() => props.selectedRepositories.size);
const classes = computed(() => capabilities.value.classes.length ? capabilities.value.classes : FALLBACK_CLASSES);
const requiredClasses = computed(() => {
  const required = [...CORE];
  if (props.includeMembers || props.includeMetadata) required.push("identities");
  if (props.includeMetadata) required.push(...metadataClasses);
  return new Set(required);
});
const unsupportedSelected = computed(() => selectedClasses.value.filter((item) => !capabilities.value.supported.includes(item)));
const scopeKey = computed(() => JSON.stringify([...props.selectedRepositories.values()].map((entry) => ({ repository: entry.repository ?? entry.name ?? "", global_id: String(entry.id) })).sort((a, b) => a.repository.localeCompare(b.repository))));
const request = computed<InventoryRequest | null>(() => {
  if (!props.sourceProfile || !selectedCount.value) return null;
  const repositories = [...props.selectedRepositories.values()].map((entry) => ({ repository: entry.repository ?? entry.name ?? "", global_id: String(entry.id) })).sort((a, b) => a.repository.localeCompare(b.repository));
  if (repositories.some((item) => !item.repository || !item.global_id)) return null;
  return { source_instance_key: props.sourceProfile.instance_key, source_profile_revision: props.sourceProfile.revision, repositories, requested_classes: [...selectedClasses.value].sort() };
});
const currentRequestKey = computed(() => JSON.stringify(request.value));
const canQueue = computed(() => Boolean(request.value && !saving.value && !loading.value));
const currentMatches = computed(() => Boolean(selectedJob.value && request.value && requestsMatch(selectedJob.value.request, request.value)));
const canUse = computed(() => Boolean(selectedJob.value?.status === "ready" && selectedJob.value.document_digest && currentMatches.value));

onMounted(async () => { resetDefaults(); await load(); });
onUnmounted(() => { disposed = true; stopPolling(); });

watch(() => props.includeMetadata, () => { resetDefaults(); invalidateReview(); });
watch(() => props.includeMembers, () => { resetDefaults(); invalidateReview(); });
watch([scopeKey, currentRequestKey], () => invalidateReview());

function resetDefaults(): void {
  const next = [...CORE];
  if (props.includeMembers || props.includeMetadata) next.push("identities");
  if (props.includeMetadata) next.push(...metadataClasses);
  selectedClasses.value = [...new Set(next)].filter((item) => classes.value.includes(item));
}
function invalidateReview(): void { emit("reviewed", null); }
function requestsMatch(left: InventoryRequest, right: InventoryRequest): boolean {
  const repositories = (value: InventoryRequest) => value.repositories.map((item) => `${item.repository}\u0000${item.global_id}`).sort();
  return left.source_instance_key === right.source_instance_key
    && left.source_profile_revision === right.source_profile_revision
    && JSON.stringify(repositories(left)) === JSON.stringify(repositories(right))
    && JSON.stringify([...left.requested_classes].sort()) === JSON.stringify([...right.requested_classes].sort());
}
function stopPolling(): void { if (pollTimer !== undefined) { window.clearTimeout(pollTimer); pollTimer = undefined; } polling = false; pollCount = 0; }

async function load(): Promise<void> {
  invalidateReview(); stopPolling(); const current = ++generation; loading.value = true; error.value = "";
  try {
    const [available, values] = await Promise.all([getInventoryCapabilities(), listInventories()]);
    if (disposed || current !== generation) return;
    capabilities.value = available; jobs.value = values;
    resetDefaults();
    const remembered = sessionStorage.getItem("gitferry_selected_inventory_id");
    const first = values.find((item) => item.inventory_id === remembered)?.inventory_id ?? values[0]?.inventory_id;
    if (first) await select(first); else { selectedId.value = ""; selectedJob.value = null; }
    values.forEach(schedulePoll);
  } catch (cause) { if (!disposed) handleError(cause, "Inventories could not be loaded."); }
  finally { if (!disposed) loading.value = false; }
}

async function select(id: string): Promise<void> {
  invalidateReview(); stopPolling(); const current = ++generation; selectedId.value = id; selectedJob.value = null; detailLoading.value = true; error.value = ""; notice.value = "";
  sessionStorage.setItem("gitferry_selected_inventory_id", id);
  try { const value = await getInventory(id); if (disposed || current !== generation) return; selectedJob.value = value; schedulePoll({ ...value }); }
  catch (cause) { if (!disposed && current === generation) handleError(cause, "Inventory details could not be loaded."); }
  finally { if (!disposed && current === generation) detailLoading.value = false; }
}

async function queue(): Promise<void> {
  const value = request.value; if (!value || !canQueue.value) return;
  invalidateReview(); saving.value = true; error.value = ""; notice.value = ""; stopPolling(); const current = ++generation;
  const id = `inventory-${crypto.randomUUID()}`;
  try { const job = await createInventory(id, value); if (disposed || current !== generation) return; upsert(job); selectedId.value = id; selectedJob.value = job; sessionStorage.setItem("gitferry_selected_inventory_id", id); notice.value = "Inventory queued. The server continues collecting facts if this page is closed."; schedulePoll(job); }
  catch (cause) { if (!disposed) handleError(cause, "The inventory could not be queued. Refresh and try again."); }
  finally { if (!disposed) saving.value = false; }
}

function upsert(job: InventoryJobSummary): void { const index = jobs.value.findIndex((item) => item.inventory_id === job.inventory_id); if (index < 0) jobs.value = [job, ...jobs.value]; else jobs.value[index] = job; }
function schedulePoll(job: InventoryJobSummary): void {
  if (!(["queued", "running"] as string[]).includes(job.status) || disposed) return;
  if (polling) return;
  polling = true;
  const poll = async (): Promise<void> => {
    if (disposed) return;
    if (pollCount++ >= 40) { polling = false; notice.value = "Live refresh paused. The inventory remains durable; refresh to check its status."; return; }
    const pending = jobs.value.find((item) => item.status === "queued" || item.status === "running");
    if (!pending) { polling = false; return; }
    try { const value = await getInventory(pending.inventory_id); if (disposed) return; upsert(value); if (selectedId.value === value.inventory_id) selectedJob.value = value; }
    catch (cause) { polling = false; if (!disposed) handleError(cause, "Inventory status could not be refreshed."); return; }
    pollTimer = window.setTimeout(poll, 1500);
  };
  pollTimer = window.setTimeout(poll, 1200);
}
function handleError(cause: unknown, fallback: string): void { if (cause instanceof ApiError && cause.status === 401) { emit("session-expired"); return; } error.value = cause instanceof ApiError ? cause.message : fallback; }
function label(name: string): string { return LABELS[name] ?? name.replaceAll("_", " "); }
function statusLabel(status: string): string { return ({ queued: "Queued", running: "Collecting", ready: "Ready", blocked: "Blocked", failed: "Failed" } as Record<string, string>)[status] ?? "Unknown"; }
function formatBytes(size: number | null | undefined): string { if (size === null || size === undefined) return "Unknown size"; if (size < 1024) return `${size} bytes`; if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`; return `${(size / 1024 / 1024).toFixed(1)} MiB`; }
function useInventory(): void { if (!canUse.value || !selectedJob.value?.document_digest) return; emit("reviewed", { inventory_id: selectedJob.value.inventory_id, digest: selectedJob.value.document_digest }); notice.value = "Inventory approved for the current selection."; }
</script>

<template>
  <section class="inventory-panel" aria-labelledby="inventory-title">
    <div class="connections-heading"><div><p class="kicker">Read-only source facts</p><h3 id="inventory-title" tabindex="-1">Content inventory</h3><p class="supporting">Capture a durable, reviewable snapshot of the selected repositories before planning a transfer.</p></div><button type="button" :disabled="loading || saving" @click="load">Refresh inventories</button></div>
    <p v-if="error" class="alert" role="alert">{{ error }}</p><p v-if="notice" class="status-line" role="status">{{ notice }}</p>
    <p class="supporting">{{ selectedCount }} repositories selected. Profile revision {{ sourceProfile?.revision ?? "—" }} is bound into each request.</p>
    <fieldset class="inventory-options" :disabled="saving"><legend>Classes requested for transfer</legend><label v-for="contentClass in classes" :key="contentClass"><input v-model="selectedClasses" type="checkbox" :value="contentClass" :disabled="requiredClasses.has(contentClass)"> {{ label(contentClass) }}<span v-if="requiredClasses.has(contentClass)" class="supporting"> (required)</span><span v-else-if="!capabilities.supported.includes(contentClass)" class="supporting"> (catalogued, transfer unsupported)</span></label></fieldset>
    <p v-if="unsupportedSelected.length" class="alert" role="status">Selected classes will be observed for diagnosis but are not transfer-ready: {{ unsupportedSelected.map(label).join(", ") }}.</p>
    <button class="primary" type="button" :disabled="!canQueue" @click="queue">{{ saving ? "Queueing inventory…" : "Inspect selected content" }}</button>

    <div class="saved-inventories" aria-labelledby="saved-inventories-title"><h3 id="saved-inventories-title">Saved inventories</h3><p v-if="loading" class="status-line" role="status">Loading saved inventories…</p><p v-else-if="!jobs.length" class="empty">No content inventories have been captured.</p><ul v-else class="inventory-list"><li v-for="job in jobs" :key="job.inventory_id"><button type="button" class="inventory-row" :class="{ selected: job.inventory_id === selectedId }" :aria-current="job.inventory_id === selectedId ? 'true' : undefined" @click="select(job.inventory_id)"><span><strong>{{ job.inventory_id }}</strong><small>{{ job.created_at }}</small></span><span class="inventory-status" :data-status="job.status">{{ statusLabel(job.status) }}</span></button><p v-if="job.error_code" class="alert">Collection failed: {{ job.error_code }}</p></li></ul></div>

    <article v-if="selectedJob" class="inventory-detail" aria-labelledby="inventory-detail-title"><div class="inventory-detail-heading"><div><p class="kicker">Selected inventory</p><h3 id="inventory-detail-title">{{ selectedJob.inventory_id }}</h3><p class="supporting">Status: <strong>{{ statusLabel(selectedJob.status) }}</strong><span v-if="selectedJob.error_code"> · {{ selectedJob.error_code }}</span></p></div><button class="primary" type="button" :disabled="!canUse" @click="useInventory">Use this inventory</button></div><p v-if="detailLoading" class="status-line" role="status">Refreshing inventory details…</p><p v-if="selectedJob.status === 'blocked'" class="alert" role="status">This inventory is blocked by observed limitations. Review the blockers below; it is not a complete transfer.</p><p v-if="selectedJob.status === 'failed'" class="alert" role="status">The source observation failed. No inventory facts are available for review.</p><p v-if="selectedJob.status === 'ready' && !currentMatches" class="alert" role="status">This ready inventory belongs to a different source selection or class set. Capture a new inventory before using it.</p>
      <section v-if="selectedJob.summary" class="inventory-summary" aria-labelledby="inventory-summary-title"><h4 id="inventory-summary-title">Coverage summary</h4><dl><div><dt>Inventory complete</dt><dd>{{ selectedJob.summary.inventory_complete ? "Yes" : "No — some selected content was not observed" }}</dd></div><div><dt>Transfer ready</dt><dd>{{ selectedJob.summary.transfer_ready ? "Yes" : "No — review blockers" }}</dd></div><div><dt>Full transfer</dt><dd>{{ selectedJob.summary.full_transfer ? "Yes" : "No — selected scope has exclusions or limitations" }}</dd></div><div><dt>Known payload</dt><dd>{{ formatBytes(selectedJob.summary.known_payload_bytes) }} · {{ selectedJob.summary.objects_without_size }} objects without size</dd></div></dl><p class="supporting">Temporary space assumption: {{ selectedJob.summary.temporary_space_assumption }}</p><ul v-if="selectedJob.summary.blockers.length" class="limitation-list"><li v-for="blocker in selectedJob.summary.blockers" :key="`${blocker.repository}:${blocker.content_class}:${blocker.reason}`">{{ blocker.repository }} · {{ label(blocker.content_class) }}: {{ blocker.reason }}</li></ul><p v-if="selectedJob.summary.excluded_classes.length" class="supporting">Excluded classes remain visible below: {{ selectedJob.summary.excluded_classes.length }}.</p></section>
      <p v-if="selectedJob.document && selectedJob.document_digest"><a :href="inventoryDocumentUrl(selectedJob.inventory_id)">Download inventory document (JSON)</a> · Digest <code>{{ selectedJob.document_digest }}</code></p>
      <section v-if="selectedJob.document" aria-labelledby="observations-title"><h4 id="observations-title">Observed content by repository</h4><details v-for="repo in selectedJob.document.repositories" :key="repo.repository" class="inventory-repository"><summary>{{ repo.repository }} · {{ repo.classes.filter((item) => item.status === 'observed').length }}/{{ repo.classes.length }} classes observed</summary><p class="supporting">Native repository ID {{ repo.native_id }}. Git estimate {{ formatBytes(repo.git_size_estimate_bytes) }}.</p><ul class="class-observations"><li v-for="observation in repo.classes" :key="observation.content_class"><div><strong>{{ label(observation.content_class) }}</strong><span class="inventory-status" :data-status="observation.status">{{ observation.status }}</span><span v-if="observation.count !== null"> · {{ observation.count }} objects</span><span v-else> · count unknown</span></div><p v-if="observation.reason" class="supporting">{{ observation.reason }}</p><p v-if="observation.limitations.length" class="supporting">Limits: {{ observation.limitations.join("; ") }}</p><details v-if="observation.objects.length"><summary>Show up to 50 objects</summary><ul><li v-for="object in observation.objects.slice(0, 50)" :key="`${object.namespace}:${object.key}`"><code>{{ object.namespace }}/{{ object.key }}</code><span v-if="object.size_bytes !== null && object.size_bytes !== undefined"> · {{ formatBytes(object.size_bytes) }}</span><span v-if="object.parent"> · parent {{ object.parent.content_class }}/{{ object.parent.key }}</span></li></ul><p v-if="observation.objects.length > 50" class="supporting">Showing the first 50 objects; the document contains {{ observation.objects.length }}.</p></details></li></ul></details></section>
    </article>
  </section>
</template>

<style scoped>
.inventory-panel { margin-top: 1.5rem; padding: 1.25rem; border: .0625rem solid #c6d2c8; background: #f8faf7; }
.inventory-panel h3 { margin-top: 0; }
.inventory-options { display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: .55rem .9rem; margin: 1rem 0; padding: .75rem 1rem; border: .0625rem solid #d0d7cf; }
.inventory-options legend { padding: 0 .25rem; font-weight: 750; }
.inventory-options label { display: flex; gap: .45rem; align-items: center; }
.inventory-options input { width: auto; min-height: auto; }
.saved-inventories { margin-top: 1.5rem; padding-top: 1rem; border-top: .0625rem solid #ccd4cd; }
.inventory-list { display: grid; gap: .5rem; margin: 0; padding: 0; list-style: none; }
.inventory-row { display: flex; width: 100%; align-items: center; justify-content: space-between; gap: 1rem; text-align: left; }
.inventory-row.selected { border-color: #205f3b; background: #edf4ee; }
.inventory-row strong, .inventory-row small { display: block; overflow-wrap: anywhere; }
.inventory-row small { color: #526057; }
.inventory-status { margin-left: .6rem; color: #315d43; font-size: .8rem; font-weight: 750; text-transform: capitalize; }
.inventory-status[data-status="blocked"], .inventory-status[data-status="failed"] { color: #8d3b27; }
.inventory-detail { margin-top: 1.5rem; padding-top: 1rem; border-top: .0625rem solid #ccd4cd; }
.inventory-detail-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.inventory-summary, .inventory-repository { margin-top: 1rem; padding: .85rem 1rem; border: .0625rem solid #d0d7cf; background: #fff; }
.inventory-summary dl { display: grid; gap: .6rem; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); }
.inventory-summary dt { color: #526057; font-size: .8rem; font-weight: 750; }
.inventory-summary dd { margin: 0; }
.class-observations { display: grid; gap: .6rem; margin: .8rem 0 0; padding: 0; list-style: none; }
.class-observations > li { padding: .65rem; border-top: .0625rem solid #e1e6e0; }
.class-observations p { margin: .35rem 0 0; }
.inventory-repository summary { cursor: pointer; font-weight: 750; }
.limitation-list { padding-left: 1.25rem; }
code { display: inline; overflow-wrap: anywhere; }
</style>
