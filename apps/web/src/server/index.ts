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
import { fileURLToPath } from "url";

const publicDir = fileURLToPath(new URL("../public", import.meta.url));
const port = 3000;

const app = express();

// Security headers for the shell and static assets. The CSP allows only this origin plus the
// Google Fonts stylesheet and files that index.html loads; login goes to Google by navigation,
// which the CSP does not restrict.
const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' https://fonts.googleapis.com",
  "font-src https://fonts.gstatic.com",
  "img-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

app.disable("x-powered-by");
app.use((_req, res, next) => {
  res.set({
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
  });
  next();
});
app.use(express.static(publicDir, { index: false }));

// Fallback de la SPA: cualquier ruta de cliente (/biblioteca, /chat, …) devuelve
// index.html y deja que React Router resuelva en el navegador. Se registra como
// middleware final en vez de ruta con comodín porque Express 5 usa path-to-regexp v8.
app.use((_req, res) => {
  res.sendFile("index.html", { root: publicDir });
});

const server = app.listen(port, () => {
  console.log(`maulwurf-web escuchando en http://127.0.0.1:${port}`);
});

// As PID 1, Node ignores SIGTERM unless it is handled: without this, `docker compose stop`
// waits for its timeout and then sends SIGKILL.
for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.on(signal, () => {
    server.close(() => process.exit(0));
  });
}
