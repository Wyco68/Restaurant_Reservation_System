<script setup>
/* Step 3 — confirmed. Deep-linkable: the reservation is re-fetched by id,
   so the URL can be reloaded or shared and still render. */
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api.js";
import BaseIcon from "../components/BaseIcon.vue";
import FlowStepper from "../components/FlowStepper.vue";
import ErrorNote from "../components/ErrorNote.vue";

const route = useRoute();
const reservation = ref(null);
const loading = ref(true);
const error = ref("");

onMounted(async () => {
  try {
    reservation.value = await api(`/api/v1/reservations/${route.params.id}`);
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
});

const when = (iso) =>
  new Date(iso).toLocaleString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });
</script>

<template>
  <section class="section container">
    <FlowStepper :current="3" />
    <ErrorNote :message="error" />

    <div v-if="loading" class="skeleton panel--narrow" style="height: 220px" />

    <div v-else-if="reservation" class="panel panel--narrow">
      <p class="alert alert--ok" style="margin-bottom: var(--space-4)">
        <BaseIcon name="check" :size="18" />
        <span>Your table is booked.</span>
      </p>

      <h1 style="font-size: 1.6rem">Reservation #{{ reservation.id }}</h1>

      <dl class="summary" style="margin-top: var(--space-4)">
        <div class="summary__row"><dt>Guest</dt><dd>{{ reservation.guest_name }}</dd></div>
        <div class="summary__row"><dt>When</dt><dd>{{ when(reservation.starts_at) }}</dd></div>
        <div class="summary__row"><dt>Guests</dt><dd>{{ reservation.party_size }}</dd></div>
        <div class="summary__row"><dt>Table</dt><dd>#{{ reservation.restaurant_table_id }}</dd></div>
        <div class="summary__row">
          <dt>Status</dt><dd style="text-transform: capitalize">{{ reservation.status }}</dd>
        </div>
      </dl>

      <div class="actions" style="margin-top: var(--space-5)">
        <router-link class="btn btn--primary" to="/account">My account</router-link>
        <router-link class="btn btn--ghost" to="/restaurants">Book another</router-link>
      </div>
    </div>

    <p v-else class="empty">Reservation not found.</p>
  </section>
</template>
