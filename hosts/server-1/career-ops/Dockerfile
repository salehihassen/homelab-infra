# One revision supplies core scripts, the production Web UI and workspace seed.
FROM node:24.18.0-bookworm-slim
ARG CAREER_OPS_REV=b5e4c26f21efee31559296495d794c9a9564a714
ARG CODEX_VERSION=0.164.0
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates curl bash ripgrep python3 util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global --no-audit --no-fund @openai/codex@${CODEX_VERSION} \
    && mkdir /app && chown node:node /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 NEXT_TELEMETRY_DISABLED=1
USER node
WORKDIR /app
RUN git init && git remote add origin https://github.com/career-ops-hq/career-ops.git \
    && git fetch --depth=1 origin ${CAREER_OPS_REV} && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${CAREER_OPS_REV}" \
    && test -f web/package-lock.json
COPY --chown=node:node core-package-lock.json /app/package-lock.json
RUN npm ci --no-audit --no-fund && npm --prefix web ci --no-audit --no-fund \
    && npm --prefix web run build
USER root
RUN cd /app && npx playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright && rm -rf /var/lib/apt/lists/*
COPY --chmod=755 entrypoint.sh scheduler.sh /usr/local/libexec/career-ops/
ENV NODE_ENV=production HOME=/home/node CODEX_HOME=/home/node/.codex \
    CAREER_OPS_ROOT=/workspace CAREER_OPS_CODE_ROOT=/workspace
USER node
WORKDIR /workspace
ENTRYPOINT ["/usr/local/libexec/career-ops/entrypoint.sh"]
CMD ["npm", "--prefix", "/workspace/web", "start"]
