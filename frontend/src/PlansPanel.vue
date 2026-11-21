<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ApiError, cancelPlan, getPlan, listPlans, preparePlan, queuePlan, type PlanOperation, type PlanStatus, type TransferPlan } from "./api";

const emit = defineEmits<{ (event: "session-expired"): void }>();
const props = defineProps<{ preparedPlan?: TransferPlan }>();
const plans = ref<TransferPlan[]>([]);
const selectedId = ref<string>();
const selected = ref<TransferPlan>();
const loading = ref(true);
const detailLoading = ref(false);
const saving = ref(false);
const error = ref("");
const notice = ref("");
const draftRound = ref("");
let pollTimer: ReturnType<typeof window.setTimeout> | undefined;
let generation = 0;
let disposed = false;
const MAX_POLLS = 24;

const active = computed(() => Boolean(selected.value && ["queued", "preflighting", "running"].includes(selected.value.status)));
const request = computed(() => selected.value?.document.inventory?.request ?? {});
const repositories = computed(() => {
  const structures = Array.isArray(request.value.repositories) ? request.value.repositories as Array<Record<string, unknown>> : [];
  const scopes = new Map(structures.map((item) => [String(item.repository), item]));
  for (const operation of selected.value?.operations ?? []) {
    const scope = operation.target_repository_scope;
    if (scope?.includes("/") && !scopes.has(scope)) scopes.set(scope, { repository: scope });
  }
  return [...scopes.values()];
});
const unsupported = computed(() => Array.isArray(request.value.unsupported_items) ? request.value.unsupported_items.map(String) : []);
const limitations = computed(() => selected.value?.document.inventory?.limitations ?? []);
const unresolved = computed(() => selected.value?.operations?.some((item) => ["outcome_unknown", "conflict", "failed"].includes(item.status)) ?? false);
const selectedActors = computed(() => {
  const actors = new Map<string, Record<string, unknown>>();
  for (const operation of selected.value?.operations ?? []) {
    for (const binding of Object.values(operation.bindings ?? {})) {
      if (binding && typeof binding === "object") {
        const actor = binding as Record<string, unknown>;
        actors.set(String(actor.binding_id), actor);
      }
    }
  }
  return [...actors.values()];
});

onMounted(load);
onBeforeUnmount(() => { disposed = true; ++generation; stopPolling(); });
watch(() => props.preparedPlan, (plan) => {
  if (!plan || disposed) return;
  stopPolling(); selectedId.value = plan.plan_id; selected.value = plan; replaceSummary(plan); notice.value = "A new draft is ready for review below."; schedulePoll(++generation, 0);
});

async function load(): Promise<void> {
  if (saving.value) return;
  stopPolling();
  const current = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const values = await listPlans();
    if (disposed || current !== generation) return;
    plans.value = values;
    const id = values.find((item) => item.plan_id === selectedId.value)?.plan_id ?? values[0]?.plan_id;
    if (id) await select(id);
    else { selectedId.value = undefined; selected.value = undefined; }
  } catch (cause) { if (!disposed && current === generation) handleError(cause, "Plans could not be loaded."); }
  finally { if (!disposed) loading.value = false; }
}

async function select(planId: string): Promise<void> {
  if (saving.value) return;
  stopPolling();
  const current = ++generation;
  selectedId.value = planId;
  selected.value = undefined;
  detailLoading.value = true;
  error.value = "";
  notice.value = "";
  try {
    const value = await getPlan(planId);
    if (disposed || current !== generation) return;
    selected.value = value;
    replaceSummary(value);
    schedulePoll(current, 0);
  } catch (cause) { if (!disposed && current === generation) handleError(cause, "Plan details could not be loaded."); }
  finally { if (current === generation) detailLoading.value = false; }
}

async function refresh(): Promise<void> { await load(); }

async function prepareDraft(): Promise<void> {
  const original = selected.value?.document.inventory?.request;
  const round = draftRound.value.trim();
  if (!original || !round || saving.value) return;
  stopPolling();
  const current = ++generation;
  saving.value = true;
  error.value = "";
  try {
    const value = await preparePlan({ ...original, round_key: round });
    if (disposed || current !== generation) return;
    selectedId.value = value.plan_id;
    selected.value = value;
    replaceSummary(value);
    draftRound.value = "";
    notice.value = "Draft prepared from the saved selection. Review its operations before starting it.";
    schedulePoll(current, 0);
  } catch (cause) { if (!disposed) handleError(cause, "The saved selection could not be prepared. Check local prerequisites."); }
  finally { saving.value = false; }
}

