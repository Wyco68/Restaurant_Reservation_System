<script setup>
import { reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { signIn } from "../api.js";
import BaseIcon from "../components/BaseIcon.vue";
import ErrorNote from "../components/ErrorNote.vue";

const route = useRoute();
const router = useRouter();

const form = reactive({ email: "", password: "" });
const errors = reactive({ email: "", password: "" });
const formError = ref("");
const busy = ref(false);

function validate() {
  errors.email = /\S+@\S+\.\S+/.test(form.email) ? "" : "Enter a valid email address.";
  errors.password = form.password ? "" : "Enter your password.";
  return !errors.email && !errors.password;
}

async function submit() {
  formError.value = "";
  if (!validate()) return;
  busy.value = true;
  try {
    await signIn(form.email.trim(), form.password);
    router.replace(route.query.redirect || "/restaurants");
  } catch (e) {
    formError.value = e.message;
    form.password = "";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="section container">
    <div class="panel panel--narrow">
      <div class="page-head">
        <h1 style="font-size: 1.8rem">Sign in</h1>
        <p>You need an account to reserve a table. Browsing stays open to everyone.</p>
      </div>

      <ErrorNote :message="formError" />

      <form class="form" novalidate @submit.prevent="submit">
        <div class="field">
          <label for="s-email">Email</label>
          <input id="s-email" v-model.trim="form.email" type="email"
                 autocomplete="username" required
                 :aria-invalid="errors.email ? 'true' : 'false'"
                 :aria-describedby="errors.email ? 's-email-error' : undefined">
          <span v-if="errors.email" id="s-email-error" class="field__error">{{ errors.email }}</span>
        </div>

        <div class="field">
          <label for="s-password">Password</label>
          <input id="s-password" v-model="form.password" type="password"
                 autocomplete="current-password" required
                 :aria-invalid="errors.password ? 'true' : 'false'"
                 :aria-describedby="errors.password ? 's-password-error' : undefined">
          <span v-if="errors.password" id="s-password-error" class="field__error">{{ errors.password }}</span>
        </div>

        <button type="submit" class="btn btn--primary btn--lg" :disabled="busy">
          {{ busy ? "Signing in…" : "Sign in" }}
          <BaseIcon v-if="!busy" name="arrowRight" :size="18" />
        </button>
      </form>
    </div>
  </section>
</template>
