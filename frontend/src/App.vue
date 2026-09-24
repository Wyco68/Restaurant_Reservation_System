<script setup>
import AppHeader from "./components/AppHeader.vue";
import AppFooter from "./components/AppFooter.vue";
</script>

<template>
  <AppHeader />
  <main id="main" tabindex="-1">
    <router-view />
  </main>
  <AppFooter />
</template>
