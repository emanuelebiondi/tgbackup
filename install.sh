#!/usr/bin/env bash
# ==============================================================================
# TGBackup - Native Installer
# ==============================================================================
# Automatically detects environment and installs TGBackup:
# 1. Arch Linux (yay): Native system package (/usr/bin/tgbackup)
# 2. Arch Linux (makepkg): Native system package (/usr/bin/tgbackup)
# 3. Generic Linux / User mode: Isolated virtualenv (~/.local/lib/tgbackup)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================================="
echo "               TGBackup Installation Script                      "
echo "================================================================="

if command -v yay >/dev/null 2>&1; then
    echo "==> Arch Linux detected with 'yay' AUR helper."
    echo "==> Building and installing system package via PKGBUILD..."
    yay -Bi .
elif command -v makepkg >/dev/null 2>&1; then
    echo "==> Arch Linux detected with 'makepkg'."
    echo "==> Building and installing system package..."
    makepkg -si
elif command -v pipx >/dev/null 2>&1; then
    echo "==> pipx detected. Installing isolated system tool..."
    pipx install .
else
    echo "==> Generic Linux: Installing into isolated directory (~/.local/lib/tgbackup)..."
    INSTALL_DIR="$HOME/.local/lib/tgbackup"
    BIN_DIR="$HOME/.local/bin"
    mkdir -p "$INSTALL_DIR" "$BIN_DIR"
    
    python3 -m venv "$INSTALL_DIR/venv"
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    "$INSTALL_DIR/venv/bin/pip" install "$SCRIPT_DIR"
    ln -sf "$INSTALL_DIR/venv/bin/tgbackup" "$BIN_DIR/tgbackup"
    
    echo "==> Linked binary to: $BIN_DIR/tgbackup"
fi

# Set up Omarchy plugin if Omarchy is installed
OMARCHY_DIR="$HOME/.config/omarchy"
if [ -d "$OMARCHY_DIR" ]; then
    PLUGINS_DIR="$OMARCHY_DIR/plugins"
    mkdir -p "$PLUGINS_DIR"
    TARGET="$PLUGINS_DIR/tgbackup"
    
    if [ -d "/usr/share/tgbackup/omarchy-plugin" ]; then
        SOURCE="/usr/share/tgbackup/omarchy-plugin"
    else
        SOURCE="$SCRIPT_DIR/omarchy-plugin"
    fi
    
    if [ ! -e "$TARGET" ] || [ -L "$TARGET" ]; then
        rm -f "$TARGET"
        ln -sf "$SOURCE" "$TARGET"
        echo "==> Omarchy plugin linked to: $TARGET"
    fi
fi

echo ""
echo "================================================================="
echo "TGBackup installed successfully!"
echo "Run 'tgbackup init' to configure your cluster and backups."
echo "================================================================="
