<script setup>
import { useRouter } from "vue-router";
import { session, signOut } from "../api.js";
import BaseIcon from "../components/BaseIcon.vue";

const router = useRouter();

function leave() {
  signOut();
  router.push("/");
}
</script>

<template>
  <section class="section container">
    <div class="panel panel--narrow">
      <div class="page-head"><h1 style="font-size: 1.8rem">Account</h1></div>

      <template v-if="session.user">
        <dl class="summary">
          <div class="summary__row"><dt>Name</dt><dd>{{ session.user.full_name }}</dd></div>
          <div class="summary__row"><dt>Email</dt><dd>{{ session.user.email }}</dd></div>
          <div class="summary__row">
            <dt>Role</dt><dd style="text-transform: capitalize">{{ session.user.role }}</dd>
          </div>
        </dl>

        <div class="actions" style="margin-top: var(--space-5)">
          <router-link class="btn btn--primary" to="/restaurants">Browse restaurants</router-link>
          <button type="button" class="btn btn--ghost" @click="leave">Sign out</button>
        </div>
      </template>

      <template v-else>
        <p class="empty">You are not signed in.</p>
        <router-link class="btn btn--primary" to="/signin">
          <BaseIcon name="logIn" :size="17" /> Sign in
        </router-link>
      </template>
    </div>
  </section>
</template>
