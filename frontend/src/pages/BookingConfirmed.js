/* Step 3, confirmed. The reservation is loaded by id, so the page still
   works after a reload or from a shared link. */
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api.js";
import BaseIcon from "../components/BaseIcon.js";
import FlowStepper from "../components/FlowStepper.js";
import ErrorNote from "../components/ErrorNote.js";

const when = (iso) =>
  new Date(iso).toLocaleString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });

// The reservation only has the table's id. The availability list for the same
// time slot has each table's number (e.g. "T3"), so look it up there.
async function tableNumber(r) {
  try {
    const tables = await api(
      `/api/v1/restaurants/${r.restaurant_id}/availability` +
        `?starts_at=${encodeURIComponent(r.starts_at)}` +
        `&ends_at=${encodeURIComponent(r.ends_at)}` +
        `&party_size=${r.party_size}`
    );
    const t = tables.find((x) => x.restaurant_table_id === r.restaurant_table_id);
    return t ? t.table_number : "";
  } catch {
    return "";
  }
}

export default {
  name: "BookingConfirmed",
  components: { BaseIcon, FlowStepper, ErrorNote },
  setup() {
    const route = useRoute();
    const reservation = ref(null);
    const table = ref("");
    const loading = ref(true);
    const error = ref("");

    onMounted(async () => {
      try {
        reservation.value = await api(`/api/v1/reservations/${route.params.id}`);
        table.value = await tableNumber(reservation.value);
      } catch (e) {
        error.value = e.message;
      } finally {
        loading.value = false;
      }
    });

    return { reservation, table, loading, error, when };
  },
  template: `
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
          <div class="summary__row"><dt>Table</dt><dd>{{ table || "#" + reservation.restaurant_table_id }}</dd></div>
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
  `,
};
