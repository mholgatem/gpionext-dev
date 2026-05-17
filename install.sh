#!/bin/bash
# GPIOnext Bootstrap Installer
# Downloads and extracts the requested version of GPIOnext and runs setup.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INSTALL_PATH="/opt/gpionext"
GITHUB_REPO="mholgatem/gpionext-dev"
NONE='\033[00m'
CYAN='\033[36m'
GREEN='\033[32m'
RED='\033[31m'
BOLD='\033[1m'

# ---------------------------------------------------------------------------
# Version Formatting
# ---------------------------------------------------------------------------

VERSION=""
for arg in "$@"; do
    case $arg in
        --version)
            shift
            if [ -n "${1:-}" ]; then
                VERSION="$1"
                shift
            fi
            ;;
    esac
done

if [ -z "$VERSION" ]; then
    echo -e "${CYAN}Determining latest release...${NONE}"
    VERSION=$(curl -sf "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" \
        | grep '"tag_name"' | sed -E 's/.*"([^"]+)".*/\1/') || VERSION=""
    
    if [ -z "$VERSION" ]; then
        echo -e "${RED}Error: Could not determine latest release.${NONE}"
        exit 1
    fi
else
    # Format version: lowercase and prepend 'v' if missing
    # Exception: 'LEGACY' should always be uppercase
    if [[ "${VERSION,,}" == "legacy" ]]; then
        VERSION="LEGACY"
    else
        VERSION="${VERSION,,}"
        if [[ ! "$VERSION" =~ ^v ]]; then
            VERSION="v${VERSION}"
        fi
    fi
fi

echo -e "Target version: ${BOLD}${VERSION}${NONE}"

# ---------------------------------------------------------------------------
# Root check
# ---------------------------------------------------------------------------

if [ "$(whoami)" != "root" ]; then
    echo "Switching to root user..."
    sudo bash "$0" "$@"
    exit $?
fi

# ---------------------------------------------------------------------------
# Fetch and Extract
# ---------------------------------------------------------------------------

echo -e "${CYAN}Creating install directory ${INSTALL_PATH}...${NONE}"
mkdir -p "$INSTALL_PATH"

echo -e "${CYAN}Downloading source tarball for ${VERSION}...${NONE}"
SOURCE_URL="https://github.com/${GITHUB_REPO}/archive/refs/tags/${VERSION}.tar.gz"

if curl -sfL "$SOURCE_URL" -o /tmp/gpionext.tar.gz; then
    echo -e "${CYAN}Extracting to ${INSTALL_PATH}...${NONE}"
    tar -xzf /tmp/gpionext.tar.gz -C "$INSTALL_PATH" --strip-components=1
    rm /tmp/gpionext.tar.gz
else
    echo -e "${RED}Error: Download failed for version ${VERSION}.${NONE}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Hand-off to setup.sh
# ---------------------------------------------------------------------------

if [ -f "${INSTALL_PATH}/setup.sh" ]; then
    echo -e "${GREEN}Handing off to setup.sh...${NONE}"
    bash "${INSTALL_PATH}/setup.sh" "$@"
else
    echo -e "${RED}Error: setup.sh not found in extracted source.${NONE}"
    exit 1
fi
