#!/usr/bin/env bash
set -euo pipefail

SERVER="68.183.142.183"
SSH_KEY="$HOME/.ssh/2026_do"
REMOTE_DIR="/opt/seabw-2026"
REPO="https://github.com/kon-rad/seabw-hackathon-2026.git"
BRANCH="${1:-main}"

SSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=no root@$SERVER"

echo "==> Deploying branch '$BRANCH' to $SERVER"

$SSH bash -s << EOF
set -euo pipefail

# Install Docker + Compose if not present
if ! command -v docker &>/dev/null; then
  echo "--- Installing Docker"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi

if ! docker compose version &>/dev/null; then
  echo "--- Installing Docker Compose plugin"
  apt-get install -y docker-compose-plugin
fi

# Clone or pull repo
if [ -d "$REMOTE_DIR/.git" ]; then
  echo "--- Pulling latest"
  git -C "$REMOTE_DIR" fetch origin
  git -C "$REMOTE_DIR" checkout "$BRANCH"
  git -C "$REMOTE_DIR" reset --hard "origin/$BRANCH"
else
  echo "--- Cloning repo"
  git clone --branch "$BRANCH" "$REPO" "$REMOTE_DIR"
fi

# Ensure .env exists
if [ ! -f "$REMOTE_DIR/.env" ]; then
  echo "WARNING: $REMOTE_DIR/.env not found — creating empty one. Fill it in before the containers will work."
  touch "$REMOTE_DIR/.env"
fi

cd "$REMOTE_DIR"

echo "--- Building and starting containers"
docker compose pull --ignore-pull-failures || true
docker compose build --no-cache
docker compose up -d --remove-orphans

echo "--- Container status"
docker compose ps

echo "==> Deploy complete. App at http://$SERVER"
EOF
