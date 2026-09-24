import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  // Relative asset paths so the built bundle works from any static host
  // without knowing its mount point.
  base: "./",
  server: {
    port: 5500,      // the origin the API's CORS config allows
    strictPort: true,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
