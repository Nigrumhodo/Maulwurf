// D1.1 — Servidor de producción. Sirve `dist/public/` y el fallback de la SPA.
//
// Por qué existe: `infra/docker-compose.yml` declara `expose: ["3000"]` con un
// healthcheck HTTP, y `infra/Caddyfile` hace `reverse_proxy web:3000`. La app no
// recibe variables de entorno (el compose no le pasa ninguna), así que este
// proceso no puede custodiar secretos ni hablar con la BD: solo entrega estáticos.
//
// Nada de esto cachea respuestas privadas: el `Cache-Control: no-store` de las
// rutas con sesión lo aplican el cliente y la API (D2.8).

import express from "express";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { securityHeaders, spaFallback } from "./security.js";

function resolvePublicDir(moduleUrl: string): string {
  const bundled = fileURLToPath(new URL("../public", moduleUrl));
  // Built server: dist/server/index.js serves dist/public, which holds index.html.
  if (fs.existsSync(path.join(bundled, "index.html"))) return bundled;
  // Vitest loads this file from src/server; the static files live in ../../public.
  const sourcePublic = fileURLToPath(new URL("../../public", moduleUrl));
  if (fs.existsSync(sourcePublic)) return sourcePublic;
  return bundled;
}

function resolveIndexHtml(moduleUrl: string, publicDir: string): string {
  const bundledIndex = path.join(publicDir, "index.html");
  if (fs.existsSync(bundledIndex)) return bundledIndex;
  const sourceIndex = fileURLToPath(new URL("../../index.html", moduleUrl));
  if (fs.existsSync(sourceIndex)) return sourceIndex;
  return bundledIndex;
}

export function createApp(moduleUrl: string = import.meta.url): express.Express {
  const publicDir = resolvePublicDir(moduleUrl);
  const indexHtml = resolveIndexHtml(moduleUrl, publicDir);
  const app = express();

  app.disable("x-powered-by");
  app.use((req, res, next) => {
    res.set(securityHeaders(req.secure, req.get("x-forwarded-proto"), process.env.NODE_ENV));
    next();
  });
  app.use(express.static(publicDir, { index: false }));

  // Fallback de la SPA: cualquier ruta de cliente (/biblioteca, /chat, …) devuelve
  // index.html y deja que React Router resuelva en el navegador. Se registra como
  // middleware final en vez de ruta con comodín porque Express 5 usa path-to-regexp v8.
  // Only GET and HEAD receive that document. Other methods are 405, and a missing
  // /assets or /api path is 404 so a bad URL is not masked as the shell.
  app.use((req, res) => {
    const decision = spaFallback(req.method, req.path);
    if (decision === "method_not_allowed") {
      res.set("Allow", "GET, HEAD");
      res.status(405).json({ error: { code: "method_not_allowed" } });
      return;
    }
    if (decision === "not_found") {
      res.status(404).json({ error: { code: "not_found" } });
      return;
    }
    res.sendFile(indexHtml, (error) => {
      if (error && !res.headersSent) {
        res.status(404).type("text/plain").send("Not Found");
      }
    });
  });

  return app;
}

function isDirectExecution(): boolean {
  const entry = process.argv[1];
  if (entry === undefined || entry === "") return false;
  return import.meta.url === pathToFileURL(path.resolve(entry)).href;
}

// Vitest imports createApp(); that process must not bind the production port.
if (process.env.VITEST !== "true" && isDirectExecution()) {
  const port = 3000;
  const server = createApp().listen(port, () => {
    console.log(`maulwurf-web escuchando en http://127.0.0.1:${port}`);
  });

  // As PID 1, Node ignores SIGTERM unless it is handled: without this, `docker compose stop`
  // waits for its timeout and then sends SIGKILL.
  for (const signal of ["SIGTERM", "SIGINT"] as const) {
    process.on(signal, () => {
      server.close(() => process.exit(0));
    });
  }
}
