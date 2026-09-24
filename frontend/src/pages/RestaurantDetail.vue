<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api, money } from "../api.js";
import BaseIcon from "../components/BaseIcon.vue";
import FlowStepper from "../components/FlowStepper.vue";
import ErrorNote from "../components/ErrorNote.vue";

const route = useRoute();
const restaurant = ref(null);
const menu = ref([]);
const loading = ref(true);
const menuBusy = ref(false);
const error = ref("");

// Printed-menu order, not alphabetical.
const COURSE_ORDER = ["Starter", "Main", "Side", "Dessert", "Drink"];

const courses = computed(() => {
  const groups = new Map();
  for (const item of menu.value) {
    if (!groups.has(item.category)) groups.set(item.category, []);
    groups.get(item.category).push(item);
  }
  const rank = (c) => (COURSE_ORDER.indexOf(c) + 1) || COURSE_ORDER.length + 1;
  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([name, items]) => ({ name, items }));
});

async function loadRestaurant() {
  loading.value = true;
  error.value = "";
  try {
    restaurant.value = await api(`/api/v1/restaurants/${route.params.id}`);
  } catch (e) {
    error.value = e.message;
    restaurant.value = null;
  } finally {
    loading.value = false;
  }
}

// A menu is read as a whole, so it loads in one request (the API caps
// `limit` at 100; seeded restaurants carry ~20 items) instead of paging.
async function loadMenu() {
  menuBusy.value = true;
  try {
    const data = await api(`/api/v1/products?restaurant_id=${route.params.id}&limit=100`);
    menu.value = data.items;
  } catch {
    menu.value = [];
  } finally {
    menuBusy.value = false;
  }
}

function reload() {
  loadRestaurant();
  loadMenu();
}

onMounted(reload);
watch(() => route.params.id, reload);
</script>

<template>
  <section class="section container">
    <FlowStepper :current="1" />
    <ErrorNote :message="error" />

    <div v-if="loading" class="skeleton" style="height: 160px" />

    <template v-else-if="restaurant">
      <router-link class="back-link" to="/restaurants">
        <BaseIcon name="arrowLeft" :size="16" /> All restaurants
      </router-link>

      <header class="venue-head">
        <div>
          <p class="eyebrow">{{ restaurant.cuisine }} · {{ restaurant.city }}</p>
          <h1>{{ restaurant.name }}</h1>
          <p class="venue-head__meta">
            <span v-if="restaurant.avg_rating" class="rating">
              <BaseIcon name="star" :size="16" /> {{ restaurant.avg_rating }}
            </span>
            <span>{{ "$".repeat(restaurant.price_range) }}</span>
            <span><BaseIcon name="pin" :size="15" /> {{ restaurant.address }}</span>
          </p>
        </div>
        <router-link class="btn btn--primary btn--lg" :to="`/book/${restaurant.id}`">
          Book a table <BaseIcon name="arrowRight" :size="18" />
        </router-link>
      </header>

      <!-- Read-only by design: no borders, hover states or pointer cursor,
           so nothing here competes with the booking button. -->
      <section class="menu-sheet" aria-labelledby="menu-title">
        <div class="menu-sheet__head">
          <h2 id="menu-title">Menu</h2>
          <span class="menu-sheet__note">{{ menu.length }} dishes</span>
        </div>

        <div v-if="menuBusy" class="menu-sheet__columns" aria-busy="true">
          <div v-for="n in 6" :key="n" class="skeleton" style="min-height: 28px" />
        </div>

        <p v-else-if="!menu.length" class="empty">No menu published yet.</p>

        <div v-else class="menu-sheet__columns">
          <section v-for="course in courses" :key="course.name" class="course">
            <h3 class="course__title">{{ course.name }}</h3>
            <ul class="course__list">
              <li v-for="item in course.items" :key="item._id" class="dish"
                  :class="{ 'is-unavailable': item.is_available === false }">
                <span class="dish__name">{{ item.name }}</span>
                <span class="dish__leader" aria-hidden="true" />
                <span class="dish__price">{{ money(item.price, item.currency) }}</span>
                <span v-if="item.is_available === false" class="dish__flag">Sold out today</span>
              </li>
            </ul>
          </section>
        </div>
      </section>
    </template>

    <p v-else class="empty">Restaurant not found.</p>
  </section>
</template>
