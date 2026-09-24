<script setup>
import { computed } from "vue";
import { session } from "../api.js";
import BaseIcon from "./BaseIcon.vue";

const tone = computed(() => {
  if (session.health.status === "ok") return "ok";
  if (session.health.status === "checking") return "";
  return "error";
});

const healthText = computed(() =>
  session.health.status === "ok"
    ? "All systems operational"
    : session.health.status === "checking"
      ? "Checking services"
      : "Service degraded"
);
</script>

<template>
  <header class="site-header">
    <div class="container site-header__inner">
      <router-link to="/" class="brand">
        <BaseIcon name="utensils" :size="22" />
        TableFlow
      </router-link>

      <span class="site-header__spacer" />

      <span class="pill" :title="healthText">
        <span class="health-dot" :class="tone" />
        <span class="sr-only">Service status: {{ healthText }}</span>
        <span class="pill__text" aria-hidden="true">{{ healthText }}</span>
      </span>

      <nav class="site-nav" aria-label="Main">
        <router-link class="nav-link" to="/restaurants">Restaurants</router-link>
        <router-link v-if="!session.user" class="nav-link" to="/signin">
          <BaseIcon name="logIn" :size="16" /> Sign in
        </router-link>
        <router-link v-else class="nav-link" to="/account">
          {{ session.user.full_name.split(" ")[0] }}
        </router-link>
      </nav>
    </div>
  </header>
</template>
