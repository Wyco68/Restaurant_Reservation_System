<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api.js";
import BaseIcon from "../components/BaseIcon.vue";

const featured = ref([]);
const loading = ref(true);

onMounted(async () => {
  try {
    const data = await api("/api/v1/restaurants?limit=3");
    featured.value = data.items;
  } catch {
    featured.value = [];
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div>
    <section class="hero container">
      <h1>Find a table worth sitting at</h1>
      <p>
        Browse restaurants by city and cuisine, read the menu, and reserve a
        table in three steps. No account needed to look around.
      </p>
      <div class="hero__actions">
        <router-link class="btn btn--primary btn--lg" to="/restaurants">
          <BaseIcon name="search" :size="18" /> Browse restaurants
        </router-link>
        <router-link class="btn btn--ghost btn--lg" to="/signin">Sign in to book</router-link>
      </div>
    </section>

    <section class="section--tight container">
      <div class="page-head"><h2>Top rated right now</h2></div>

      <div v-if="loading" class="grid">
        <div v-for="n in 3" :key="n" class="skeleton" />
      </div>

      <p v-else-if="!featured.length" class="empty">
        Nothing to show yet — the API may still be starting.
      </p>

      <div v-else class="grid stagger">
        <router-link v-for="(r, i) in featured" :key="r.id"
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
    </section>
  </div>
</template>
