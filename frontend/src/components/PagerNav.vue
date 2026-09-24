<script setup>
import BaseIcon from "./BaseIcon.vue";

defineProps({ page: Number, pages: Number, total: Number, busy: Boolean });
defineEmits(["go"]);
</script>

<template>
  <div v-if="pages > 1" class="pager">
    <button type="button" class="btn btn--ghost" :disabled="page <= 1 || busy"
            @click="$emit('go', page - 1)">
      <BaseIcon name="arrowLeft" :size="16" /> Prev
    </button>
    <span aria-live="polite">Page {{ page }} of {{ pages }} · {{ total }} total</span>
    <button type="button" class="btn btn--ghost" :disabled="page >= pages || busy"
            @click="$emit('go', page + 1)">
      Next <BaseIcon name="arrowRight" :size="16" />
    </button>
  </div>
</template>
