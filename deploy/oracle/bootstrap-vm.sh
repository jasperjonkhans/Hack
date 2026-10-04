#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 VM. Safe to re-run.
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/jasperjonkhans/Hack.git}
REPO_DIR=${REPO_DIR:-/opt/hack}

sudo DEBIAN_FRONTEND=noninteractive apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -yq docker.io docker-compose-v2 git curl openssl bubblewrap
sudo usermod -aG docker "$USER"

# Agent sandboxes (os_env.sandbox: auto) use bubblewrap. Ubuntu 24.04 blocks
# unprivileged user namespaces (kernel.apparmor_restrict_unprivileged_userns),
# so allow them for /usr/bin/bwrap only; without this every sandboxed tool call
# fails with "setting up uid map: Permission denied".
if [[ ! -f /etc/apparmor.d/bwrap ]]; then
  sudo tee /etc/apparmor.d/bwrap >/dev/null <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,

  include if exists <local/bwrap>
}
PROFILE
  sudo apparmor_parser -r /etc/apparmor.d/bwrap
fi

if ! command -v uv >/dev/null; then
  tarball="uv-$(uname -m)-unknown-linux-gnu.tar.gz"
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/$tarball" "https://github.com/astral-sh/uv/releases/latest/download/$tarball"
  curl -fsSL -o "$tmp/$tarball.sha256" "https://github.com/astral-sh/uv/releases/latest/download/$tarball.sha256"
  (cd "$tmp" && sha256sum -c "$tarball.sha256")
  sudo tar -xzf "$tmp/$tarball" -C /usr/local/bin --strip-components=1
  rm -rf "$tmp"
fi

# Omnigent's Claude, Codex and Pi harnesses need Node.js 22.10+.
if ! node --version 2>/dev/null | grep -q '^v2[2-9]\.'; then
  base=https://nodejs.org/dist/latest-v22.x
  case $(uname -m) in
    aarch64) node_arch=arm64 ;;
    x86_64) node_arch=x64 ;;
  esac
  sums=$(curl -fsSL "$base/SHASUMS256.txt")
  node_tarball=$(echo "$sums" | grep -oE "node-v22\.[0-9]+\.[0-9]+-linux-$node_arch\.tar\.xz" | head -1)
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/$node_tarball" "$base/$node_tarball"
  (cd "$tmp" && echo "$sums" | grep " $node_tarball\$" | sha256sum -c -)
  sudo tar -xJf "$tmp/$node_tarball" -C /usr/local --strip-components=1 --exclude='*.md' --exclude=LICENSE
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
