<script setup lang="ts">
import { onUnmounted, ref } from "vue";
import { ApiError, listConnections, type ConnectionProfile } from "./api";
import {
  checkIdentityAccount,
  createIdentityCandidate,
  createIdentityRealm,
  decideIdentityCandidate,
  listIdentityCandidates,
  listIdentityEvents,
  listIdentityRealms,
  type IdentityCandidate,
  type IdentityEvent,
  type IdentityRealm,
} from "./identity-api";

const emit = defineEmits<{ (event: "session-expired"): void }>();
const profiles = ref<ConnectionProfile[]>([]);
const realms = ref<IdentityRealm[]>([]);
const candidates = ref<IdentityCandidate[]>([]);
const events = ref<Record<string, IdentityEvent[]>>({});
const loading = ref(false);
const opened = ref(false);
const saving = ref(false);
const error = ref("");
const notice = ref("");
const checked = ref<{ accountId: string; login: string; digest: string; profileRevision: number }>();
const realmForm = ref({ realm_key: "", display_name: "", external_marker: "" });
const form = ref({
  target_instance_key: "",
  target_login: "",
  credential_ref: "",
  realm_key: "",
  source_login: "",
  external_id: "",
  ou_path: "",
  email: "",
  issue_author: true,
  comment_author: true,
  team_member: true,
});
let formGeneration = 0;
let mounted = true;

async function openAdministration(): Promise<void> { opened.value = true; await load(); }
onUnmounted(() => { mounted = false; formGeneration += 1; });

function invalidateCheck(): void { checked.value = undefined; formGeneration += 1; }

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [connectionList, realmList, candidateList] = await Promise.all([listConnections(), listIdentityRealms(), listIdentityCandidates()]);
    profiles.value = connectionList.filter((profile) => profile.enabled && profile.locally_approved);
    realms.value = realmList;
    candidates.value = candidateList;
    if (!form.value.target_instance_key && profiles.value[0]) form.value.target_instance_key = profiles.value[0].instance_key;
    if (!form.value.realm_key && realms.value[0]) form.value.realm_key = realms.value[0].realm_key;
  } catch (cause) { handleError(cause, "Identity administration could not be loaded."); }
  finally { loading.value = false; }
}

async function registerRealm(): Promise<void> {
  if (!realmForm.value.realm_key.trim() || !realmForm.value.display_name.trim() || !realmForm.value.external_marker.trim()) return;
  error.value = ""; notice.value = ""; saving.value = true;
  try {
    const realm = await createIdentityRealm({ realm_key: realmForm.value.realm_key.trim(), display_name: realmForm.value.display_name.trim(), external_marker: realmForm.value.external_marker.trim() });
    realms.value = [...realms.value.filter((item) => item.realm_key !== realm.realm_key), realm];
    form.value.realm_key = realm.realm_key;
    realmForm.value = { realm_key: "", display_name: "", external_marker: "" };
    notice.value = "Identity realm registered.";
  } catch (cause) { handleError(cause, "Identity realm could not be registered."); }
  finally { saving.value = false; }
}

async function checkAccount(): Promise<void> {
  const profile = profiles.value.find((item) => item.instance_key === form.value.target_instance_key);
  if (!profile || !form.value.target_login.trim()) return;
  error.value = ""; notice.value = ""; saving.value = true; checked.value = undefined;
  const generation = formGeneration;
  try {
    const result = await checkIdentityAccount({ target_instance_key: profile.instance_key, profile_revision: profile.revision, target_login: form.value.target_login.trim(), ...(form.value.credential_ref.trim() ? { credential_ref: form.value.credential_ref.trim() } : {}) });
    if (!mounted || generation !== formGeneration) return;
    checked.value = { accountId: result.target_account_id, login: result.target_login, digest: result.authority_digest, profileRevision: profile.revision };
    notice.value = result.credential_checked ? "Account checked. Review the qualified selector before submitting." : "Account found. Credential evidence was not checked.";
  } catch (cause) { handleError(cause, "The target account could not be checked."); }
  finally { saving.value = false; }
}

