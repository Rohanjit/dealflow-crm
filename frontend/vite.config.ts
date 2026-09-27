import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the Django API runs on :8000 and Vite proxies /api to it, so the
// browser sees one origin (session cookie + CSRF work exactly as in production).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500 },
  test: { environment: "node" },
} as never);
