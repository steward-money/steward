# Bases pinned by digest (release spec 2026-11-05, section 6); a digest bump changes them here, tests/test_image_files.py checks their shape.
FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS base
RUN useradd -u 1000 -m steward
COPY --from=ghcr.io/astral-sh/uv:0.9@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock alembic.ini README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-cache
COPY --from=web /web/dist ./web/dist
ENV STEWARD_DATA_DIR=/data STEWARD_HOST=0.0.0.0 STEWARD_PORT=8420 PATH="/app/.venv/bin:$PATH"
RUN mkdir -p /data && chown steward:steward /data
USER steward
VOLUME ["/data"]
EXPOSE 8420
CMD ["steward", "serve"]
