import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Relative asset URLs, so the built UI works under any path prefix
  // (Home Assistant serves it from /api/hassio_ingress/<token>/).
  base: "./",
  server: {
    port: 5173,
    // Same-origin in the browser: the UI calls /api/... and Vite forwards it to
    // the FastAPI process, so there is no CORS dance during development.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
