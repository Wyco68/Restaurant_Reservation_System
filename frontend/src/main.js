import { createApp } from "vue";
import router from "./router.js";
import { refreshHealth } from "./api.js";
import App from "./App.js";

createApp(App).use(router).mount("#app");

refreshHealth();
setInterval(refreshHealth, 30000);
