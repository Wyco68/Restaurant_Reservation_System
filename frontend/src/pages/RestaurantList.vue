<script setup>
import { onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, CITIES, CUISINES } from "../api.js";
import BaseIcon from "../components/BaseIcon.vue";
import FlowStepper from "../components/FlowStepper.vue";
import PagerNav from "../components/PagerNav.vue";
import ErrorNote from "../components/ErrorNote.vue";

const route = useRoute();
const router = useRouter();

const page = reactive({ items: [], page: 1, pages: 0, total: 0 });
const filters = reactive({ city: route.query.city || "", cuisine: route.query.cuisine || "" });
const busy = ref(false);
const error = ref("");

async function load(p = 1) {
  busy.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({ page: p, limit: 9 });
    if (filters.city) params.set("city", filters.city);
    if (filters.cuisine) params.set("cuisine", filters.cuisine);
    Object.assign(page, await api(`/api/v1/restaurants?${params}`));
  } catch (e) {
    error.value = e.message;
    Object.assign(page, { items: [], page: 1, pages: 0, total: 0 });
  } finally {
    busy.value = false;
  }
}

// Filters live in the query string, so a filtered list is shareable and the
// back button returns to the exact view the user left.
function applyFilters() {
  router.push({
    path: "/restaurants",
    query: {
      ...(filters.city ? { city: filters.city } : {}),
      ...(filters.cuisine ? { cuisine: filters.cuisine } : {}),
      page: 1,
    },
  });
}

const goPage = (p) =>
  router.push({ path: "/restaurants", query: { ...route.query, page: p } });

watch(() => route.query, (q) => {
  filters.city = q.city || "";
  filters.cuisine = q.cuisine || "";
  load(Number(q.page) || 1);
});

onMounted(() => load(Number(route.query.page) || 1));
</script>

<template>
  <section class="section container">
    <FlowStepper :current="1" />

    <div class="page-head">
      <h1>Restaurants</h1>
      <p>Filter by city and cuisine, then pick somewhere to eat.</p>
    </div>

    <form class="filters" @submit.prevent="applyFilters">
      <div class="field">
        <label for="f-city">City</label>
        <select id="f-city" v-model="filters.city">
          <option value="">Any city</option>
          <option v-for="c in CITIES" :key="c" :value="c">{{ c }}</option>
        </select>
      </div>
      <div class="field">
        <label for="f-cuisine">Cuisine</label>
        <select id="f-cuisine" v-model="filters.cuisine">
          <option value="">Any cuisine</option>
          <option v-for="c in CUISINES" :key="c" :value="c">{{ c }}</option>
        </select>
      </div>
      <button type="submit" class="btn btn--primary" :disabled="busy">
        <BaseIcon name="search" :size="17" /> Search
      </button>
    </form>

    <ErrorNote :message="error" />

    <div v-if="busy" class="grid">
      <div v-for="n in 6" :key="n" class="skeleton" />
    </div>

    <p v-else-if="!page.items.length" class="empty">
      No restaurants match those filters. Try widening the search.
    </p>

    <div v-else class="grid stagger">
      <router-link v-for="(r, i) in page.items" :key="r.id"
                   :to="`/restaurants/${r.id}`" class="card" :style="{ '--i': i }">
        <h3>{{ r.name }}</h3>
        <span class="card__meta">
          <BaseIcon name="pin" :size="15" /> {{ r.city }} · {{ r.cuisine }}
        </span>
        <span class="card__meta">
          <span class="rating"><BaseIcon name="star" :size="15" /> {{ r.avg_rating ?? "–" }}</span>
          <span>{{ "$".repeat(r.price_range) }}</span>
        </span>
      </router-link>
    </div>

    <PagerNav :page="page.page" :pages="page.pages" :total="page.total"
              :busy="busy" @go="goPage" />
  </section>
</template>
