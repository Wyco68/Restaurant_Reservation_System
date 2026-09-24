<script setup>
import { computed, nextTick, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { api, session, signOut, updateProfile } from "../api.js";
import BaseIcon from "../components/BaseIcon.vue";
import ErrorNote from "../components/ErrorNote.vue";

const router = useRouter();

const bookings = ref([]);
// "loading" | "ready" | "unavailable" | "error"
const state = ref("loading");
const error = ref("");
const restaurants = reactive({});

const initials = computed(() =>
  (session.user?.full_name || "")
    .split(/\s+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0].toUpperCase()).join("")
);

const memberSince = computed(() =>
  session.user?.created_at
    ? new Date(session.user.created_at).toLocaleDateString(undefined, { month: "long", year: "numeric" })
    : ""
);

const now = Date.now();
const upcoming = computed(() =>
  bookings.value
    .filter((b) => b.status !== "cancelled" && new Date(b.ends_at).getTime() >= now)
    .sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at))
);
const past = computed(() =>
  bookings.value
    .filter((b) => !upcoming.value.includes(b))
    .sort((a, b) => new Date(b.starts_at) - new Date(a.starts_at))
);

const groups = computed(() =>
  [
    { id: "upcoming", title: "Upcoming", items: upcoming.value },
    { id: "past", title: "Past & cancelled", items: past.value },
  ].filter((g) => g.items.length)
);

const when = (iso) =>
  new Date(iso).toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });

// Bookings carry only restaurant_id; names are fetched once per restaurant.
async function loadRestaurantNames(list) {
  const ids = [...new Set(list.map((b) => b.restaurant_id))].filter((id) => !(id in restaurants));
  await Promise.all(ids.map(async (id) => {
    try {
      restaurants[id] = (await api(`/api/v1/restaurants/${id}`)).name;
    } catch {
      restaurants[id] = `Restaurant #${id}`;
    }
  }));
}

// GET /api/v1/reservations (the signed-in user's bookings) is not in the API
// yet - it belongs in app/routers/reservations.py. Until it lands the server
// answers 405, shown as "coming soon". Either a plain array or the standard
// pagination envelope lights this list up with no frontend change.
async function loadBookings() {
  state.value = "loading";
  try {
    const data = await api("/api/v1/reservations");
    bookings.value = Array.isArray(data) ? data : data.items || [];
    await loadRestaurantNames(bookings.value);
    state.value = "ready";
  } catch (e) {
    if (e.status === 405 || e.status === 404) {
      state.value = "unavailable";
    } else {
      error.value = e.message;
      state.value = "error";
    }
  }
}

// ---- profile edit
const editing = ref(false);
const saving = ref(false);
const saved = ref(false);
const saveError = ref("");
const draft = reactive({ full_name: "", email: "", phone: "" });
const draftErrors = reactive({ full_name: "", email: "" });

async function startEdit() {
  const u = session.user;
  Object.assign(draft, { full_name: u.full_name, email: u.email, phone: u.phone || "" });
  Object.assign(draftErrors, { full_name: "", email: "" });
  saveError.value = "";
  saved.value = false;
  editing.value = true;
  await nextTick();
  document.getElementById("p-name")?.focus();
}

// Same rules as registration and the server's UserUpdate schema.
function validateDraft() {
  draftErrors.full_name = draft.full_name.trim().length >= 2 ? "" : "Enter your full name.";
  draftErrors.email = /\S+@\S+\.\S+/.test(draft.email) ? "" : "Enter a valid email address.";
  return !draftErrors.full_name && !draftErrors.email;
}

async function saveProfile() {
  saveError.value = "";
  if (!validateDraft()) return;
  const u = session.user;
  const changes = {};
  if (draft.full_name.trim() !== u.full_name) changes.full_name = draft.full_name.trim();
  if (draft.email.trim() !== u.email) changes.email = draft.email.trim();
  const phone = draft.phone.trim() || null;
  if (phone !== (u.phone || null)) changes.phone = phone;

  if (!Object.keys(changes).length) {
    editing.value = false;
    return;
  }
  saving.value = true;
  try {
    await updateProfile(changes);
    editing.value = false;
    saved.value = true;
  } catch (e) {
    if (e.status === 409) draftErrors.email = "Another account already uses this email.";
    // PATCH /users/me ships separately (app/routers/users.py); until then
    // the API answers 405.
    else if (e.status === 405) saveError.value = "Profile editing is not available yet.";
    else saveError.value = e.message;
  } finally {
    saving.value = false;
  }
}

function leave() {
  signOut();
  router.push("/");
}

onMounted(() => {
  if (session.user) loadBookings();
});
</script>

