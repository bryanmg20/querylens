# Tag + digest: el mismo build en cualquier maquina. Para actualizar,
# cambiar el digest (docker buildx imagetools inspect node:20-alpine).
ARG NODE_IMAGE=node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
# npm ci instala exactamente lo del lockfile (falla si no coincide con
# package.json): el mismo node_modules en cualquier maquina.
COPY package.json package-lock.json ./
RUN npm ci

FROM ${NODE_IMAGE} AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_AUTH_SERVICE_URL
ENV NEXT_PUBLIC_AUTH_SERVICE_URL=${NEXT_PUBLIC_AUTH_SERVICE_URL}
ARG NEXT_PUBLIC_API_REST_URL
ENV NEXT_PUBLIC_API_REST_URL=${NEXT_PUBLIC_API_REST_URL}
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM ${NODE_IMAGE} AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
