<script setup lang="ts">
import { computed, useId } from "vue";
import type { InventoryCoverageReport } from "./api";

const headingId = useId();
const props = defineProps<{ context: string; report?: InventoryCoverageReport | null }>();
const selectedClasses = computed(() => props.report?.repositories.reduce((total, repo) => total + repo.classes.filter((item) => item.selected).length, 0) ?? 0);
function label(value: string): string { return value.replaceAll("_", " "); }
function bytes(value: number | null): string {
  if (value === null) return "Unknown size";
  if (value < 1024) return `${value} bytes`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}
</script>

<template>
  <section class="coverage" :aria-labelledby="headingId">
    <h4 :id="headingId">Content inventory coverage — {{ context }}</h4>
    <p v-if="!report" class="supporting">No content inventory is attached. This is a bounded legacy selection, not a complete source inventory. Imported facts grant no approval.</p>
    <template v-else>
      <p class="supporting">Read-only facts from snapshot <code>{{ report.snapshot_id }}</code> at {{ report.source_instance_key }}. Imported facts grant no approval.</p>
      <dl class="coverage-summary"><div><dt>Observed selected scope</dt><dd>{{ report.summary.inventory_complete ? "Complete" : "Incomplete or blocked" }} · {{ selectedClasses }} selected class observations</dd></div><div><dt>Transfer readiness</dt><dd>{{ report.summary.transfer_ready ? "Ready" : "Blocked by limitations" }}</dd></div><div><dt>Full transfer</dt><dd>{{ report.summary.full_transfer ? "Complete selected catalog" : "Not a full transfer" }}</dd></div><div><dt>Payload sizes</dt><dd>{{ bytes(report.summary.known_payload_bytes) }} known · {{ report.summary.objects_without_size }} objects without size</dd></div></dl>
      <p v-if="report.summary.excluded_classes.length" class="supporting">{{ report.summary.excluded_classes.length }} class exclusions remain outside the selected scope.</p>
      <ul v-if="report.summary.blockers.length" class="coverage-list"><li v-for="item in report.summary.blockers" :key="`${item.repository}:${item.content_class}:${item.reason}`">{{ item.repository }} · {{ label(item.content_class) }}: {{ item.reason }}</li></ul>
      <p class="supporting">Size estimates are observations, not quotas. Temporary-space assumption: {{ report.summary.temporary_space_assumption }}</p>
      <details v-for="repo in report.repositories" :key="repo.repository" class="coverage-repository"><summary>{{ repo.repository }} · {{ bytes(repo.git_size_estimate_bytes) }} Git estimate</summary><ul class="coverage-classes"><li v-for="item in repo.classes" :key="item.content_class"><strong>{{ label(item.content_class) }}</strong><span :data-status="item.status">{{ item.selected ? "selected" : "excluded" }} · {{ item.status }} · {{ item.count === null ? "count unknown" : `${item.count} objects` }}</span><small v-if="item.reason">{{ item.reason }}</small><small v-if="item.limitations.length">Limits: {{ item.limitations.join("; ") }}</small></li></ul><p v-if="repo.size_assumptions.length" class="supporting">Size assumptions: {{ repo.size_assumptions.join("; ") }}</p></details>
    </template>
  </section>
</template>

<style scoped>
.coverage { margin-top: 1rem; padding: .85rem 1rem; border: .0625rem solid #d0d7cf; background: #fff; }
.coverage h4 { margin: 0 0 .55rem; }
.coverage-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: .55rem; }
.coverage-summary dt { color: #526057; font-size: .8rem; font-weight: 750; }
.coverage-summary dd { margin: 0; }
.coverage-list { padding-left: 1.2rem; }
.coverage-repository { margin-top: .75rem; padding: .65rem .75rem; border-top: .0625rem solid #e1e6e0; }
.coverage-repository summary { cursor: pointer; font-weight: 750; }
.coverage-classes { display: grid; gap: .45rem; margin: .65rem 0 0; padding: 0; list-style: none; }
.coverage-classes li { display: grid; gap: .1rem; padding: .45rem; background: #f7f9f6; }
.coverage-classes span { color: #315d43; font-size: .82rem; }
.coverage-classes span[data-status="blocked"], .coverage-classes span[data-status="failed"], .coverage-classes span[data-status="not-supported"] { color: #8d3b27; }
.coverage-classes small { color: #526057; }
code { display: inline; overflow-wrap: anywhere; }
</style>
