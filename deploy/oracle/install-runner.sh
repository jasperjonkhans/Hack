#!/usr/bin/env bash
# Register this VM as the repo's self-hosted Actions runner (label: oracle),
# running as a service under the current user.
# Usage: ./install-runner.sh <registration-token>
# The repo owner gets the token from Settings → Actions → Runners → New self-hosted runner.
set -euo pipefail

TOKEN=${1:?usage: $0 <registration-token>}
REPO_URL=${REPO_URL:-https://github.com/jasperjonkhans/Hack}
RUNNER_DIR=${RUNNER_DIR:-/opt/actions-runner}

version=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | sed -n 's/.*"tag_name": "v\([^"]*\)".*/\1/p')
case $(uname -m) in
  aarch64) arch=arm64 ;;
  x86_64) arch=x64 ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac
tarball="actions-runner-linux-$arch-$version.tar.gz"

sudo mkdir -p "$RUNNER_DIR"
sudo chown "$USER:$USER" "$RUNNER_DIR"
cd "$RUNNER_DIR"
curl -fsSL -o "$tarball" "https://github.com/actions/runner/releases/download/v$version/$tarball"
tar -xzf "$tarball"
rm "$tarball"

./config.sh --unattended --replace --url "$REPO_URL" --token "$TOKEN" --name "$(hostname)" --labels oracle
sudo ./svc.sh install "$USER"
sudo ./svc.sh start
