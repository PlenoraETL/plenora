# BE-GO-READY-INSTALL-106-W10-DOCKER-FRONTEND
#
# Production frontend image: build the Vite app, then serve the static
# bundle from nginx. Runtime contains no Node toolchain.

FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# Empty means same-origin (/api through nginx). Set an absolute URL only when
# frontend and backend are intentionally published on different origins.
ARG VITE_API_URL=""
ENV VITE_API_URL=${VITE_API_URL}

# #35: Sentry DSN (opzionale). Vuoto di default -> Sentry no-op a runtime.
ARG VITE_SENTRY_DSN=""
ENV VITE_SENTRY_DSN=${VITE_SENTRY_DSN}
ARG VITE_SENTRY_TRACES_SAMPLE_RATE="0.1"
ENV VITE_SENTRY_TRACES_SAMPLE_RATE=${VITE_SENTRY_TRACES_SAMPLE_RATE}

RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10 AS runtime

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
