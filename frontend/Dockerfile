# syntax=docker/dockerfile:1

# ----------------------------------------------------------------- build --
FROM node:20-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json* ./
# `npm ci` when a lockfile is present, `npm install` otherwise.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .

# Vite inlines env vars at build time, so the backend URL is a build arg.
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# --------------------------------------------------------------- runtime --
# Static bundle only: ~25 MB final image, no Node runtime shipped.
FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget --quiet --spider http://127.0.0.1:3000/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