async function queue(): Promise<void> {
  const plan = selected.value;
  if (!plan || saving.value || detailLoading.value || unresolved.value || unsupported.value.length > 0 || !["draft", "blocked", "failed", "cancelled"].includes(plan.status)) return;
  stopPolling();
  const current = ++generation;
  error.value = ""; notice.value = ""; saving.value = true;
  try {
    const value = await queuePlan(plan.plan_id, plan.digest);
    if (disposed || current !== generation) return;
    selected.value = value;
    replaceSummary(value);
    notice.value = "Plan queued. The server continues working if you close this page.";
    schedulePoll(current, 0);
  } catch (cause) { if (!disposed) handleError(cause, "Queueing could not be confirmed. Refresh before trying again."); }
  finally { saving.value = false; }
}

async function cancel(): Promise<void> {
  const plan = selected.value;
  if (!plan || saving.value || plan.cancel_requested || !["draft", "queued", "preflighting", "running"].includes(plan.status)) return;
  if (!window.confirm("Request a stop before the next operation? An in-flight write may finish. Confirmed writes will not be undone.")) return;
  stopPolling();
  const current = ++generation;
  error.value = ""; notice.value = ""; saving.value = true;
  try {
    const value = await cancelPlan(plan.plan_id);
    if (disposed || current !== generation) return;
    selected.value = value;
    replaceSummary(value);
    notice.value = value.status === "cancelled" ? "Plan cancelled. Confirmed work is retained." : "Cancellation requested; the worker will stop at a safe boundary.";
    schedulePoll(current, 0);
  } catch (cause) { if (!disposed) handleError(cause, "Cancellation could not be confirmed. Refresh before trying again."); }
  finally { saving.value = false; }
}

function replaceSummary(plan: TransferPlan): void {
  const index = plans.value.findIndex((item) => item.plan_id === plan.plan_id);
  if (index >= 0) plans.value[index] = plan; else plans.value = [plan, ...plans.value];
}

function schedulePoll(current: number, count: number): void {
  stopPolling();
  if (!active.value || disposed || current !== generation) return;
  if (count >= MAX_POLLS) { notice.value = "Live refresh paused. The server continues working; refresh to check its status."; return; }
  const id = selectedId.value!;
  pollTimer = window.setTimeout(async () => {
    try {
      const value = await getPlan(id);
      if (disposed || current !== generation) return;
      selected.value = value;
      replaceSummary(value);
      schedulePoll(current, count + 1);
    } catch (cause) { if (!disposed && current === generation) handleError(cause, "Live refresh stopped. Refresh to try again."); }
  }, 2000);
}

function stopPolling(): void { if (pollTimer !== undefined) { window.clearTimeout(pollTimer); pollTimer = undefined; } }
function statusLabel(status: PlanStatus): string {
  const labels: Record<string, string> = { draft: "Draft", queued: "Queued", preflighting: "Checking prerequisites", running: "Running", confirmed: "Confirmed", blocked: "Blocked", failed: "Failed", cancelled: "Cancelled" };
  return labels[status] ?? "Unknown";
}
function operationStatus(status: string): string {
  return ({ outcome_unknown: "Reconciliation required — may already be written", blocked_parent: "Waiting for parent — not sent", prepared: "Prepared — not sent", executing: "Executing", confirmed: "Confirmed", conflict: "Conflict — review required", failed: "Failed" } as Record<string, string>)[status] ?? status;
}
function dependencies(operation: PlanOperation): string[] {
  const graph = selected.value?.document.dependencies as Record<string, string[]> | undefined;
  return graph?.[operation.operation_id] ?? [];
}
function operationContent(operation: PlanOperation): string {
  const intent = operation.intent ?? {};
  if (typeof intent.body === "string") return intent.body;
  if (intent.values && typeof intent.values === "object") {
    const values = intent.values as Record<string, unknown>;
    return `${String(values.title ?? "")}\n\n${String(values.body ?? "")}\n\nState: ${String(values.state ?? "")}\nLabels: ${Array.isArray(values.labels) ? values.labels.join(", ") : ""}`;
  }
  if (typeof intent.permission === "string") return `Permission: ${intent.permission}`;
  return "";
}
function operationLabel(operation: PlanOperation): string { return operation.operation_type.replaceAll("_", " "); }
function handleError(cause: unknown, fallback: string): void {
  if (cause instanceof ApiError && cause.status === 401) { stopPolling(); emit("session-expired"); return; }
  error.value = cause instanceof ApiError && cause.message ? cause.message : fallback;
}
</script>

