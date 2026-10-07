import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { api, session, signOut } from "../api.js";
import BaseIcon from "../components/BaseIcon.js";
import ErrorNote from "../components/ErrorNote.js";

const when = (iso) =>
  new Date(iso).toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });

export default {
  name: "AccountPage",
  components: { BaseIcon, ErrorNote },
  setup() {
    const router = useRouter();
    const bookings = ref([]);
    const names = reactive({});        // restaurant_id -> name
    const state = ref("loading");      // loading | ready | unavailable | error
    const error = ref("");
    const cancelling = ref(null);

    const isUpcoming = (b) => b.status !== "cancelled" && new Date(b.ends_at) >= new Date();
    const groups = computed(() => [
      { id: "upcoming", title: "Upcoming", items: bookings.value.filter(isUpcoming).reverse() },
      { id: "past", title: "Past & cancelled", items: bookings.value.filter((b) => !isUpcoming(b)) },
    ].filter((g) => g.items.length));

    // A booking carries only restaurant_id; each name is fetched once.
    async function loadNames(list) {
      const missing = [...new Set(list.map((b) => b.restaurant_id))].filter((id) => !(id in names));
      await Promise.all(missing.map(async (id) => {
        try {
          names[id] = (await api(`/api/v1/restaurants/${id}`)).name;
        } catch {
          names[id] = `Restaurant #${id}`;
        }
      }));
    }

    async function load() {
      state.value = "loading";
      try {
        // Latest first, cancelled included - see GET /reservations.
        bookings.value = await api("/api/v1/reservations");
        await loadNames(bookings.value);
        state.value = "ready";
      } catch (e) {
        // 404/405: the API running here has no list endpoint yet.
        state.value = e.status === 404 || e.status === 405 ? "unavailable" : "error";
        error.value = e.message;
      }
    }

    async function cancel(b) {
      cancelling.value = b.id;
      error.value = "";
      try {
        const updated = await api(`/api/v1/reservations/${b.id}`, { method: "DELETE" });
        bookings.value = bookings.value.map((x) => (x.id === b.id ? updated : x));
      } catch (e) {
        error.value = e.message;
      } finally {
        cancelling.value = null;
      }
    }

    function leave() {
      signOut();
      router.push("/");
    }

    onMounted(() => {
      if (session.user) load();
    });

    return {
      session, bookings, names, state, error, groups, cancelling,
      isUpcoming, cancel, leave, when,
    };
  },
  template: `
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

      <div v-if="session.user" class="bookings panel--narrow">
        <h2>My bookings</h2>
        <ErrorNote :message="state === 'ready' ? error : ''" />

        <div v-if="state === 'loading'" class="bookings__list" aria-busy="true">
          <div v-for="n in 2" :key="n" class="skeleton" style="min-height: 84px" />
        </div>

        <p v-else-if="state === 'unavailable'" class="empty">
          Booking history appears here once the API lists bookings.
        </p>

        <ErrorNote v-else-if="state === 'error'" :message="error" />

        <p v-else-if="!bookings.length" class="empty">
          No bookings yet. Find a restaurant and reserve a table.
        </p>

        <template v-else>
        <section v-for="group in groups" :key="group.id" :aria-labelledby="group.id">
          <h3 :id="group.id" class="bookings__group">{{ group.title }}</h3>
          <ul class="bookings__list">
            <li v-for="b in group.items" :key="b.id" class="booking"
                :class="{ 'booking--past': !isUpcoming(b) }">
              <router-link :to="'/confirmation/' + b.id" class="booking__link">
                <span class="booking__date"><BaseIcon name="calendar" :size="15" /> {{ when(b.starts_at) }}</span>
                <span class="booking__place">{{ names[b.restaurant_id] || 'Restaurant #' + b.restaurant_id }}</span>
                <span class="booking__meta">
                  <BaseIcon name="users" :size="14" /> {{ b.party_size }} · {{ b.guest_name }}
                </span>
              </router-link>
              <span class="pill booking__status" :class="'booking__status--' + b.status">{{ b.status }}</span>
              <button v-if="isUpcoming(b)" type="button" class="btn btn--ghost booking__cancel"
                      :disabled="cancelling === b.id" @click="cancel(b)">
                {{ cancelling === b.id ? "Cancelling…" : "Cancel" }}
              </button>
            </li>
          </ul>
        </section>
        </template>
      </div>
    </section>
  `,
};
