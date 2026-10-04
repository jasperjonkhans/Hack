#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 VM. Safe to re-run.
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/jasperjonkhans/Hack.git}
REPO_DIR=${REPO_DIR:-/opt/hack}

sudo DEBIAN_FRONTEND=noninteractive apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -yq docker.io docker-compose-v2 git curl openssl
sudo usermod -aG docker "$USER"

if ! command -v uv >/dev/null; then
  tarball="uv-$(uname -m)-unknown-linux-gnu.tar.gz"
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/$tarball" "https://github.com/astral-sh/uv/releases/latest/download/$tarball"
  curl -fsSL -o "$tmp/$tarball.sha256" "https://github.com/astral-sh/uv/releases/latest/download/$tarball.sha256"
  (cd "$tmp" && sha256sum -c "$tarball.sha256")
  sudo tar -xzf "$tmp/$tarball" -C /usr/local/bin --strip-components=1
  rm -rf "$tmp"
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  sudo mkdir -p "$REPO_DIR"
  sudo chown "$USER:$USER" "$REPO_DIR"
  git clone "$REPO_URL" "$REPO_DIR"
fi

cd "$REPO_DIR/deploy/oracle"
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" .env
  sed -i "s|^OMNIGENT_ACCOUNTS_COOKIE_SECRET=.*|OMNIGENT_ACCOUNTS_COOKIE_SECRET=$(openssl rand -hex 32)|" .env
  if [[ -n "${OMNIGENT_DOMAIN:-}" ]]; then
    sed -i "s|^OMNIGENT_DOMAIN=.*|OMNIGENT_DOMAIN=$OMNIGENT_DOMAIN|" .env
  fi
fi

echo "Bootstrap done. Open a new shell (docker group), then run $REPO_DIR/deploy/oracle/deploy.sh"