<template>
  <section class="plans-panel" aria-labelledby="plans-title">
    <div class="plans-heading">
      <div><p class="kicker">Durable execution review</p><h2 id="plans-title">Transfer plans</h2><p class="supporting">Review the exact local selection and evidence captured for a plan before queueing it. A plan is not a completed migration.</p></div>
      <button type="button" :disabled="loading || detailLoading || saving" @click="refresh">Refresh plans</button>
    </div>
    <p v-if="error" class="alert" role="alert">{{ error }}</p><p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="loading" class="status-line" aria-live="polite">Loading plans…</p>
    <p v-else-if="!plans.length" class="empty">No transfer plans are available. Plans are created from an explicit local selection.</p>
    <div v-else class="plans-layout">
      <nav aria-label="Transfer plans" class="plan-list">
        <button v-for="plan in plans" :key="plan.plan_id" type="button" class="plan-row" :disabled="saving" :class="{ selected: plan.plan_id === selectedId }" :aria-current="plan.plan_id === selectedId ? 'true' : undefined" @click="select(plan.plan_id)">
          <span><strong>{{ plan.round_key }}</strong><small>{{ plan.target_instance_key }} · {{ plan.operations?.length ?? "—" }} operations</small></span><span class="plan-status" :data-status="plan.status">{{ statusLabel(plan.status) }}</span>
        </button>
      </nav>
      <article v-if="selected" class="plan-detail" aria-labelledby="plan-detail-title">
        <div class="plan-detail-heading"><div><p class="kicker">Selected plan</p><h3 id="plan-detail-title">{{ selected.round_key }}</h3><p class="supporting">Status: <strong>{{ statusLabel(selected.status) }}</strong><span v-if="selected.reason"> · {{ selected.reason }}</span></p></div><div class="actions"><button v-if="['draft','blocked','failed','cancelled'].includes(selected.status)" class="primary" type="button" :disabled="saving || unresolved || detailLoading || unsupported.length > 0" @click="queue">{{ saving ? "Queueing…" : "Start reviewed plan" }}</button><button v-if="['draft','queued','preflighting','running'].includes(selected.status)" type="button" :disabled="saving || selected.cancel_requested" @click="cancel">Cancel plan</button></div></div>
        <p v-if="detailLoading" class="status-line" aria-live="polite">Refreshing plan details…</p>
        <details class="evidence-box"><summary>Connection and plan verification details</summary><dl><div><dt>Target</dt><dd><code>{{ selected.target_instance_key }}</code></dd></div><div><dt>Profile revision</dt><dd>{{ selected.profile_revision }}</dd></div><div><dt>Approved identity evidence</dt><dd><code>{{ selected.evidence_digest }}</code></dd></div><div><dt>Current plan digest</dt><dd><code>{{ selected.digest }}</code></dd></div></dl><p class="supporting">These local references bind review to the target connection and immutable plan contents. They do not contain credentials.</p></details>
        <p v-if="unresolved" class="alert" role="status">An operation needs local reconciliation or conflict resolution before this plan can continue. Requeueing does not resolve it.</p>
        <section v-if="unsupported.length" aria-labelledby="unsupported-title"><h4 id="unsupported-title">Selected contents are not supported</h4><p class="alert" role="status">Revise this selection before starting a transfer.</p><ul><li v-for="item in unsupported" :key="item">{{ item }}</li></ul></section>
        <section aria-labelledby="scope-title"><h4 id="scope-title">Selected repository scope</h4><p v-if="!repositories.length" class="supporting">No repositories are included in this plan.</p><ul v-else class="scope-list"><li v-for="(repository, index) in repositories" :key="`${String(repository.repository ?? index)}-${index}`"><strong>{{ String(repository.repository ?? "Unnamed repository") }}</strong><span v-if="repository.organization">Organization: {{ String(repository.organization) }}</span><span v-if="repository.bundle">Git bundle selected for this repository.</span></li></ul><p v-if="selected.document.inventory?.inventory_complete === false" class="supporting limitation-note">This is an explicit selection, not a complete source inventory.</p></section>
        <section v-if="selectedActors.length" aria-labelledby="actors-title"><h4 id="actors-title">Locally confirmed identities</h4><ul><li v-for="actor in selectedActors" :key="String(actor.binding_id)">{{ actor.source_realm_key }} / {{ actor.source_login }} → {{ actor.target_login }} (target account {{ actor.target_account_id }})</li></ul></section>
        <section aria-labelledby="operations-title"><h4 id="operations-title">Operations and dependency evidence</h4><p v-if="!selected.operations?.length" class="supporting">No operation details were returned.</p><ol v-else class="operation-list"><li v-for="operation in selected.operations" :key="operation.operation_id"><div><strong>{{ operationLabel(operation) }}</strong><span v-if="operation.target_repository_scope"> · {{ operation.target_repository_scope }}</span></div><span class="operation-status" :data-status="operation.status">{{ operationStatus(operation.status) }}</span><details v-if="operationContent(operation)"><summary>Content to apply</summary><pre class="operation-content">{{ operationContent(operation) }}</pre></details><details v-if="operation.operation_id || operation.target_global_id"><summary>Operation details</summary><dl><div><dt>Operation ID</dt><dd><code>{{ operation.operation_id }}</code></dd></div><div v-if="dependencies(operation).length"><dt>Depends on</dt><dd>{{ dependencies(operation).join(", ") }}</dd></div><div v-if="operation.target_global_id"><dt>Target ID</dt><dd><code>{{ operation.target_global_id }}</code></dd></div><div v-if="operation.error_reason"><dt>Reason</dt><dd>{{ operation.error_reason }}</dd></div></dl></details></li></ol></section>
        <form v-if="selected.document.inventory?.request" class="selection-copy" @submit.prevent="prepareDraft">
          <h4>Prepare from this saved selection</h4>
          <p class="supporting">Reuse the selected contents and local bindings. Original comparison facts and journal protections remain in effect; this does not capture a fresh source inventory.</p>
          <label for="plan-round">New plan round</label>
          <input id="plan-round" v-model="draftRound" required maxlength="256" :disabled="saving" />
          <button type="submit" :disabled="saving || !draftRound.trim()">Prepare draft</button>
        </form>
        <section v-if="limitations.length" aria-labelledby="limitations-title"><h4 id="limitations-title">Current limitations</h4><ul class="limitation-list"><li v-for="limitation in limitations" :key="limitation">{{ limitation }}</li></ul><p class="supporting">Unsupported items remain visible as limitations; this screen never calls them migrated.</p></section>
      </article>
    </div>
  </section>
