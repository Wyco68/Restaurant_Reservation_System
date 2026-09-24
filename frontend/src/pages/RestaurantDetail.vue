<script setup>
import { onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api, money } from "../api.js";
import BaseIcon from "../components/BaseIcon.vue";
import FlowStepper from "../components/FlowStepper.vue";
import PagerNav from "../components/PagerNav.vue";
import ErrorNote from "../components/ErrorNote.vue";

const route = useRoute();
const restaurant = ref(null);
const menu = reactive({ items: [], page: 1, pages: 0, total: 0 });
const loading = ref(true);
const menuBusy = ref(false);
const error = ref("");

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

async function loadMenu(p = 1) {
  menuBusy.value = true;
  try {
    Object.assign(menu, await api(
      `/api/v1/products?restaurant_id=${route.params.id}&page=${p}&limit=8`
    ));
  } catch {
    Object.assign(menu, { items: [], page: 1, pages: 0, total: 0 });
  } finally {
    menuBusy.value = false;
  }
}

function reload() {
  loadRestaurant();
  loadMenu(1);
}

onMounted(reload);
watch(() => route.params.id, reload);
</script>

<template>
  <section class="section container">
    <FlowStepper :current="1" />
    <ErrorNote :message="error" />

    <div v-if="loading" class="skeleton" style="height: 120px" />

    <template v-else-if="restaurant">
      <div class="page-head">
        <h1>{{ restaurant.name }}</h1>
        <p>
          {{ restaurant.cuisine }} · {{ restaurant.city }} ·
          {{ "$".repeat(restaurant.price_range) }}
          <span v-if="restaurant.avg_rating" class="rating">
            <BaseIcon name="star" :size="15" /> {{ restaurant.avg_rating }}
          </span>
        </p>
        <p>{{ restaurant.address }}</p>
      </div>

      <div class="actions" style="margin-bottom: var(--space-6)">
        <router-link class="btn btn--primary btn--lg" :to="`/book/${restaurant.id}`">
          Book a table <BaseIcon name="arrowRight" :size="18" />
        </router-link>
        <router-link class="btn btn--ghost" to="/restaurants">
          <BaseIcon name="arrowLeft" :size="16" /> All restaurants
        </router-link>
      </div>

      <h2>Menu</h2>
      <p class="field__hint" style="margin-bottom: var(--space-3)">{{ menu.total }} items</p>

      <div v-if="menuBusy" class="grid grid--menu">
        <div v-for="n in 4" :key="n" class="skeleton" style="min-height: 96px" />
      </div>

      <p v-else-if="!menu.items.length" class="empty">No menu items published yet.</p>

      <div v-else class="grid grid--menu stagger">
        <article v-for="(p, i) in menu.items" :key="p._id" class="card" :style="{ '--i': i }">
          <h3>{{ p.name }}</h3>
          <span class="pill">{{ p.category }}</span>
          <span class="card__price">{{ money(p.price, p.currency) }}</span>
        </article>
      </div>

      <PagerNav :page="menu.page" :pages="menu.pages" :total="menu.total"
                :busy="menuBusy" @go="loadMenu" />
    </template>

    <p v-else class="empty">Restaurant not found.</p>
  </section>
</template>
