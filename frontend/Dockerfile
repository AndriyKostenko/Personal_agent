# syntax=docker/dockerfile:1

# ───────────── stage 1: build the static site ─────────────
FROM node:24-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY . .

# The address of the API is compiled into the bundle. The default is a relative path: the nginx
# of this image proxies /api and /media to the backend, so the site and the API share one origin
# (no CORS, one public port). For a backend on another host pass a full URL instead:
#   docker build --build-arg VITE_API_BASE=https://api.example.com/api/v1 .
ARG VITE_API_BASE=/api/v1
ENV VITE_API_BASE=${VITE_API_BASE}

RUN npm run build-only


# ───────────── stage 2: nginx (unprivileged, port 8080) ─────────────
FROM nginxinc/nginx-unprivileged:stable-alpine

# *.template files are processed with envsubst at start; only BACKEND_* variables are replaced,
# so nginx's own $variables are left alone
ENV BACKEND_UPSTREAM=http://backend:8000 \
    NGINX_ENVSUBST_FILTER=BACKEND_

COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/ || exit 1
