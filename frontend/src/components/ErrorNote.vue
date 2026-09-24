<script setup>
import BaseIcon from "./BaseIcon.vue";
defineProps({ message: String });
</script>

<template>
  <p v-if="message" class="alert alert--error" role="alert">
    <BaseIcon name="alert" :size="18" />
    <span>{{ message }}</span>
  </p>
</template>
