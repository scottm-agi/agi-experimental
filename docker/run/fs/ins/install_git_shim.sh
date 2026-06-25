#!/bin/bash

# ==============================================================================
# Git Shim Installer (HARDENED): Registering OS-Level Guard
# ==============================================================================
# This script renames# 1. Rename real git to a hidden path (not in common PATH)
# and ensure it's not easily guessable.
# CRITICAL: Only move if it's the real binary (large size) to avoid moving shims.
mkdir -p /usr/share/git-agix
GIT_SIZE=$(stat -c%s /usr/bin/git)
if [ "$GIT_SIZE" -gt 1000000 ]; then
    mv /usr/bin/git /usr/share/git-agix/git
    chmod 711 /usr/share/git-agix/git
    echo "[SETUP] Real git binary relocated to hidden path."
else
    echo "[SETUP] /usr/bin/git already seems to be a shim or missing real binary. Skipping move."
fi
# ==============================================================================

SHIM_SOURCE="/ins/git_shim.sh"
REAL_GIT="/usr/bin/git"
REAL_GIT_BACKUP="/usr/bin/git.real"

# 1. Rename real git if not already done
if [ -f "$REAL_GIT" ] && [ ! -f "$REAL_GIT_BACKUP" ]; then
    echo "[SETUP] Hardening Git: Renaming $REAL_GIT to $REAL_GIT_BACKUP..."
    mv "$REAL_GIT" "$REAL_GIT_BACKUP"
fi

# 2. Install shim at BOTH locations for safety (/usr/bin/git and /usr/local/bin/git)
if [ -f "$SHIM_SOURCE" ]; then
    echo "[SETUP] Installing Hardened Git Shim Guard..."
    cp "$SHIM_SOURCE" "$REAL_GIT"
    chmod +x "$REAL_GIT"
    
    # Also mask /usr/local/bin if it exists
    cp "$SHIM_SOURCE" "/usr/local/bin/git"
    chmod +x "/usr/local/bin/git"
    
    echo "[SETUP] Hardened Git Shim Guard installed successfully."
else
    echo "[ERROR] Git Shim source not found at $SHIM_SOURCE"
    exit 1
fi
