FROM node:20-bookworm-slim AS base
RUN printf '%s\n' \
    'Types: deb' \
    'URIs: http://mirrors.tuna.tsinghua.edu.cn/debian' \
    'Suites: bookworm bookworm-updates' \
    'Components: main' \
    'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
    '' \
    'Types: deb' \
    'URIs: http://mirrors.tuna.tsinghua.edu.cn/debian-security' \
    'Suites: bookworm-security' \
    'Components: main' \
    'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
    > /etc/apt/sources.list.d/debian.sources
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && \
    sed -i 's|http://mirrors.tuna.tsinghua.edu.cn|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
    rm -rf /var/lib/apt/lists/*

FROM base AS build-tools

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
RUN npm config set registry https://registry.npmmirror.com && \
    npm install turbo@^2.5.6 --global && \
    corepack enable && \
    npm_config_registry=https://registry.npmmirror.com corepack prepare pnpm@9.5.0 --activate && \
    pnpm config set registry https://registry.npmmirror.com

FROM build-tools AS pruner

WORKDIR /app

COPY . .
RUN turbo prune --scope=web --docker

FROM build-tools AS builder

WORKDIR /app

# First install the dependencies (as they change less often)
COPY --from=pruner /app/out/pnpm-lock.yaml ./pnpm-lock.yaml
COPY --from=pruner /app/out/pnpm-workspace.yaml ./pnpm-workspace.yaml
COPY --from=pruner /app/out/json/ .

ARG TARGETARCH
RUN --mount=type=cache,id=langfuse-web-pnpm-${TARGETARCH},target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir=/pnpm/store --reporter=ndjson

ENV DOCKER_BUILD=1

# pass public variables in build step
ARG NEXT_PUBLIC_PLAIN_APP_ID
ARG NEXT_PUBLIC_LANGFUSE_CLOUD_REGION
ENV NEXT_PUBLIC_LANGFUSE_CLOUD_REGION=$NEXT_PUBLIC_LANGFUSE_CLOUD_REGION
ARG NEXT_PUBLIC_DEMO_ORG_ID
ENV NEXT_PUBLIC_DEMO_ORG_ID=$NEXT_PUBLIC_DEMO_ORG_ID
ARG NEXT_PUBLIC_DEMO_PROJECT_ID
ENV NEXT_PUBLIC_DEMO_PROJECT_ID=$NEXT_PUBLIC_DEMO_PROJECT_ID
ARG NEXT_PUBLIC_SIGN_UP_DISABLED
ENV NEXT_PUBLIC_SIGN_UP_DISABLED=$NEXT_PUBLIC_SIGN_UP_DISABLED
ARG NEXT_PUBLIC_TURNSTILE_SITE_KEY
ENV NEXT_PUBLIC_TURNSTILE_SITE_KEY=$NEXT_PUBLIC_TURNSTILE_SITE_KEY
ARG NEXT_PUBLIC_POSTHOG_KEY
ENV NEXT_PUBLIC_POSTHOG_KEY=$NEXT_PUBLIC_POSTHOG_KEY
ARG NEXT_PUBLIC_POSTHOG_HOST
ENV NEXT_PUBLIC_POSTHOG_HOST=$NEXT_PUBLIC_POSTHOG_HOST
ARG NEXT_PUBLIC_LANGFUSE_TRACING_SAMPLE_RATE
ENV NEXT_PUBLIC_LANGFUSE_TRACING_SAMPLE_RATE=$NEXT_PUBLIC_LANGFUSE_TRACING_SAMPLE_RATE
ARG NEXT_PUBLIC_BASE_PATH
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH

# Accept build id as NEXT_PUBLIC_BUILD_ID
ARG NEXT_PUBLIC_BUILD_ID
ENV NEXT_PUBLIC_BUILD_ID=$NEXT_PUBLIC_BUILD_ID

# Copy source code of isolated subworkspace
COPY --from=pruner /app/out/full/ .

# remove middleware.ts if it exists - not needed in self-hosted environments
RUN rm -f ./web/src/middleware.ts

# Next.js collects completely anonymous telemetry data about general usage.
# Learn more here: https://nextjs.org/telemetry
# Uncomment the following line in case you want to disable telemetry during the build.
ENV NEXT_TELEMETRY_DISABLED=1
# set the CI flag to true to get CI specific logs
ENV CI=true

ARG SKIP_BUILD_LINT=false
RUN --mount=type=cache,id=langfuse-web-next-${TARGETARCH},target=/app/web/.next/cache,sharing=locked \
    SKIP_BUILD_LINT="$SKIP_BUILD_LINT" NODE_OPTIONS='--max-old-space-size=8192' \
    turbo run build --filter=web...

# Production image, copy all the files and run next
FROM base AS runner

WORKDIR /app

ENV NODE_ENV=production
# Uncomment the following line in case you want to disable telemetry during runtime.
ENV NEXT_TELEMETRY_DISABLED=1
# Needed to re-enable validation of environment variables during runtime
ENV DOCKER_BUILD=0
# Set NEXT_MANUAL_SIG_HANDLE for runtime
ENV NEXT_MANUAL_SIG_HANDLE=true

RUN apt-get update && \
    apt-get install -y --no-install-recommends dumb-init tzdata python3 python3-pip && \
    rm -rf /var/lib/apt/lists/*
COPY ./scripts/requirements.txt ./scripts/requirements.txt
RUN python3 -m pip install --break-system-packages --no-cache-dir \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r ./scripts/requirements.txt

# Don't run production as root
ARG UID=1001
ARG GID=1001
RUN addgroup --system --gid ${GID} nodejs
RUN adduser --system --uid ${UID} --ingroup nodejs nextjs

# Install dd-trace only if NEXT_PUBLIC_LANGFUSE_CLOUD_REGION is configured
ARG NEXT_PUBLIC_LANGFUSE_CLOUD_REGION
ENV NEXT_PUBLIC_LANGFUSE_CLOUD_REGION=$NEXT_PUBLIC_LANGFUSE_CLOUD_REGION
RUN if [ -n "$NEXT_PUBLIC_LANGFUSE_CLOUD_REGION" ]; then \
        npm install --registry=https://registry.npmmirror.com --no-package-lock --no-save dd-trace@5.36.0; \
    fi

COPY --from=builder --chown=nextjs:nodejs /app/web/next.config.mjs .
COPY --from=builder --chown=nextjs:nodejs /app/web/package.json .

# Automatically leverage output traces to reduce image size
# https://nextjs.org/docs/advanced-features/output-file-tracing
COPY --from=builder --chown=nextjs:nodejs /app/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/web/.next/static ./web/.next/static
COPY --from=builder --chown=nextjs:nodejs /app/web/public ./web/public

COPY --from=builder --chown=nextjs:nodejs /app/packages/shared/prisma ./packages/shared/prisma
COPY --chown=nextjs:nodejs ./db ./db
COPY --chown=nextjs:nodejs ./scripts/postgres_sync_schema.py ./scripts/postgres_sync_schema.py
COPY --chown=nextjs:nodejs ./scripts/clickhouse_sync_schema.py ./scripts/clickhouse_sync_schema.py

COPY --chown=nextjs:nodejs ./web/entrypoint.sh ./web/entrypoint.sh
RUN chmod +x ./web/entrypoint.sh

USER nextjs

# Default port to 3000
ENV PORT=3000
ARG NEXT_PUBLIC_BUILD_ID
ENV BUILD_ID=$NEXT_PUBLIC_BUILD_ID

# Docker ENTRYPOINT (dumb-init) is covered by semantic versioning, not the entrypoint.sh itself
# Reasoning: ENTRYPOINT is overridden by some self-hosted deployments, thus changing this is breaking
ENTRYPOINT ["dumb-init", "--", "./web/entrypoint.sh"]

# startup command - use dd-trace if NEXT_PUBLIC_LANGFUSE_CLOUD_REGION is configured
CMD ["sh", "-c", "if [ -n \"$NEXT_PUBLIC_LANGFUSE_CLOUD_REGION\" ]; then exec node --import dd-trace/initialize.mjs ./web/server.js --keepAliveTimeout 110000; else exec node ./web/server.js --keepAliveTimeout 110000; fi"]
