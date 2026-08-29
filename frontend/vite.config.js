// frontend/vite.config.js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// "/api/*" is proxied to the FastAPI backend and the "/api" prefix is stripped,
// so the frontend calls "/api/start_lecture" -> "http://localhost:8000/start_lecture".
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