async function submitCandidate(): Promise<void> {
  const profile = profiles.value.find((item) => item.instance_key === form.value.target_instance_key);
  if (!profile || !checked.value || checked.value.profileRevision !== profile.revision || !form.value.source_login.trim() || !form.value.realm_key) return;
  error.value = ""; notice.value = ""; saving.value = true;
  try {
    const candidate = await createIdentityCandidate({
      target_instance_key: profile.instance_key, profile_revision: profile.revision, authority_digest: checked.value.digest,
      selector: { realm_key: form.value.realm_key, login: form.value.source_login.trim(), external_id: form.value.external_id.trim(), ou_path: form.value.ou_path.trim() },
      target_account_id: checked.value.accountId, target_login: checked.value.login, email: form.value.email.trim() || null,
      prerequisites: { issue_author: form.value.issue_author, comment_author: form.value.comment_author, team_member: form.value.team_member },
    });
    if (!mounted) return;
    candidates.value = [candidate, ...candidates.value.filter((item) => item.candidate_id !== candidate.candidate_id)];
    checked.value = undefined;
    notice.value = "Candidate submitted for explicit approval.";
  } catch (cause) { handleError(cause, "The identity candidate could not be submitted."); }
  finally { saving.value = false; }
}

async function decide(candidate: IdentityCandidate, decision: "approved" | "rejected" | "pending"): Promise<void> {
  const profile = profiles.value.find((item) => item.instance_key === candidate.target.instance_key);
  if (!profile) return;
  error.value = ""; notice.value = ""; saving.value = true;
  try {
    const updated = await decideIdentityCandidate(candidate.candidate_id, { expected_revision: candidate.revision, decision, profile_revision: profile.revision });
    candidates.value = candidates.value.map((item) => item.candidate_id === updated.candidate_id ? updated : item);
    notice.value = `Candidate ${updated.status}.`;
  } catch (cause) { handleError(cause, "The candidate decision could not be saved. Reload and review the current revision."); }
  finally { saving.value = false; }
}

async function showEvents(candidate: IdentityCandidate): Promise<void> {
  try { events.value[candidate.candidate_id] = await listIdentityEvents(candidate.candidate_id); }
  catch (cause) { handleError(cause, "Candidate events could not be loaded."); }
}

function handleError(cause: unknown, fallback: string): void {
  if (cause instanceof ApiError && cause.status === 401) { emit("session-expired"); return; }
  error.value = cause instanceof ApiError && cause.status === 409 ? "This identity changed elsewhere or its checked fields are stale. Reload and check the account again." : cause instanceof ApiError && cause.message ? cause.message : fallback;
}

</script>

