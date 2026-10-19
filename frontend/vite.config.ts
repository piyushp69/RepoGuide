import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server proxies /api to the local FastAPI backend (see scripts/dev.py).
const backend = process.env.REPOGUIDE_BACKEND_URL ?? "http://127.0.0.1:8000";
const port = Number(process.env.REPOGUIDE_FRONTEND_PORT ?? 5173);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port,
    strictPort: true,
    proxy: { "/api": { target: backend } },
  },
  preview: { host: "127.0.0.1", port: 4173 },
  build: {
    // Mermaid is large but is only loaded on the Overview page (dynamic import).
    chunkSizeWarningLimit: 3000,
  },
});
