FROM node:20-alpine AS base
RUN sed -i 's|https://dl-cdn.alpinelinux.org/alpine|https://mirrors.tuna.tsinghua.edu.cn/alpine|g' /etc/apk/repositories

# Check https://github.com/nodejs/docker-node/tree/b4117f9333da4138b03a546ec926ef50a31506c3#nodealpine to understand why libc6-compat might be needed.
RUN apk upgrade --no-cache libcrypto3 libssl3 libc6-compat busybox ssl_client && \
    apk add --no-cache openssl

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
RUN turbo prune --scope=worker --docker

FROM build-tools AS workspace

WORKDIR /app

# First install the dependencies (as they change less often)
COPY --from=pruner /app/out/pnpm-lock.yaml ./pnpm-lock.yaml
COPY --from=pruner /app/out/pnpm-workspace.yaml ./pnpm-workspace.yaml
COPY --from=pruner /app/out/json/ .

ARG TARGETARCH

FROM workspace AS builder

RUN --mount=type=cache,id=langfuse-worker-pnpm-${TARGETARCH},target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir=/pnpm/store

# pass public variables in build step
ARG NEXT_PUBLIC_LANGFUSE_CLOUD_REGION
ARG NEXT_PUBLIC_DEMO_ORG_ID
ARG NEXT_PUBLIC_DEMO_PROJECT_ID
ARG NEXT_PUBLIC_POSTHOG_KEY
ARG NEXT_PUBLIC_POSTHOG_HOST

# Copy source code of isolated subworkspace
COPY --from=pruner /app/out/full/ .

RUN turbo run build --filter=worker...

FROM workspace AS production-deps

# The root install hooks are only used for development.
RUN --mount=type=cache,id=langfuse-worker-pnpm-${TARGETARCH},target=/pnpm/store \
    npm pkg delete scripts.preinstall scripts.prepare && \
    PRISMA_SKIP_POSTINSTALL_GENERATE=true \
    pnpm install --prod --frozen-lockfile --store-dir=/pnpm/store

COPY --from=builder /app/packages/shared/dist ./packages/shared/dist
COPY --from=builder /app/worker/dist ./worker/dist

# Copy the generated Prisma client into the production dependency tree.
RUN --mount=type=bind,from=builder,source=/app,target=/build \
    set -eu; \
    build_client=$(node -p 'require.resolve("@prisma/client", { paths: ["/build/packages/shared"] })'); \
    runtime_client=$(node -p 'require.resolve("@prisma/client", { paths: ["/app/packages/shared"] })'); \
    build_generated="$(dirname "$build_client")/../../.prisma/client"; \
    runtime_generated="$(dirname "$runtime_client")/../../.prisma/client"; \
    test -f "$build_generated/schema.prisma"; \
    mkdir -p "$runtime_generated"; \
    cp -a "$build_generated/." "$runtime_generated/"

FROM base AS runner

RUN apk add --no-cache dumb-init

WORKDIR /app

ENV NODE_ENV=production
ENV DOCKER_BUILD=0

ARG UID=1001
ARG GID=1001
RUN addgroup --system --gid "${GID}" expressjs && \
    adduser --system --uid "${UID}" --ingroup expressjs expressjs

COPY --from=production-deps --chown=expressjs:expressjs /app ./
COPY --chown=expressjs:expressjs --chmod=755 ./worker/entrypoint.sh ./worker/entrypoint.sh
USER expressjs

ARG NEXT_PUBLIC_BUILD_ID
ENV BUILD_ID=$NEXT_PUBLIC_BUILD_ID

EXPOSE 3030
ENV PORT=3030

# Docker ENTRYPOINT (dumb-init) is covered by semantic versioning, not the entrypoint.sh itself
# Reasoning: ENTRYPOINT is overridden by some self-hosted deployments, thus changing this is breaking
ENTRYPOINT ["dumb-init", "--", "./worker/entrypoint.sh"]

# startup command
CMD ["node", "worker/dist/index.js"]
