import AppHeader from "./components/AppHeader.js";
import AppFooter from "./components/AppFooter.js";

export default {
  name: "App",
  components: { AppHeader, AppFooter },
  template: `
    <AppHeader />
    <main id="main" tabindex="-1">
      <router-view />
    </main>
    <AppFooter />
  `,
};
