#!/usr/bin/env bash
# Bring the VM to a commit of main (DEPLOY_SHA, default origin/main): sync code
# and the uv env, run the Omnigent server image matching uv.lock, restart the
# agent host, check health.
set -euo pipefail

# Flag agent settings missing from the repository .env in the deploy log, by
# name only. Warnings, not failures: the server itself runs without them.
check_env() {
  local file=$1 key value
  for key in ACADEMIC_DB_URL ACADEMIC_DB_READER_URL CONTACT_EMAIL \
             S2_API_KEY OPENALEX_MAILTO OPENALEX_API_KEY; do
    # The last assignment wins, as in academic_db's .env reader; strip quotes and spaces.
    value=$(sed -nE "s/^[[:space:]]*$key[[:space:]]*=(.*)/\1/p" "$file" 2>/dev/null | tail -n 1 | tr -d "\"' \t" || true)
    # Unset: empty, only a comment, or .env.example's CHANGE_ME placeholder.
    if [[ -n $value && $value != \#* && $value != *CHANGE_ME* ]]; then
      continue
    fi
    case $key in
      ACADEMIC_DB_*) echo "::warning::$key is not set in $file: Mimir's database tools fail without it" ;;
      CONTACT_EMAIL) echo "::warning::$key is not set in $file: the Verifier's Unpaywall fallback fails without it" ;;
      *) echo "::warning::$key is not set in $file: optional, see .env.example" ;;
    esac
  done
}

# Wrapped in main so bash has read the whole file before git reset rewrites it.
main() {
  local repo_dir=${REPO_DIR:-/opt/hack}
  cd "$repo_dir"

  if [[ "${SKIP_GIT:-0}" != 1 ]]; then
    git fetch --quiet origin
    git reset --hard --quiet "${DEPLOY_SHA:-origin/main}"
  fi

  uv sync --locked --quiet
  # Agents start academic-db-mcp from their session folder, which is often
  # outside this project; a tool install puts it on PATH (~/.local/bin). The
  # editable install still reads this repository's .env.
  uv tool install --quiet --force --editable tools/academic_db
  check_env "$repo_dir/.env"

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
  # Built-in agents are seeded only at startup, so pick up agent config changes (lab/ is Mimir).
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
