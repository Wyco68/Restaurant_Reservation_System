<script setup>
import BaseIcon from "./BaseIcon.vue";

/* Funnel progress. The rule for a multi-step flow is simple: always say
   which step this is and how many remain. */
defineProps({ current: { type: Number, required: true } });
const steps = ["Choose a restaurant", "Reserve", "Confirmed"];
</script>

<template>
  <nav class="stepper" aria-label="Booking progress">
    <template v-for="(label, i) in steps" :key="label">
      <span class="stepper__item"
            :class="{ 'is-current': i + 1 === current, 'is-done': i + 1 < current }"
            :aria-current="i + 1 === current ? 'step' : undefined">
        <span class="stepper__num">
          <BaseIcon v-if="i + 1 < current" name="check" :size="13" />
          <template v-else>{{ i + 1 }}</template>
        </span>
        {{ label }}
      </span>
      <span v-if="i < steps.length - 1" class="stepper__sep" aria-hidden="true">/</span>
    </template>
    <span class="sr-only">Step {{ current }} of {{ steps.length }}</span>
  </nav>
</template>
