# syntax=docker/dockerfile:1

# J1.1 — imagen de la web. Multi-stage y usuario no raíz.
# D1.1 (ADR-0007) — Vite compila el cliente a dist/public y tsc el servidor a dist/server.
# El runtime sigue sirviendo HTTP en 3000 para que el healthcheck del compose
# (infra/docker-compose.yml) y el reverse_proxy de Caddy sigan siendo válidos.
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build --chown=node:node /app/dist ./dist
COPY --from=build --chown=node:node /app/package.json ./package.json
COPY --from=build --chown=node:node /app/node_modules ./node_modules
USER node
EXPOSE 3000
CMD ["npm", "run", "start"]
