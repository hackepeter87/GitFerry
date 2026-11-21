<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";

import { ApiError, getAuthStatus, getSession, login, logout, type Principal } from "./api";
import ConnectionsPanel from "./ConnectionsPanel.vue";
import PlansPanel from "./PlansPanel.vue";
import SourceSelectionPanel from "./SourceSelectionPanel.vue";

type View = "loading" | "bootstrap" | "login" | "admin" | "unavailable";

const view = ref<View>("loading");
const principal = ref<Principal>();
const username = ref("");
const password = ref("");
const message = ref("");
const submitting = ref(false);
const usernameInput = ref<HTMLInputElement>();
const passwordInput = ref<HTMLInputElement>();

onMounted(initialize);

async function initialize(): Promise<void> {
  message.value = "";
  try {
    const status = await getAuthStatus();
    if (status.bootstrapRequired) {
      view.value = "bootstrap";
      return;
    }
    try {
      showAdmin((await getSession()).principal);
    } catch (error) {
      if (isAuthenticationRequired(error)) view.value = "login";
      else throw error;
    }
  } catch {
    view.value = "unavailable";
  }
}

async function submitLogin(): Promise<void> {
  message.value = "";
  submitting.value = true;
  let focusPassword = false;
  try {
    const session = await login(username.value, password.value);
    password.value = "";
    showAdmin(session.principal);
  } catch (error) {
    password.value = "";
    message.value = loginMessage(error);
    focusPassword = true;
  } finally {
    submitting.value = false;
  }
  if (focusPassword) {
    await nextTick();
    passwordInput.value?.focus();
  }
}

async function checkSession(): Promise<void> {
  message.value = "";
  submitting.value = true;
  let focusUsername = false;
  try {
    showAdmin((await getSession()).principal);
  } catch (error) {
    if (isAuthenticationRequired(error)) {
      principal.value = undefined;
      view.value = "login";
      message.value = "Your session expired or was revoked. Sign in again.";
      focusUsername = true;
    } else {
      message.value = "The session could not be checked. Try again.";
    }
  } finally {
    submitting.value = false;
  }
  if (focusUsername) {
    await nextTick();
    usernameInput.value?.focus();
  }
}

async function signOut(): Promise<void> {
  message.value = "";
  submitting.value = true;
  try {
    await logout();
  } catch (error) {
    if (!isAuthenticationRequired(error)) {
      message.value = "Sign out could not be confirmed. Check the service before continuing.";
      submitting.value = false;
      return;
    }
  }
  principal.value = undefined;
  username.value = "";
  password.value = "";
  view.value = "login";
  submitting.value = false;
}

function showAdmin(value: Principal): void {
  principal.value = value;
  view.value = "admin";
  message.value = "";
}

function isAuthenticationRequired(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

function loginMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "invalid_credentials") return "Invalid credentials";
  if (error instanceof ApiError && error.code === "login_rate_limited") {
    return "Too many attempts. Wait before trying again.";
  }
  return "Sign in failed. Check the service and try again.";
}

function handlePanelSessionExpired(): void {
  principal.value = undefined;
  view.value = "login";
  message.value = "Your session expired or was revoked. Sign in again.";
}
</script>

<template>
  <main class="shell">
    <header class="masthead">
      <a class="brand" href="/" aria-label="GitFerry administration home">GitFerry</a>
      <p class="eyebrow">Gitea transport administration</p>
    </header>

    <section v-if="view === 'loading'" class="panel" aria-live="polite">
      <p class="status-line">Checking local administrator state…</p>
    </section>

    <section v-else-if="view === 'bootstrap'" class="panel" aria-labelledby="bootstrap-title">
      <p class="kicker">One-time setup</p>
      <h1 id="bootstrap-title">Administrator bootstrap required</h1>
      <p class="lede">
        Create the first local administrator from the GitFerry host before this browser can sign in.
        No default account or password is provided.
      </p>
      <div class="command-note">
        <p>Use a protected local password file:</p>
        <code>gitferry admin bootstrap --database … --username … --password-file …</code>
      </div>
      <p class="supporting">Reload this page after the command completes successfully.</p>
    </section>

    <section v-else-if="view === 'login'" class="panel panel-narrow" aria-labelledby="login-title">
      <p class="kicker">Protected administration</p>
      <h1 id="login-title">Sign in to GitFerry</h1>
      <p class="lede">Use the local administrator identity created on this GitFerry installation.</p>
      <p v-if="message" class="alert" role="alert">{{ message }}</p>
      <form class="login-form" @submit.prevent="submitLogin">
        <label for="username">Username</label>
        <input id="username" ref="usernameInput" v-model="username" name="username" type="text" autocomplete="username" autocapitalize="none" required :disabled="submitting" />
        <label for="password">Password</label>
        <input id="password" ref="passwordInput" v-model="password" name="password" type="password" autocomplete="current-password" required :disabled="submitting" />
        <button class="primary" type="submit" :disabled="submitting">{{ submitting ? "Signing in…" : "Sign in" }}</button>
      </form>
    </section>

    <section v-else-if="view === 'admin'" class="panel" aria-labelledby="admin-title">
      <div class="admin-heading">
        <div>
          <p class="kicker">Authenticated workspace</p>
          <h1 id="admin-title">Administration</h1>
        </div>
        <div class="identity" aria-label="Current administrator">
          <strong>{{ principal?.username }}</strong>
          <span>{{ principal?.role === "admin" ? "Administrator" : principal?.role }}</span>
        </div>
      </div>
      <p v-if="message" class="alert" role="alert">{{ message }}</p>
      <div class="readiness" aria-labelledby="readiness-title">
        <div>
          <h2 id="readiness-title">Local access is protected</h2>
          <p>Transfer planning remains evidence-driven. Unsupported capabilities are never presented as complete migrations.</p>
        </div>
        <div class="actions">
          <button type="button" :disabled="submitting" @click="checkSession">Check session</button>
          <button type="button" :disabled="submitting" @click="signOut">Sign out</button>
        </div>
      </div>
      <ConnectionsPanel @session-expired="handlePanelSessionExpired" />
      <SourceSelectionPanel @session-expired="handlePanelSessionExpired" />
      <PlansPanel @session-expired="handlePanelSessionExpired" />
    </section>

    <section v-else class="panel panel-narrow" aria-labelledby="unavailable-title">
      <p class="kicker">Service unavailable</p>
      <h1 id="unavailable-title">Administration could not start</h1>
      <p class="lede">GitFerry could not verify its local authentication state. No access was granted.</p>
      <button type="button" @click="initialize">Try again</button>
    </section>

    <footer>Local administrator access · Session credentials never appear in the page URL.</footer>
  </main>
</template>