<template>
  <section class="section container">
    <template v-if="session.user">
      <div class="page-head">
        <h1>My account</h1>
      </div>

      <div class="account">
        <aside class="panel profile" aria-labelledby="profile-name">
          <div class="profile__avatar" aria-hidden="true">{{ initials }}</div>
          <h2 id="profile-name" class="profile__name">{{ session.user.full_name }}</h2>
          <span class="pill profile__role">{{ session.user.role }}</span>

          <form v-if="editing" class="form profile__form" novalidate @submit.prevent="saveProfile">
            <ErrorNote :message="saveError" />
            <div class="field">
              <label for="p-name">Full name</label>
              <input id="p-name" v-model="draft.full_name" type="text" autocomplete="name"
                     required maxlength="120"
                     :aria-invalid="draftErrors.full_name ? 'true' : 'false'"
                     :aria-describedby="draftErrors.full_name ? 'p-name-error' : undefined">
              <span v-if="draftErrors.full_name" id="p-name-error" class="field__error">{{ draftErrors.full_name }}</span>
            </div>
            <div class="field">
              <label for="p-email">Email</label>
              <input id="p-email" v-model="draft.email" type="email" autocomplete="email" required
                     :aria-invalid="draftErrors.email ? 'true' : 'false'"
                     :aria-describedby="draftErrors.email ? 'p-email-error' : 'p-email-hint'">
              <span v-if="draftErrors.email" id="p-email-error" class="field__error">{{ draftErrors.email }}</span>
              <span v-else id="p-email-hint" class="field__hint">You'll sign in with this address.</span>
            </div>
            <div class="field">
              <label for="p-phone">Phone <span class="field__hint">(optional)</span></label>
              <input id="p-phone" v-model="draft.phone" type="tel" autocomplete="tel" maxlength="32">
            </div>
            <div class="profile__form-actions">
              <button type="submit" class="btn btn--primary" :disabled="saving">
                {{ saving ? "Saving…" : "Save changes" }}
              </button>
              <button type="button" class="btn btn--ghost" :disabled="saving" @click="editing = false">
                Cancel
              </button>
            </div>
          </form>

          <template v-else>
            <p v-if="saved" class="alert alert--ok profile__saved" role="status">
              <BaseIcon name="check" :size="18" /> <span>Profile updated.</span>
            </p>

            <dl class="summary profile__details">
              <div class="summary__row"><dt>Email</dt><dd>{{ session.user.email }}</dd></div>
              <div class="summary__row"><dt>Phone</dt><dd>{{ session.user.phone || "—" }}</dd></div>
              <div v-if="memberSince" class="summary__row"><dt>Member since</dt><dd>{{ memberSince }}</dd></div>
            </dl>

            <button type="button" class="btn btn--ghost profile__signout" @click="startEdit">
              <BaseIcon name="pencil" :size="16" /> Edit profile
            </button>
            <button type="button" class="link-button profile__leave" @click="leave">Sign out</button>
          </template>
        </aside>

        <div class="bookings">
          <div class="bookings__head">
            <h2>My bookings</h2>
            <router-link class="btn btn--primary" to="/restaurants">
              <BaseIcon name="search" :size="17" /> Book a table
            </router-link>
          </div>

          <div v-if="state === 'loading'" class="bookings__list" aria-busy="true">
            <div v-for="n in 3" :key="n" class="skeleton" style="min-height: 84px" />
          </div>

          <div v-else-if="state === 'unavailable'" class="notice">
            <BaseIcon name="calendar" :size="22" />
            <div>
              <p class="notice__title">Booking history is coming soon</p>
              <p>
                Your upcoming and past reservations will be listed here once
                the bookings service supports listing them.
              </p>
            </div>
          </div>

          <ErrorNote v-else-if="state === 'error'" :message="error" />

          <p v-else-if="!bookings.length" class="empty">
            No bookings yet. Find a restaurant and reserve a table.
          </p>

          <template v-else>
            <section v-for="group in groups" :key="group.id" :aria-labelledby="`${group.id}-title`">
              <h3 :id="`${group.id}-title`" class="bookings__group">{{ group.title }}</h3>
              <ul class="bookings__list">
                <li v-for="b in group.items" :key="b.id">
                  <router-link :to="`/confirmation/${b.id}`" class="booking"
                               :class="{ 'booking--past': group.id === 'past' }">
                    <span class="booking__date">
                      <BaseIcon name="calendar" :size="18" /> {{ when(b.starts_at) }}
                    </span>
                    <span class="booking__place">{{ restaurants[b.restaurant_id] }}</span>
                    <span class="booking__meta">
                      <BaseIcon name="users" :size="15" /> {{ b.party_size }} ·
                      Table #{{ b.restaurant_table_id }}
                    </span>
                    <span class="status" :class="`status--${b.status}`">{{ b.status }}</span>
                    <BaseIcon class="booking__arrow" name="arrowRight" :size="18" />
                  </router-link>
                </li>
              </ul>
            </section>
          </template>
        </div>
      </div>
    </template>

    <div v-else class="panel panel--narrow">
      <div class="page-head"><h1 style="font-size: 1.8rem">My account</h1></div>
      <p class="empty">Sign in to see your profile and bookings.</p>
      <div class="actions" style="justify-content: center">
        <router-link class="btn btn--primary" :to="{ path: '/signin', query: { redirect: '/account' } }">
          <BaseIcon name="logIn" :size="17" /> Sign in
        </router-link>
        <router-link class="btn btn--ghost" :to="{ path: '/signin', query: { mode: 'register', redirect: '/account' } }">
          Create account
        </router-link>
      </div>
    </div>
  </section>
</template>
