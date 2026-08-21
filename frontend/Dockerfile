# TerraSight frontend — Next.js standalone build, multi-stage for a lean
# runtime image. Talks to the backend only through NEXT_PUBLIC_API_URL (a
# public base URL) — never through Supabase or a service-role key.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Public, non-secret: base URL the browser/server use to reach the backend.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
# Emit .next/standalone for the lean runtime stage (Vercel builds omit this).
ENV NEXT_OUTPUT=standalone
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
ENV PORT=3000
CMD ["node", "server.js"]
