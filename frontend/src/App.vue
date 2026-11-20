<script setup lang="ts">
const capabilities = [
  { name: "Blueprint validation", status: "Available in iteration 1" },
  { name: "Git bundle probe", status: "Integration evidence" },
  { name: "Full migration", status: "Not implemented" },
] as const;
</script>

<template>
  <main class="shell">
    <header class="masthead">
      <p class="eyebrow">GitFerry · Iteration 1</p>
      <h1>Controlled Gitea transport, built on evidence.</h1>
      <p class="lede">
        This development build validates configuration and reports feasibility results. It does not
        perform general migrations.
      </p>
    </header>

    <section aria-labelledby="readiness-heading" class="readiness">
      <div>
        <h2 id="readiness-heading">Current readiness</h2>
        <p>Capabilities remain unavailable until a matching automated test has run successfully.</p>
      </div>
      <dl>
        <div v-for="capability in capabilities" :key="capability.name">
          <dt>{{ capability.name }}</dt>
          <dd>{{ capability.status }}</dd>
        </div>
      </dl>
    </section>

    <p class="notice" role="status">
      The guided administrator workflow is intentionally deferred until the transfer contracts are
      proven against real Gitea instances.
    </p>
  </main>
</template>
