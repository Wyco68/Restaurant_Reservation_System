<script setup>
import { computed, nextTick, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { register, signIn } from "../api.js";
import BaseIcon from "../components/BaseIcon.vue";
import ErrorNote from "../components/ErrorNote.vue";

const route = useRoute();
const router = useRouter();

// The mode lives in the query string, so the back button and a shared link
// both land on the right form, and the post-login redirect survives a switch.
const isRegister = computed(() => route.query.mode === "register");

const form = reactive({ full_name: "", email: "", password: "", phone: "" });
const errors = reactive({ full_name: "", email: "", password: "" });
const formError = ref("");
const busy = ref(false);

function setMode(mode) {
  if ((mode === "register") === isRegister.value) return;
  router.replace({
    query: { ...route.query, mode: mode === "register" ? "register" : undefined },
  });
}

watch(isRegister, async () => {
  formError.value = "";
  Object.keys(errors).forEach((k) => (errors[k] = ""));
  form.password = "";
  await nextTick();
  document.getElementById(isRegister.value ? "s-name" : "s-email")?.focus();
});

// Mirrors the server's UserCreate / LoginRequest rules so most mistakes are
// caught before a round trip; the server still validates everything.
function validate() {
  errors.email = /\S+@\S+\.\S+/.test(form.email) ? "" : "Enter a valid email address.";
  if (isRegister.value) {
    errors.full_name = form.full_name.trim().length >= 2 ? "" : "Enter your full name.";
    errors.password = form.password.length >= 8 ? "" : "Use at least 8 characters.";
  } else {
    errors.full_name = "";
    errors.password = form.password ? "" : "Enter your password.";
  }
  return !errors.email && !errors.password && !errors.full_name;
}

async function submit() {
  formError.value = "";
  if (!validate()) return;
  busy.value = true;
  try {
    if (isRegister.value) {
      await register({
        email: form.email.trim(),
        password: form.password,
        full_name: form.full_name.trim(),
        phone: form.phone.trim(),
      });
    } else {
      await signIn(form.email.trim(), form.password);
    }
    router.replace(route.query.redirect || "/restaurants");
  } catch (e) {
    if (isRegister.value && e.status === 409) {
      errors.email = "An account with this email already exists.";
    } else {
      formError.value = e.message;
    }
    form.password = "";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="section container">
    <div class="panel panel--narrow">
      <div class="segmented" role="group" aria-label="Account">
        <button type="button" class="segmented__option" :aria-pressed="!isRegister"
                @click="setMode('signin')">Sign in</button>
        <button type="button" class="segmented__option" :aria-pressed="isRegister"
                @click="setMode('register')">Create account</button>
      </div>

      <div class="page-head">
        <h1 style="font-size: 1.8rem">{{ isRegister ? "Create an account" : "Sign in" }}</h1>
        <p v-if="isRegister">Free, and it takes a minute. You'll be signed in straight away.</p>
        <p v-else>You need an account to reserve a table. Browsing stays open to everyone.</p>
      </div>

      <ErrorNote :message="formError" />

      <form class="form" novalidate @submit.prevent="submit">
        <div v-if="isRegister" class="field">
          <label for="s-name">Full name</label>
          <input id="s-name" v-model="form.full_name" type="text"
                 autocomplete="name" required maxlength="120"
                 :aria-invalid="errors.full_name ? 'true' : 'false'"
                 :aria-describedby="errors.full_name ? 's-name-error' : undefined">
          <span v-if="errors.full_name" id="s-name-error" class="field__error">{{ errors.full_name }}</span>
        </div>

        <div class="field">
          <label for="s-email">Email</label>
          <input id="s-email" v-model.trim="form.email" type="email"
                 autocomplete="username" required
                 :aria-invalid="errors.email ? 'true' : 'false'"
                 :aria-describedby="errors.email ? 's-email-error' : undefined">
          <span v-if="errors.email" id="s-email-error" class="field__error">
            {{ errors.email }}
            <button v-if="isRegister && errors.email.startsWith('An account')" type="button"
                    class="link-button" @click="setMode('signin')">Sign in instead</button>
          </span>
        </div>

        <div class="field">
          <label for="s-password">Password</label>
          <input id="s-password" v-model="form.password" type="password"
                 :autocomplete="isRegister ? 'new-password' : 'current-password'"
                 required maxlength="128"
                 :aria-invalid="errors.password ? 'true' : 'false'"
                 :aria-describedby="[isRegister ? 's-password-hint' : '', errors.password ? 's-password-error' : ''].join(' ').trim() || undefined">
          <span v-if="isRegister" id="s-password-hint" class="field__hint">At least 8 characters.</span>
          <span v-if="errors.password" id="s-password-error" class="field__error">{{ errors.password }}</span>
        </div>

        <div v-if="isRegister" class="field">
          <label for="s-phone">Phone <span class="field__hint">(optional)</span></label>
          <input id="s-phone" v-model="form.phone" type="tel" autocomplete="tel" maxlength="32">
        </div>

        <button type="submit" class="btn btn--primary btn--lg" :disabled="busy">
          <template v-if="busy">{{ isRegister ? "Creating account…" : "Signing in…" }}</template>
          <template v-else>
            {{ isRegister ? "Create account" : "Sign in" }}
            <BaseIcon name="arrowRight" :size="18" />
          </template>
        </button>
      </form>

      <p class="switch-note">
        <template v-if="isRegister">
          Already have an account?
          <button type="button" class="link-button" @click="setMode('signin')">Sign in</button>
        </template>
        <template v-else>
          New to TableFlow?
          <button type="button" class="link-button" @click="setMode('register')">Create an account</button>
        </template>
      </p>
    </div>
  </section>
</template>
