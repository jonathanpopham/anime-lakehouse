#!/usr/bin/env bash
set -euo pipefail

# setup-runner.sh — idempotent GitHub Actions self-hosted runner setup.
# Requires: RUNNER_URL and RUNNER_TOKEN env vars.
# Refuses to run as root. Safe to re-run.

if [[ "$(id -u)" -eq 0 ]]; then
    echo "ERROR: Do not run this script as root. Use a dedicated runner user." >&2
    exit 1
fi

RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
RUNNER_VERSION="${RUNNER_VERSION:-2.321.0}"
RUNNER_ARCH="${RUNNER_ARCH:-linux-x64}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,linux}"
RUNNER_NAME="${RUNNER_NAME:-$(hostname)}"

if [[ -z "${RUNNER_URL:-}" ]]; then
    echo "ERROR: RUNNER_URL not set (e.g. https://github.com/youruser/anime-lakehouse)" >&2
    exit 1
fi
if [[ -z "${RUNNER_TOKEN:-}" ]]; then
    echo "ERROR: RUNNER_TOKEN not set (get from Settings > Actions > Runners > New)" >&2
    exit 1
fi

echo "=== GitHub Actions Runner Setup ==="
echo "  dir:     $RUNNER_DIR"
echo "  version: $RUNNER_VERSION"
echo "  url:     $RUNNER_URL"
echo "  name:    $RUNNER_NAME"
echo ""

# --- Download runner if not present ---
if [[ ! -x "${RUNNER_DIR}/run.sh" ]]; then
    echo "Downloading actions-runner ${RUNNER_VERSION}..."
    mkdir -p "$RUNNER_DIR"
    TARBALL="actions-runner-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
    curl -sL "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TARBALL}" \
        -o "/tmp/${TARBALL}"
    tar xzf "/tmp/${TARBALL}" -C "$RUNNER_DIR"
    rm -f "/tmp/${TARBALL}"
    echo "  -> extracted to $RUNNER_DIR"
else
    echo "Runner binary already present, skipping download."
fi

# --- Configure runner if not already configured ---
if [[ ! -f "${RUNNER_DIR}/.credentials" ]]; then
    echo "Configuring runner..."
    "${RUNNER_DIR}/config.sh" \
        --url "$RUNNER_URL" \
        --token "$RUNNER_TOKEN" \
        --name "$RUNNER_NAME" \
        --labels "$RUNNER_LABELS" \
        --unattended \
        --replace
    echo "  -> configured"
else
    echo "Runner already configured, skipping config.sh."
fi

# --- Install systemd service ---
SERVICE_FILE="/etc/systemd/system/actions-runner.service"
if [[ ! -f "$SERVICE_FILE" ]]; then
    echo "Installing systemd service..."
    sudo tee "$SERVICE_FILE" > /dev/null <<UNIT
[Unit]
Description=GitHub Actions Runner
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${RUNNER_DIR}
ExecStart=${RUNNER_DIR}/run.sh
Restart=always
RestartSec=5
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
UNIT
    sudo systemctl daemon-reload
    sudo systemctl enable actions-runner.service
    echo "  -> service installed and enabled"
else
    echo "Systemd service already exists."
fi

# --- Start/restart service ---
sudo systemctl restart actions-runner.service
sleep 2
if systemctl is-active --quiet actions-runner.service; then
    echo ""
    echo "=== Runner is active ==="
    systemctl status actions-runner.service --no-pager | head -5
else
    echo ""
    echo "WARNING: Service failed to start. Check: journalctl -u actions-runner.service"
    exit 1
fi
