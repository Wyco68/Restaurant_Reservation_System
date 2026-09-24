<script setup>
import { computed } from "vue";
import BaseIcon from "./BaseIcon.vue";

/* The one clickable card in the app. Everything about it says "go
   somewhere": a banner, an explicit call to action with an arrow, a lift on
   hover. Static content (the menu) deliberately shares none of these cues. */
const props = defineProps({ restaurant: { type: Object, required: true } });

// A stable hue per cuisine, kept inside the blue family (175-265) so the
// banners tell cuisines apart without leaving the palette.
const HUES = {
  Vegan: 175, Vietnamese: 185, Thai: 195, Japanese: 205, Italian: 215,
  Indian: 225, Chinese: 235, Korean: 245, Mexican: 255, French: 265,
};
const hue = computed(() => HUES[props.restaurant.cuisine] ?? 210);
</script>

<template>
  <router-link :to="`/restaurants/${restaurant.id}`" class="venue">
    <span class="venue__banner" :style="{ '--hue': hue }" aria-hidden="true">
      <span class="venue__initial">{{ restaurant.name.charAt(0) }}</span>
      <span class="venue__cuisine">{{ restaurant.cuisine }}</span>
    </span>

    <span class="venue__body">
      <h3 class="venue__name">{{ restaurant.name }}</h3>
      <span class="venue__meta">
        <span><BaseIcon name="pin" :size="15" /> {{ restaurant.city }}</span>
        <span class="rating" :aria-label="`Rated ${restaurant.avg_rating ?? 'not yet'}`">
          <BaseIcon name="star" :size="15" /> {{ restaurant.avg_rating ?? "–" }}
        </span>
        <span :aria-label="`Price level ${restaurant.price_range} of 4`">
          {{ "$".repeat(restaurant.price_range) }}
        </span>
      </span>
    </span>

    <span class="venue__cta">
      View menu &amp; book <BaseIcon name="arrowRight" :size="17" />
    </span>
  </router-link>
</template>
