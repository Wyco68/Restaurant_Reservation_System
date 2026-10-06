import { onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api, money } from "../api.js";
import BaseIcon from "../components/BaseIcon.js";
import FlowStepper from "../components/FlowStepper.js";
import PagerNav from "../components/PagerNav.js";
import ErrorNote from "../components/ErrorNote.js";

export default {
  name: "RestaurantDetail",
  components: { BaseIcon, FlowStepper, PagerNav, ErrorNote },
  setup() {
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

    return { restaurant, menu, loading, menuBusy, error, loadMenu, money };
  },
  template: `
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
          <router-link class="btn btn--primary btn--lg" :to="'/book/' + restaurant.id">
            Book a table <BaseIcon name="arrowRight" :size="18" />
          </router-link>
          <router-link class="btn btn--ghost" to="/restaurants">
            <BaseIcon name="arrowLeft" :size="16" /> All restaurants
          </router-link>
        </div>

        <h2>Menu</h2>
        <p class="field__hint" style="margin-bottom: var(--space-3)">{{ menu.total }} items</p>

        <div v-if="menuBusy" class="skeleton" style="min-height: 280px" />

        <p v-else-if="!menu.items.length" class="empty">No menu items published yet.</p>

        <ul v-else class="menu-list stagger">
          <li v-for="(p, i) in menu.items" :key="p._id" class="menu-item"
              :class="{ 'is-unavailable': p.is_available === false }" :style="{ '--i': i }">
            <div class="menu-item__line">
              <span class="menu-item__name">{{ p.name }}</span>
              <span class="menu-item__leader" aria-hidden="true" />
              <span class="menu-item__price">{{ money(p.price, p.currency) }}</span>
            </div>
            <span class="menu-item__category">
              {{ p.category }}<template v-if="p.is_available === false"> · Unavailable</template>
            </span>
          </li>
        </ul>

        <PagerNav :page="menu.page" :pages="menu.pages" :total="menu.total"
                  :busy="menuBusy" @go="loadMenu" />
      </template>

      <p v-else class="empty">Restaurant not found.</p>
    </section>
  `,
};
