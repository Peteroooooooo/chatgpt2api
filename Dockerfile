ARG BUILDPLATFORM
ARG TARGETPLATFORM
ARG TARGETARCH
ARG NODE_IMAGE=node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32
ARG PYTHON_IMAGE=python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS web-build

WORKDIR /app/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY VERSION /app/VERSION
COPY CHANGELOG.md /app/CHANGELOG.md
COPY web ./
RUN NEXT_PUBLIC_APP_VERSION="$(cat /app/VERSION)" npm run build


FROM --platform=$TARGETPLATFORM ${PYTHON_IMAGE} AS app

ARG TARGETPLATFORM
ARG TARGETARCH
ARG UV_VERSION=0.11.32
ARG APP_VERSION=1.8.0-peter.4
ARG VCS_REF=local
ARG UPSTREAM_REF=e55aef2829e7bf1d7256d6ff3feb4b40b02743d2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 安装系统依赖
# - git: Git 存储后端需要
# - libpq-dev: PostgreSQL 客户端库
# - gcc: 编译 psycopg2-binary 需要
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libpq-dev \
    gcc \
    openssl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py ./
COPY config.json ./
COPY VERSION ./
COPY api ./api
COPY services ./services
COPY utils ./utils
COPY scripts ./scripts
COPY --from=web-build /app/web/out ./web_dist

LABEL org.opencontainers.image.title="chatgpt2api-peter" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/basketikun/chatgpt2api" \
      org.opencontainers.image.description="Reproducible chatgpt2api build with authenticated Plus trial eligibility checks" \
      com.peter.chatgpt2api.upstream-revision="${UPSTREAM_REF}"

EXPOSE 80

CMD ["/app/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80", "--access-log"]
