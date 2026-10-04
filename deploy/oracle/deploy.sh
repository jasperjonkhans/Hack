#!/usr/bin/env bash
# Bring the VM to a commit of main (DEPLOY_SHA, default origin/main): sync code
# and the uv env, run the Omnigent server image matching uv.lock, restart the
# agent host, check health.
set -euo pipefail

# Wrapped in main so bash has read the whole file before git reset rewrites it.
main() {
  local repo_dir=${REPO_DIR:-/opt/hack}
  cd "$repo_dir"

  if [[ "${SKIP_GIT:-0}" != 1 ]]; then
    git fetch --quiet origin
    git reset --hard --quiet "${DEPLOY_SHA:-origin/main}"
  fi

  uv sync --locked --quiet

  local version
  version=$(awk '$0 == "name = \"omnigent\"" { getline; gsub(/version = |"/, ""); print; exit }' uv.lock)
  if [[ -z "$version" ]]; then
    echo "could not read the omnigent version from uv.lock" >&2
    exit 1
  fi
  cd deploy/oracle
  # Persist the tag in .env so manual `docker compose` commands resolve the same image.
  if grep -q '^OMNIGENT_IMAGE_TAG=' .env; then
    sed -i "s|^OMNIGENT_IMAGE_TAG=.*|OMNIGENT_IMAGE_TAG=v$version|" .env
  else
    echo "OMNIGENT_IMAGE_TAG=v$version" >> .env
  fi

  docker compose pull --quiet
  docker compose up -d --remove-orphans
  # Built-in agents are seeded only at startup, so pick up demo/team changes.
  docker compose restart omnigent

  if systemctl is-enabled --quiet omnigent-host 2>/dev/null; then
    sudo systemctl restart omnigent-host
  fi

  for _ in $(seq 60); do
    if curl -fsS -o /dev/null http://127.0.0.1:8000/health; then
      echo "healthy: omnigent v$version, $(git -C "$repo_dir" rev-parse --short HEAD)"
      exit 0
    fi
    sleep 2
  done
  echo "server did not become healthy" >&2
  docker compose logs --tail 80 omnigent >&2
  exit 1
}

main "$@"
