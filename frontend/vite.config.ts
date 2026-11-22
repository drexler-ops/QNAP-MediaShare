import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies the API to a locally running backend (uvicorn on :8080 by default).
export default defineConfig({
  plugins: [react()],
  server: {
    // API_TARGET=http://localhost:18080 npm run dev  -> use a Docker stack on another port
    proxy: { "/api": process.env.API_TARGET || "http://localhost:8080" },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 1200 },
});
