import { createRouter, createWebHashHistory } from "vue-router";
import { session } from "./api.js";

// Every page is code-split: the browser downloads a route's chunk the first
// time it is visited, not on initial load.
const routes = [
  { path: "/", component: () => import("./pages/HomePage.vue"), meta: { title: "TableFlow" } },
  { path: "/restaurants", component: () => import("./pages/RestaurantList.vue"), meta: { title: "Restaurants — TableFlow" } },
  { path: "/restaurants/:id", component: () => import("./pages/RestaurantDetail.vue"), meta: { title: "Menu — TableFlow" } },
  { path: "/book/:id", component: () => import("./pages/BookTable.vue"), meta: { title: "Reserve — TableFlow", requiresAuth: true } },
  { path: "/confirmation/:id", component: () => import("./pages/BookingConfirmed.vue"), meta: { title: "Confirmed — TableFlow", requiresAuth: true } },
  { path: "/signin", component: () => import("./pages/SignInPage.vue"), meta: { title: "Sign in — TableFlow" } },
  { path: "/account", component: () => import("./pages/AccountPage.vue"), meta: { title: "Account — TableFlow" } },
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

const router = createRouter({
  // Hash history needs no server rewrite rule, so the built bundle works
  // behind any static host without extra configuration.
  history: createWebHashHistory(),
  routes,
  scrollBehavior: (to, from, saved) => saved || { top: 0 },
});

router.beforeEach((to) => {
  // Remember where the user was heading so the funnel resumes after sign-in
  // instead of restarting.
  if (to.meta.requiresAuth && !session.user) {
    return { path: "/signin", query: { redirect: to.fullPath } };
  }
  return true;
});

router.afterEach((to) => {
  document.title = to.meta.title || "TableFlow";
});

export default router;
