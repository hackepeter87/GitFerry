<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ApiError, browseConnection, listConnections, type BrowseEntry, type ConnectionProfile } from "./api";

const emit = defineEmits<{ (event: "session-expired"): void }>();
const profiles = ref<ConnectionProfile[]>([]);
const selectedKey = ref("");
const organizations = ref<BrowseEntry[]>([]);
const repositories = ref<BrowseEntry[]>([]);
const organization = ref<string | null>(null);
const selectedRepositories = ref<Map<string, BrowseEntry>>(new Map());
const organizationPage = ref(1);
const repositoryPage = ref(1);
const organizationNext = ref<number | null>(null);
const repositoryNext = ref<number | null>(null);
const loading = ref(true);
const loadingOrganizations = ref(false);
const loadingRepositories = ref(false);
const error = ref("");
const generation = ref(0);
let sourceRequest = 0;
let disposed = false;
const browsePending = computed(() => loadingOrganizations.value || loadingRepositories.value);

const availableProfiles = computed(() => profiles.value.filter((profile) => profile.enabled && profile.locally_approved));
const selectedProfile = computed(() => availableProfiles.value.find((profile) => profile.instance_key === selectedKey.value));
const canBrowse = computed(() => Boolean(selectedProfile.value));

onMounted(loadSources);
onUnmounted(() => { disposed = true; generation.value++; sourceRequest++; });
watch(selectedKey, () => { resetSelection(); if (selectedKey.value) void loadOrganizations(1); });

async function loadSources(): Promise<void> {
  if (disposed || browsePending.value) return;
  const ticket = ++sourceRequest;
  loading.value = true; error.value = "";
  const previousKey = selectedKey.value;
  try {
    const values = await listConnections();
    if (disposed || ticket !== sourceRequest) return;
    profiles.value = values;
    const nextKey = availableProfiles.value.some((profile) => profile.instance_key === previousKey)
      ? previousKey : "";
    selectedKey.value = nextKey;
    if (nextKey && nextKey === previousKey) { resetSelection(); await loadOrganizations(1); }
  } catch (cause) { if (!disposed && ticket === sourceRequest) handleError(cause, "Source connections could not be loaded."); }
  finally { if (!disposed && ticket === sourceRequest) loading.value = false; }
}

function resetSelection(): void {
  generation.value++; loadingOrganizations.value = false; loadingRepositories.value = false;
  organizations.value = []; repositories.value = []; organization.value = null;
  selectedRepositories.value = new Map(); organizationPage.value = 1; repositoryPage.value = 1;
  organizationNext.value = null; repositoryNext.value = null;
}

async function loadOrganizations(page: number): Promise<void> {
  if (disposed || browsePending.value) return;
  const profile = selectedProfile.value; if (!profile) return;
  const ticket = ++generation.value; loadingOrganizations.value = true; error.value = "";
  try {
    const result = await browseConnection(profile.instance_key, { expected_revision: profile.revision, kind: "organizations", organization: null, page });
    if (ticket !== generation.value || result.instance_key !== selectedKey.value) return;
    organizations.value = page === 1 ? result.entries : [...organizations.value, ...result.entries];
    organizationPage.value = result.page; organizationNext.value = result.next_page;
  } catch (cause) { if (ticket === generation.value) handleError(cause, "Organizations could not be loaded."); }
  finally { if (ticket === generation.value) loadingOrganizations.value = false; }
}

async function chooseOrganization(value: string): Promise<void> {
  if (disposed || browsePending.value) return;
  organization.value = value || null; repositories.value = []; repositoryPage.value = 1; repositoryNext.value = null;
  if (organization.value) await loadRepositories(1);
}

async function loadRepositories(page: number): Promise<void> {
  if (disposed || browsePending.value) return;
  const profile = selectedProfile.value; if (!profile || !organization.value) return;
  const ticket = ++generation.value; loadingRepositories.value = true; error.value = "";
  try {
    const result = await browseConnection(profile.instance_key, { expected_revision: profile.revision, kind: "repositories", organization: organization.value, page });
    if (ticket !== generation.value || result.instance_key !== selectedKey.value || result.organization !== organization.value) return;
    repositories.value = page === 1 ? result.entries : [...repositories.value, ...result.entries];
    repositoryPage.value = result.page; repositoryNext.value = result.next_page;
  } catch (cause) { if (ticket === generation.value) handleError(cause, "Repositories could not be loaded."); }
  finally { if (ticket === generation.value) loadingRepositories.value = false; }
}

