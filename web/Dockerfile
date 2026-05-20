FROM python:3.11-slim AS base

# Install Node.js 20 (LTS) and system deps
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
       curl ca-certificates nodejs npm \
  && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# ── Python deps (cached layer) ────────────────────────────────────────
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync --no-dev

# ── Node deps (cached layer) ──────────────────────────────────────────
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci && npm ci --prefix frontend

# ── Source ────────────────────────────────────────────────────────────
COPY . .

# ── Build Vue frontend ────────────────────────────────────────────────
# VITE_API_BASE_URL injected at build time from docker-compose env
ARG VITE_API_BASE_URL=https://aum.lol
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

EXPOSE 3000 5001

# ── Start: gunicorn (backend) + static file server (frontend) ─────────
CMD ["sh", "-c", \
  "cd backend && uv run gunicorn 'app:create_app()' \
     --bind 0.0.0.0:5001 \
     --workers 2 \
     --timeout 600 \
     --worker-class sync \
     & npx --yes serve frontend/dist --listen tcp://0.0.0.0:3000 --no-clipboard"]