<template>
  <section class="identity-panel" aria-labelledby="identities-title">
    <div class="identity-heading"><div><p class="kicker">Qualified bindings</p><h2 id="identities-title">Identity administration</h2><p class="supporting">Review source realm and target account together. A binding grants no author or team rights until each implication is explicitly selected and approved.</p></div><button type="button" :disabled="loading || saving" @click="openAdministration">{{ opened ? "Reload identities" : "Manage identities" }}</button></div>
    <p v-if="error" class="alert" role="alert">{{ error }}</p><p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="loading" class="status-line" aria-live="polite">Loading identity candidates…</p>
    <template v-else-if="opened">
      <div class="identity-grid">
        <form class="identity-form" aria-labelledby="realm-form-title" @submit.prevent="registerRealm">
          <h3 id="realm-form-title">Register a source realm</h3>
          <p class="supporting">Realm registration is explicit; names and email addresses are never matched automatically.</p>
          <label for="realm-key">Realm key</label><input id="realm-key" v-model="realmForm.realm_key" :disabled="saving" required maxlength="120" autocomplete="off">
          <label for="realm-name">Display name</label><input id="realm-name" v-model="realmForm.display_name" :disabled="saving" required maxlength="120">
          <label for="realm-marker">External marker</label><input id="realm-marker" v-model="realmForm.external_marker" :disabled="saving" required maxlength="120" autocomplete="off">
          <button class="primary" type="submit" :disabled="saving">Register realm</button>
        </form>
        <form class="identity-form" aria-labelledby="candidate-form-title" @submit.prevent="submitCandidate">
          <h3 id="candidate-form-title">Submit a candidate</h3>
          <label for="identity-target">Approved target instance</label><select id="identity-target" v-model="form.target_instance_key" :disabled="saving" required @change="invalidateCheck"><option value="" disabled>Select a connection</option><option v-for="profile in profiles" :key="profile.instance_key" :value="profile.instance_key">{{ profile.display_name }}</option></select>
          <label for="identity-target-login">Target login</label><input id="identity-target-login" v-model="form.target_login" :disabled="saving" required autocomplete="off" @input="invalidateCheck">
          <label for="identity-credential">Credential reference <span>(optional)</span></label><input id="identity-credential" v-model="form.credential_ref" :disabled="saving" autocomplete="off" @input="invalidateCheck">
          <button type="button" :disabled="saving || !form.target_login.trim()" @click="checkAccount">Check target account</button>
          <p v-if="checked" class="evidence" role="status">Checked <code>{{ checked.login }}</code> ({{ checked.accountId }}). Authority: <code>{{ checked.digest }}</code></p>
          <fieldset :disabled="saving"><legend>Source selector</legend><label for="identity-realm">Registered realm</label><select id="identity-realm" v-model="form.realm_key" required><option value="" disabled>Select a realm</option><option v-for="realm in realms" :key="realm.realm_key" :value="realm.realm_key">{{ realm.display_name }} ({{ realm.realm_key }})</option></select><label for="identity-source-login">Source login</label><input id="identity-source-login" v-model="form.source_login" required autocomplete="off"><label for="identity-external-id">External ID</label><input id="identity-external-id" v-model="form.external_id" required autocomplete="off"><label for="identity-ou">OU path <span>(optional)</span></label><input id="identity-ou" v-model="form.ou_path" autocomplete="off"><label for="identity-email">Email <span>(optional, informational)</span></label><input id="identity-email" v-model="form.email" type="email" autocomplete="off"></fieldset>
          <fieldset><legend>Requested implications</legend><label class="check-label"><input v-model="form.issue_author" type="checkbox"> Issue author</label><label class="check-label"><input v-model="form.comment_author" type="checkbox"> Comment author</label><label class="check-label"><input v-model="form.team_member" type="checkbox"> Team member</label><p class="supporting">These record migration prerequisites; they do not grant rights. Identity binding alone grants none.</p></fieldset>
          <button class="primary" type="submit" :disabled="saving || !checked || checked.profileRevision !== profiles.find((item) => item.instance_key === form.target_instance_key)?.revision">Submit for approval</button>
        </form>
      </div>
          <section aria-labelledby="candidate-list-title"><h3 id="candidate-list-title">Candidates</h3><p v-if="!candidates.length" class="empty">No identity candidates yet.</p><ul v-else class="identity-list"><li v-for="candidate in candidates" :key="candidate.candidate_id" class="identity-card"><div class="identity-summary"><div><h4>{{ candidate.selector.login }} → {{ candidate.target.login }}</h4><p>{{ candidate.selector.realm_key }} · {{ candidate.target.instance_key }}</p><small>External ID {{ candidate.selector.external_id }}<span v-if="candidate.selector.ou_path"> · OU {{ candidate.selector.ou_path }}</span><span v-if="candidate.email"> · {{ candidate.email }}</span></small></div><span class="state">{{ candidate.status }}</span></div><p class="implications">Implications requested: {{ candidate.prerequisites.issue_author ? "issue author" : "no issue author" }} · {{ candidate.prerequisites.comment_author ? "comment author" : "no comment author" }} · {{ candidate.prerequisites.team_member ? "team member" : "no team member" }}</p><div class="actions"><button v-if="candidate.status === 'pending'" type="button" :disabled="saving" @click="decide(candidate, 'approved')">Approve</button><button v-if="candidate.status === 'pending'" type="button" :disabled="saving" @click="decide(candidate, 'rejected')">Reject</button><button v-if="candidate.status === 'conflict' || candidate.status === 'rejected'" type="button" :disabled="saving" @click="decide(candidate, 'pending')">Reopen</button><button type="button" :disabled="saving" @click="showEvents(candidate)">View events</button></div><ul v-if="events[candidate.candidate_id]" class="events" aria-label="Candidate events"><li v-for="(event, index) in events[candidate.candidate_id]" :key="String(event.event_id ?? event.created_at ?? index)">{{ event.decision ?? event.event_type ?? event.action ?? "Identity event" }}<span v-if="event.created_at"> · {{ event.created_at }}</span></li></ul></li></ul></section>
    </template>
  </section>
