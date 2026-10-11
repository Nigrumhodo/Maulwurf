import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "url";
import { defineConfig } from "vite";

// D1.1 — Vite + React 19 + Tailwind 4 (ADR-0007). Sustituye el scaffold de Next.js.
// El dev server escucha en 3000 para reproducir el mismo origen de producción tras Caddy
// y validar cookies same-origin en local (docs/plan/S1.md §6, D1.1).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 3000,
    strictPort: true,
  },
  preview: {
    port: 3000,
    strictPort: true,
  },
  build: {
    // `dist/public` para el cliente y `dist/server` para el servidor (tsconfig.server.json),
    // de modo que `node dist/server/index.js` sirve estáticos sin colisionar salidas.
    outDir: "dist/public",
    sourcemap: true,
  },
});
