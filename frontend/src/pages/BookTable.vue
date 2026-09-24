<script setup>
/*
 * Step 2 — reserve.
 *
 * Progressive disclosure: date and party size first, because the table list
 * is meaningless until both are known. Tables reload whenever either
 * changes, so the options on screen always answer the query on screen.
 */
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, session, windowFor, SERVICE_MINUTES } from "../api.js";
import BaseIcon from "../components/BaseIcon.vue";
import FlowStepper from "../components/FlowStepper.vue";
import ErrorNote from "../components/ErrorNote.vue";

const route = useRoute();
const router = useRouter();

function defaultSlot() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(19, 0, 0, 0);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const restaurant = ref(null);
const tables = ref([]);
const form = reactive({
  startsAt: defaultSlot(),
  partySize: 2,
  tableId: "",
  guestName: "",
});
const errors = reactive({ guestName: "", tableId: "" });
const formError = ref("");
const tablesBusy = ref(false);
const submitting = ref(false);

const available = computed(() => tables.value.filter((t) => t.available));

async function loadRestaurant() {
  try {
    restaurant.value = await api(`/api/v1/restaurants/${route.params.id}`);
  } catch (e) {
    formError.value = e.message;
  }
}

async function loadTables() {
  if (!form.startsAt) return;
  tablesBusy.value = true;
  const { start, end } = windowFor(form.startsAt);
  try {
    tables.value = await api(
      `/api/v1/restaurants/${route.params.id}/availability` +
        `?starts_at=${encodeURIComponent(start.toISOString())}` +
        `&ends_at=${encodeURIComponent(end.toISOString())}` +
        `&party_size=${form.partySize}`
    );
    // Drop a stale selection rather than submitting a table that is no
    // longer free for the new window.
    if (!tables.value.some((t) => t.restaurant_table_id === form.tableId && t.available)) {
      form.tableId = "";
    }
  } catch {
    tables.value = [];
  } finally {
    tablesBusy.value = false;
  }
}

watch(() => [form.startsAt, form.partySize], loadTables);

onMounted(async () => {
  await loadRestaurant();
  await loadTables();
  if (session.user) form.guestName = session.user.full_name;
});

function validate() {
  errors.guestName =
    form.guestName.trim().length < 2 ? "Enter the name the table is booked under." : "";
  errors.tableId = form.tableId ? "" : "Choose an available table.";
  return !errors.guestName && !errors.tableId;
}

async function submit() {
  formError.value = "";
  if (!session.user) {
    formError.value = "Sign in before reserving a table.";
    return;
  }
  if (!validate()) return;

  submitting.value = true;
  const { start, end } = windowFor(form.startsAt);
  try {
    const r = await api("/api/v1/reservations", {
      method: "POST",
      body: JSON.stringify({
        restaurant_id: Number(route.params.id),
        restaurant_table_id: form.tableId,
        guest_name: form.guestName.trim(),
        party_size: form.partySize,
        starts_at: start.toISOString(),
        ends_at: end.toISOString(),
      }),
    });
    router.push(`/confirmation/${r.id}`);
  } catch (e) {
    // 409 is the double-booking case: the only error here the user can act
    // on, so it gets its own wording and a refreshed table list.
    formError.value =
      e.status === 409
        ? "Someone just took that table. Pick another from the refreshed list."
        : e.message;
    await loadTables();
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <section class="section container">
    <FlowStepper :current="2" />

    <div class="page-head">
      <h1>Reserve a table</h1>
      <p v-if="restaurant">
        {{ restaurant.name }} · {{ restaurant.city }} ·
        tables are held for {{ SERVICE_MINUTES }} minutes.
      </p>
    </div>

    <div class="panel panel--narrow">
      <ErrorNote :message="formError" />

      <form class="form" novalidate @submit.prevent="submit">
        <div class="form-row form-row--2">
          <div class="field">
            <label for="b-when">Date and time</label>
            <input id="b-when" v-model="form.startsAt" type="datetime-local" required>
            <span class="field__hint">{{ SERVICE_MINUTES }}-minute sitting</span>
          </div>

          <div class="field">
            <label for="b-party">Guests</label>
            <input id="b-party" v-model.number="form.partySize" type="number"
                   min="1" max="20" required>
            <span class="field__hint">1 to 20</span>
          </div>
        </div>

        <div class="field">
          <label for="b-table">Table</label>
          <select id="b-table" v-model.number="form.tableId" required
                  :aria-invalid="errors.tableId ? 'true' : 'false'"
                  :aria-describedby="errors.tableId ? 'b-table-error' : 'b-table-hint'">
            <option value="" disabled>
              {{ tablesBusy ? "Checking availability…"
                 : available.length ? "Select a table"
                 : "No tables free at that time" }}
            </option>
            <option v-for="t in tables" :key="t.restaurant_table_id"
                    :value="t.restaurant_table_id" :disabled="!t.available">
              {{ t.table_number }} — seats {{ t.capacity }}{{ t.available ? "" : " (taken)" }}
            </option>
          </select>
          <span v-if="errors.tableId" id="b-table-error" class="field__error">
            {{ errors.tableId }}
          </span>
          <span v-else id="b-table-hint" class="field__hint" aria-live="polite">
            {{ available.length }} of {{ tables.length }} tables free
          </span>
        </div>

        <div class="field">
          <label for="b-name">Name on the booking</label>
          <input id="b-name" v-model="form.guestName" type="text" required
                 autocomplete="name"
                 :aria-invalid="errors.guestName ? 'true' : 'false'"
                 :aria-describedby="errors.guestName ? 'b-name-error' : undefined">
          <span v-if="errors.guestName" id="b-name-error" class="field__error">
            {{ errors.guestName }}
          </span>
        </div>

        <div class="actions">
          <button type="submit" class="btn btn--primary btn--lg" :disabled="submitting">
            {{ submitting ? "Reserving…" : "Confirm reservation" }}
            <BaseIcon v-if="!submitting" name="arrowRight" :size="18" />
          </button>
          <router-link class="btn btn--ghost" :to="`/restaurants/${route.params.id}`">
            <BaseIcon name="arrowLeft" :size="16" /> Back to menu
          </router-link>
        </div>
      </form>
    </div>
  </section>
</template>
