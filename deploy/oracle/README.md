# Team Omnigent server (Oracle VM)

The shared Omnigent server runs on the Oracle Always Free VM `hack-nation-pg`
(Ubuntu 24.04, ARM, 2 OCPU / 12 GB, `130.61.237.227`), next to the shared
`academic` Postgres. Every merge to `main` deploys automatically.

| Piece | Where | Notes |
| --- | --- | --- |
| Web UI + API | `https://130-61-237-227.sslip.io` | Caddy terminates TLS (Let's Encrypt) |
| Omnigent server | Docker, `ghcr.io/omnigent-ai/omnigent-server:v<uv.lock version>` | Also on `127.0.0.1:8000` for SSH tunnels |
| Omnigent database | Docker `postgres:16`, not exposed | Separate from the `academic` database |
| Team agents | `demo/team`, seeded as the built-in agent `hack-team` | Reloaded on every deploy |
| Agent host | systemd `omnigent-host`, runs from `/opt/hack/.venv` | Executes agents and tools on the VM |
| Deploys | GitHub Actions self-hosted runner on the VM (label `oracle`) | `.github/workflows/deploy.yml` |

The Omnigent version comes from `uv.lock`, so bumping `omnigent` in a PR
upgrades the server and the host together.

## First-time setup

On the VM, as `ubuntu`:

```bash
git clone https://github.com/jasperjonkhans/Hack.git /opt/hack
/opt/hack/deploy/oracle/bootstrap-vm.sh
/opt/hack/deploy/oracle/deploy.sh
```

`bootstrap-vm.sh` installs Docker and uv and writes `deploy/oracle/.env` with
generated secrets. It never overwrites an existing `.env`.

### Create the admin before exposing the UI

Until an admin exists, whoever opens the UI first can create one. Create it
through an SSH tunnel while ports 80/443 are still closed:

```bash
ssh -L 8000:127.0.0.1:8000 ubuntu@130.61.237.227
```

Open http://localhost:8000, create the admin, then open TCP 80 and 443 from
`0.0.0.0/0` in the subnet's security list. Caddy gets the certificate within a
minute or two (`docker compose logs caddy` from this directory).

Invite teammates from the account menu → **Members** → **Invite member**.

### Agent host

Hosts belong to one Omnigent account: sessions owned by another user cannot
run on it. The VM host therefore signs in as a shared team account.

```bash
sudo mkdir -p /etc/hack
sudo tee /etc/hack/host.env >/dev/null <<'EOF'
OMNIGENT_URL=https://130-61-237-227.sslip.io
ANTHROPIC_API_KEY=
EOF
sudo chmod 600 /etc/hack/host.env && sudo chown ubuntu /etc/hack/host.env
/opt/hack/.venv/bin/omnigent login https://130-61-237-227.sslip.io
sudo cp /opt/hack/deploy/oracle/omnigent-host.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now omnigent-host
```

Fill in the model key in `/etc/hack/host.env` yourself. Agents running on the
host can read its environment, so keep only what they need in that file.

### Continuous deployment

The repository owner does this once:

1. **Settings → Actions → General → Fork pull request workflows**: require
   approval for all external contributors. The runner executes on the VM, so
   workflows from forks must never start unattended.
2. **Settings → Actions → Runners → New self-hosted runner**: copy the
   registration token (valid for one hour) and run, on the VM:

   ```bash
   /opt/hack/deploy/oracle/install-runner.sh <token>
   ```

After that, every push to `main` runs CI on GitHub-hosted runners and, if it
passes, `deploy.sh` on the VM. The runner only makes outbound connections, so
SSH can stay limited to known IPs.

## Operations

All commands run on the VM from `/opt/hack/deploy/oracle`.

- Deploy logs: the **Deploy** workflow in the Actions tab.
- Manual deploy or re-run: **Actions → Deploy → Run workflow**, or `./deploy.sh`.
- Roll back: revert the commit on `main`; the revert deploys like any other merge.
- Server logs: `docker compose logs -f omnigent`.
- Host logs: `journalctl -u omnigent-host -f`.
- Release features: set `OMNIGENT_FEATURES` in `.env` (for example `canvas`), then `./deploy.sh`.
