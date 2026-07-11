# CI Linux Box Setup

Self-hosted GitHub Actions runner for anime-lakehouse. ~10 minutes from bare
box to green runs.

## Prerequisites

- Ubuntu 22.04+ or Debian 12+ (x86_64)
- A non-root user with sudo (e.g. `runner`)
- Network access to github.com and pypi.org

## 1. System dependencies

```bash
sudo apt update && sudo apt install -y \
    git curl build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev libffi-dev \
    python3.12 python3.12-venv python3.12-dev

# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc  # or re-login to pick up ~/.local/bin
```

Node.js is NOT required.

## 2. Clone the repo

```bash
cd ~
git clone https://github.com/jonathanpopham/anime-lakehouse.git
cd anime-lakehouse
```

## 3. Register the runner

Get a registration token from:
**Settings → Actions → Runners → New self-hosted runner** on the GitHub repo.

```bash
export RUNNER_URL="https://github.com/jonathanpopham/anime-lakehouse"
export RUNNER_TOKEN="<paste-token-here>"

ci/setup-runner.sh
```

The script will:
1. Download the actions-runner tarball (if not already present)
2. Run `config.sh` to register with GitHub
3. Install a systemd service (`actions-runner.service`)
4. Start the service

To verify:
```bash
systemctl status actions-runner.service
```

## 4. Verify a run

Push to main or open a PR — the workflow fires automatically.

Or trigger manually from the Actions tab: **CI Gate Lattice → Run workflow**.

## 5. Watch from labboard (optional)

If the labboard agent is running on the same box:

```bash
cd ~/anime-lakehouse
python labboard/server.py &
```

From your local machine, SSH tunnel:
```bash
ssh -L 8377:127.0.0.1:8377 runner@<box-ip>
```

Then open http://127.0.0.1:8377 in your browser.

## 6. Re-running setup

`ci/setup-runner.sh` is idempotent — safe to re-run after updates:
- If the runner binary exists, download is skipped
- If `.credentials` exists, config is skipped
- The systemd service is always restarted

To update the runner version:
```bash
rm -rf ~/actions-runner
export RUNNER_VERSION="2.322.0"  # or whatever's current
ci/setup-runner.sh
```

## 7. Security note

**Do not make this repo public while the runner is active on a personal box.**
Self-hosted runners on public repos execute code from any PR — an attacker can
run arbitrary commands on your machine. Keep the repo private until you've
reviewed the Actions hardening guide:
https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners#self-hosted-runner-security

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Runner offline in GitHub UI | `sudo systemctl restart actions-runner.service` |
| `uv: command not found` in CI | Ensure `~/.local/bin` is in PATH for the runner user |
| dbt fails to find warehouse | The workflow runs `ingest + simulate` first; check AniList is reachable |
| Permission denied on gate.sh | `chmod +x ci/gate.sh` |
