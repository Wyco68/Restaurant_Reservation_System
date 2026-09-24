<script setup>
import { computed } from "vue";

/* Inline SVG (Lucide-style geometry). Never emoji-as-icon: emoji render
   differently per platform and screen readers announce them as words. */
const props = defineProps({
  name: { type: String, required: true },
  size: { type: Number, default: 18 },
  label: { type: String, default: "" },
});

const PATHS = {
  utensils: '<path d="M3 2v7a3 3 0 0 0 3 3v10"/><path d="M6 2v6"/><path d="M9 2v6"/><path d="M17 2c-1.7 1.4-2.5 4-2.5 7 0 2 .5 3 2.5 3v10"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  star: '<path d="M12 3.5 14.6 9l6 .8-4.4 4.2 1.1 6-5.3-2.9L6.7 20l1.1-6L3.4 9.8 9.4 9z"/>',
  users: '<path d="M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1"/><circle cx="9" cy="7" r="4"/>',
  check: '<path d="m4.5 12.5 5 5 10-11"/>',
  arrowLeft: '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
  arrowRight: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16.5h.01"/>',
  pin: '<path d="M20 10c0 5.5-8 12-8 12s-8-6.5-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
  calendar: '<rect x="3" y="4.5" width="18" height="17" rx="2"/><path d="M16 2.5v4"/><path d="M8 2.5v4"/><path d="M3 10h18"/>',
  pencil: '<path d="M17 3a2.8 2.8 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/>',
  logIn: '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="m10 17 5-5-5-5"/><path d="M15 12H3"/>',
};

const paths = computed(() => PATHS[props.name] || "");
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
       stroke-linejoin="round" :aria-hidden="label ? 'false' : 'true'"
       :role="label ? 'img' : undefined" :aria-label="label || undefined"
       v-html="paths" />
</template>
