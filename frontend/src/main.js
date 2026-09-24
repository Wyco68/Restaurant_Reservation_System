import { createApp } from "vue";
import router from "./router.js";
import { refreshHealth } from "./api.js";
import App from "./App.vue";
import "./style.css";

createApp(App).use(router).mount("#app");

refreshHealth();
setInterval(refreshHealth, 30000);
