# syntax=docker/dockerfile:1

# Multi-stage build. The runner carries only the standalone server output plus
# the Prisma engine and CLI needed to migrate on boot — no dev dependencies, no
# source tree, and it runs as a non-root user.

# ───────────────────────────────────────────────────────────────── deps ──
FROM node:22-alpine AS deps
WORKDIR /app
# libc6-compat: the Prisma query engine binary expects glibc symbols on Alpine.
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json* ./
# `npm ci` when a lockfile exists (reproducible), `npm install` otherwise so a
# fresh clone without one still builds.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# ──────────────────────────────────────────────────────────────── build ──
FROM node:22-alpine AS builder
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# The client is generated at build time so the bundle can import it, and
# DATABASE_URL is only needed for its shape — no connection is opened.
ENV DATABASE_URL="postgresql://build:build@localhost:5432/build"
ENV NEXT_TELEMETRY_DISABLED=1

# `NEXT_PUBLIC_*` values are inlined into the client bundle by `next build`, so they must be
# present as *build* arguments — setting them at container runtime has no effect on code that
# already shipped to the browser. This is the one class of configuration that cannot be
# deferred to deploy time.
ARG NEXT_PUBLIC_API_URL="http://localhost:8000"
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_MARKETING_URL="http://localhost:3001"
ENV NEXT_PUBLIC_MARKETING_URL=$NEXT_PUBLIC_MARKETING_URL
RUN npx prisma generate
RUN npm run build

# ─────────────────────────────────────────────────────────────── runner ──
FROM node:22-alpine AS runner
WORKDIR /app
# ffmpeg extracts the audio track from video uploads and compresses oversized audio
# (SPEC-010 §4.1). Roughly 30 MB in the image, in exchange for MOV/MKV/AVI support and
# for a 300 MB screen recording becoming a couple of MB of audio before it is sent.
RUN apk add --no-cache libc6-compat curl ffmpeg

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 nextjs

# Standalone output already contains the pruned node_modules Next needs.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

# Migrations + seed run from the entrypoint, so these must be present at runtime.
COPY --from=builder --chown=nextjs:nodejs /app/prisma ./prisma
COPY --from=builder --chown=nextjs:nodejs /app/node_modules/prisma ./node_modules/prisma
COPY --from=builder --chown=nextjs:nodejs /app/node_modules/@prisma ./node_modules/@prisma
COPY --from=builder --chown=nextjs:nodejs /app/node_modules/.prisma ./node_modules/.prisma
COPY --chown=nextjs:nodejs docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:3000/api/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["node", "server.js"]