</template>

<style scoped>
.selection-copy { margin-top: 1.25rem; }
.operation-status { display: inline-block; margin-inline-start: .5rem; }
.operation-content { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
.plans-panel { margin-top: 2rem; padding-top: 1.5rem; border-top: .0625rem solid #ccd4cd; }
.plans-heading, .plan-detail-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
.plans-heading h2 { margin-bottom: .35rem; font-size: 1.55rem; }
.plans-heading .supporting { max-width: 48rem; margin-bottom: 0; }
.plans-layout { display: grid; grid-template-columns: minmax(13rem, 18rem) minmax(0, 1fr); gap: 1rem; margin-top: 1.25rem; }
.plan-list { display: grid; align-content: start; gap: .5rem; }
.plan-row { display: flex; align-items: flex-start; justify-content: space-between; gap: .75rem; min-height: 0; padding: .75rem; text-align: left; }
.plan-row span:first-child { min-width: 0; }.plan-row strong, .plan-row small { display: block; overflow-wrap: anywhere; }.plan-row small { margin-top: .2rem; color: #5a695f; font-size: .8rem; }.plan-row.selected { border-color: #1f6540; background: #eaf3eb; }
.plan-status, .operation-status { color: #315d43; font-size: .76rem; font-weight: 750; text-transform: capitalize; white-space: nowrap; }.plan-status[data-status="blocked"], .plan-status[data-status="failed"], .operation-status[data-status="blocked_parent"] { color: #8d3b27; }.plan-status[data-status="confirmed"] { color: #1f6540; }
.plan-detail { min-width: 0; padding: 1rem; border: .0625rem solid #c6d2c8; background: #fbfcfa; }.plan-detail h3 { margin: 0 0 .35rem; font-size: 1.4rem; }.plan-detail section { margin-top: 1.35rem; padding-top: 1rem; border-top: .0625rem solid #d7dfd8; }.plan-detail h4 { margin: 0 0 .55rem; }.evidence-box { margin-top: 1rem; padding: .85rem; border-left: .25rem solid #c4691d; background: #f8f1e6; }.evidence-box h4 { margin: 0 0 .5rem; }dl { display: grid; gap: .4rem; margin: 0; }dl div { display: grid; grid-template-columns: minmax(8rem, auto) minmax(0, 1fr); gap: .75rem; }dt { color: #526057; font-weight: 700; }dd { margin: 0; overflow-wrap: anywhere; }dd code { display: inline; }.scope-list, .limitation-list { margin: 0; padding-left: 1.2rem; }.scope-list li, .limitation-list li { margin: .35rem 0; }.scope-list span { display: block; color: #526057; font-size: .88rem; }.operation-list { display: grid; gap: .55rem; margin: 0; padding-left: 1.35rem; }.operation-list li { padding: .65rem .75rem; border: .0625rem solid #d0d7cf; background: #fff; }.operation-list > li > div { display: inline; }.operation-list details { margin-top: .5rem; }.operation-list summary { color: #526057; cursor: pointer; font-size: .84rem; }.limitation-note { margin-top: .7rem; }.empty, .status-line { margin: 1.25rem 0 0; color: #526057; }
@media (max-width: 48rem) { .plans-heading, .plan-detail-heading { flex-direction: column; }.plans-layout { grid-template-columns: 1fr; } }
</style>
