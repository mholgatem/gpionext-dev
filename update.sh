#!/bin/bash
# GPIOnext Update Script
# Pulls latest source, downloads new Rust binary, and restarts the daemon.
#
# Usage: gpionext update
#        or: bash /opt/gpionext/update.sh

set -euo pipefail

if [ "$(whoami)" != "root" ]; then
    sudo bash "$0" "$@"
    exit $?
fi

INSTALL_PATH="/opt/gpionext"
GITHUB_REPO="mholgatem/gpionext-dev"

CYAN='\033[36m'
GREEN='\033[32m'
RED='\033[31m'
NONE='\033[00m'

ARCH=$(uname -m)
case "$ARCH" in
    armv7l)  RUST_ARCH="armv7l"  ;;
    aarch64) RUST_ARCH="aarch64" ;;
    x86_64)  RUST_ARCH="x86_64"  ;;
    *)       echo -e "${RED}Unsupported architecture: $ARCH${NONE}"; exit 1 ;;
esac

echo -e "${CYAN}Updating GPIOnext...${NONE}"

# ---------------------------------------------------------------------------
# Pull latest source
# ---------------------------------------------------------------------------

cd "$INSTALL_PATH"
if [ -d ".git" ]; then
    echo "Pulling latest source..."
    git pull --ff-only
else
    echo -e "${RED}Not a git repo — skipping source update.${NONE}"
    echo "To get source updates, re-run install.sh."
fi

# ---------------------------------------------------------------------------
# Download latest binary
# ---------------------------------------------------------------------------

BINARY_NAME="gpionext_core-${RUST_ARCH}.so"
DEST="${INSTALL_PATH}/${BINARY_NAME}"

LATEST_TAG=$(curl -sf "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" \
    | grep '"tag_name"' | sed -E 's/.*"([^"]+)".*/\1/') || LATEST_TAG=""

if [ -z "$LATEST_TAG" ]; then
    echo -e "${RED}Could not fetch latest release tag. Skipping binary update.${NONE}"
else
    BINARY_URL="https://github.com/${GITHUB_REPO}/releases/download/${LATEST_TAG}/${BINARY_NAME}"
    echo "Downloading $BINARY_URL..."
    if curl -sfL "$BINARY_URL" -o "${DEST}.tmp"; then
        mv "${DEST}.tmp" "$DEST"
        chmod 755 "$DEST"
        ln -sf "$DEST" "${INSTALL_PATH}/gpionext_core.so"
        echo -e "${GREEN}Binary updated to ${LATEST_TAG}.${NONE}"
    else
        echo -e "${RED}Binary download failed — keeping current binary.${NONE}"
        rm -f "${DEST}.tmp"
    fi
fi

# ---------------------------------------------------------------------------
# Reload systemd and restart daemon
# ---------------------------------------------------------------------------

echo "Restarting GPIOnext daemon..."
systemctl daemon-reload
systemctl restart gpionext

echo -e "${GREEN}Update complete.${NONE}"