function toggleRepository(entry: BrowseEntry): void {
  const next = new Map(selectedRepositories.value); const id = repositoryKey(entry);
  next.has(id) ? next.delete(id) : next.set(id, entry); selectedRepositories.value = next;
}

function repositoryKey(entry: BrowseEntry): string { return `${selectedKey.value}:${entry.id}`; }
function handleError(cause: unknown, fallback: string): void {
  if (cause instanceof ApiError && cause.status === 401) { emit("session-expired"); return; }
  error.value = cause instanceof ApiError ? cause.message : fallback;
}
</script>

<template>
  <section class="source-selection" aria-labelledby="source-selection-title">
    <div class="connections-heading">
      <div><p class="kicker">Guided first import</p><h2 id="source-selection-title">Choose a source</h2><p class="supporting">Browse a locally approved Gitea connection and select repositories to include.</p></div>
      <button type="button" :disabled="loading || browsePending" @click="loadSources">Refresh sources</button>
    </div>
    <p v-if="error" class="alert" role="alert">{{ error }}</p>
    <p v-if="loading" class="status-line" role="status">Loading approved sources…</p>
    <template v-else>
      <label for="source-connection">Source connection</label>
      <select id="source-connection" v-model="selectedKey" :disabled="!availableProfiles.length || browsePending">
        <option value="" disabled>Select an approved connection</option>
        <option v-for="profile in availableProfiles" :key="profile.instance_key" :value="profile.instance_key">{{ profile.display_name }}</option>
      </select>
      <p v-if="!availableProfiles.length" class="empty">No enabled, locally approved source connections are available.</p>
      <div v-if="canBrowse" class="browse-grid">
        <div>
          <h3>Organizations</h3>
          <p v-if="loadingOrganizations" class="status-line" role="status">Loading organizations…</p>
          <select v-else id="source-organization" aria-label="Organization" :value="organization ?? ''" :disabled="browsePending" @change="chooseOrganization(($event.target as HTMLSelectElement).value)">
            <option value="">Select an organization</option><option v-for="entry in organizations" :key="entry.id" :value="entry.name">{{ entry.name }}</option>
          </select>
          <button v-if="organizationNext" type="button" :disabled="browsePending" @click="loadOrganizations(organizationNext!)">Load more organizations</button>
        </div>
        <div v-if="organization">
          <h3>Repositories</h3>
          <p v-if="loadingRepositories" class="status-line" role="status">Loading repositories…</p>
          <ul v-else class="selection-list"><li v-for="entry in repositories" :key="entry.id"><label><input type="checkbox" :checked="selectedRepositories.has(repositoryKey(entry))" @change="toggleRepository(entry)"><span>{{ entry.repository }}</span><small v-if="entry.private">Private</small></label></li></ul>
          <button v-if="repositoryNext" type="button" :disabled="browsePending" @click="loadRepositories(repositoryNext!)">Load more repositories</button>
          <p class="supporting" aria-live="polite">{{ selectedRepositories.size }} repositories selected.</p>
        </div>
      </div>
      <ul v-if="selectedRepositories.size" aria-label="Selected repositories" class="selection-list">
        <li v-for="[key, entry] in selectedRepositories" :key="key">
          {{ entry.repository }}
          <button type="button" :aria-label="`Remove ${entry.repository} from selection`" @click="toggleRepository(entry)">Remove</button>
        </li>
      </ul>
      <p class="notice">Export/import execution is not available yet. This selection stays in this browser view; reloading the page clears it. Content and identity prerequisites have not yet been inventoried.</p>
    </template>
  </section>
</template>

<style scoped>
.source-selection { margin-top: 2rem; padding-top: 1.5rem; border-top: .0625rem solid #ccd4cd; }
.browse-grid { display: grid; gap: 1rem; margin-top: 1.25rem; }
.browse-grid > div { padding: 1rem; border: .0625rem solid #c6d2c8; background: #fbfcfa; }
.browse-grid h3 { margin-top: 0; }
select { width: 100%; min-height: 2.9rem; padding: .65rem .75rem; border: .0625rem solid #829187; border-radius: .25rem; background: #fff; color: #18221c; }
.selection-list { display: grid; gap: .5rem; margin: 0 0 1rem; padding: 0; list-style: none; }
.selection-list label { display: flex; align-items: center; gap: .5rem; padding: .5rem; border: .0625rem solid #d0d7cf; }
.selection-list small { margin-left: auto; color: #526057; }
.browse-grid button { margin-top: .75rem; }
.empty { margin-top: 1rem; color: #526057; }
@media (min-width: 48rem) { .browse-grid { grid-template-columns: minmax(12rem, .8fr) minmax(0, 1.2fr); } }
</style>
