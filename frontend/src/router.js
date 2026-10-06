import { createRouter, createWebHashHistory } from "vue-router";
import { session } from "./api.js";

// Every page is lazy-loaded: the browser fetches a route's module the first
// time it is visited, not on initial load.
const routes = [
  { path: "/", component: () => import("./pages/HomePage.js"), meta: { title: "TableFlow" } },
  { path: "/restaurants", component: () => import("./pages/RestaurantList.js"), meta: { title: "Restaurants — TableFlow" } },
  { path: "/restaurants/:id", component: () => import("./pages/RestaurantDetail.js"), meta: { title: "Menu — TableFlow" } },
  { path: "/book/:id", component: () => import("./pages/BookTable.js"), meta: { title: "Reserve — TableFlow", requiresAuth: true } },
  { path: "/confirmation/:id", component: () => import("./pages/BookingConfirmed.js"), meta: { title: "Confirmed — TableFlow", requiresAuth: true } },
  { path: "/signin", component: () => import("./pages/SignInPage.js"), meta: { title: "Sign in — TableFlow" } },
  { path: "/account", component: () => import("./pages/AccountPage.js"), meta: { title: "Account — TableFlow" } },
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

const router = createRouter({
  // Hash history needs no server rewrite rule, so the client works behind
  // any static host without extra configuration.
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