</template>

<style scoped>
.identity-panel { margin-top: 2rem; padding-top: 1.5rem; border-top: .0625rem solid #ccd4cd; }
.identity-heading { display: flex; justify-content: space-between; gap: 1rem; }
.identity-heading h2 { margin-bottom: .35rem; font-size: 1.55rem; }
.identity-heading .supporting { max-width: 50rem; }
.identity-grid { display: grid; gap: 1rem; margin-top: 1rem; }
.identity-form { display: grid; gap: .45rem; padding: 1rem; border: .0625rem solid #c6d2c8; background: #fbfcfa; }
.identity-form h3 { margin: 0 0 .25rem; }
.identity-form label { margin-top: .35rem; font-weight: 700; }
.identity-form label span, .supporting { color: #5a695f; font-weight: 400; }
.identity-form fieldset { display: grid; gap: .45rem; margin: 1rem 0 0; padding: .75rem; border: .0625rem solid #c6d2c8; }
.identity-form legend { padding: 0 .25rem; font-weight: 700; }
.check-label { display: flex; align-items: center; gap: .5rem; font-weight: 400 !important; }
.check-label input { width: auto; min-height: auto; }
.evidence { padding: .7rem .9rem; border-left: .25rem solid #c4691d; background: #f8f1e6; overflow-wrap: anywhere; }
.evidence code, .identity-card code { display: inline; color: #402d1d; }
.identity-list { display: grid; gap: .85rem; margin: 1rem 0 0; padding: 0; list-style: none; }
.identity-card { padding: 1rem; border: .0625rem solid #c6d2c8; background: #f7faf6; }
.identity-summary { display: flex; justify-content: space-between; gap: 1rem; }
.identity-summary h4 { margin: 0; font-size: 1.05rem; }
.identity-summary p, .identity-summary small { margin: .2rem 0; color: #526057; overflow-wrap: anywhere; }
.state { padding: .25rem .5rem; border: .0625rem solid #b2c5b5; border-radius: 1rem; color: #21462f; font-size: .78rem; font-weight: 700; white-space: nowrap; }
.implications { margin: .75rem 0; color: #526057; }
.events { margin: .75rem 0 0; padding-left: 1.25rem; color: #526057; font-size: .9rem; }
.empty, .status-line { margin: 1.25rem 0 0; color: #526057; }
.notice { margin: 1.25rem 0; padding: .7rem .9rem; border-left: .25rem solid #1f6540; background: #eaf3eb; color: #21462f; }
@media (min-width: 48rem) { .identity-grid { grid-template-columns: minmax(0, .8fr) minmax(0, 1.2fr); } }
@media (max-width: 40rem) { .identity-summary { flex-direction: column; } }
</style>
