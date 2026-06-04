# BE-GO-READY-INSTALL-106-W10-DOCKER-FRONTEND
#
# Production frontend image: build the Vite app, then serve the static
# bundle from nginx. Runtime contains no Node toolchain.

FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# The URL is compiled into the Vite bundle. Compose defaults this to
# the backend port published on the Docker host.
ARG VITE_API_URL=http://localhost:5004
ENV VITE_API_URL=${VITE_API_URL}

# #35: Sentry DSN (opzionale). Vuoto di default -> Sentry no-op a runtime.
ARG VITE_SENTRY_DSN=""
ENV VITE_SENTRY_DSN=${VITE_SENTRY_DSN}

RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
