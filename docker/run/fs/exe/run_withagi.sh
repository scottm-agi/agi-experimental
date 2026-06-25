#!/bin/bash
# STABILITY_MARKER_V2

# =============================================================================
# AGIX Startup Script
# Handles brownfield upgrades (git fetch/pull) gracefully
# =============================================================================

# Log version for traceability
set -x
export PYTHONUNBUFFERED=1
export GIT_ISOLATION_BYPASS=5ff78804-system-init

echo "=== AGIX Starting ==="
echo "Git commit: $(cd /git/agix && git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
echo "Timestamp: $(date -Iseconds)"
echo "DEBUG: PATH: $PATH"
echo "DEBUG: PYTHONPATH: $PYTHONPATH"
echo "DEBUG: Contents of /opt:"
ls -la /opt || echo "/opt not found"
echo "DEBUG: Contents of $VENV_DIR (if set):"
ls -la $VENV_DIR || echo "VENV_DIR not found"

. "/ins/setup_venv.sh" "$@"
. "/ins/copy_agix.sh" "$@"
# Explicitly disable exit-on-error to ensure we reach run_ui.py
set +e

# =============================================================================
# Suppress third-party library warnings (sentence-transformers, etc.)
# These are NOT bugs in our code - they're from pip dependencies
# =============================================================================
export PYTHONWARNINGS="ignore::SyntaxWarning"

# =============================================================================
# MISE Environment Activation
# This MUST happen before any Python execution so tools like ast-grep, ruff, rg
# are available to the agent via mise shims.
# =============================================================================
if command -v mise &> /dev/null; then
    echo "Activating MISE environment..."
    # Add mise shims to PATH (these contain ast-grep, ruff, etc.)
    export PATH="$HOME/.local/share/mise/shims:$PATH"
    # Activate mise for bash (sets up env vars, hooks)
    eval "$(mise activate bash)"
    # Verify activation
    if mise doctor 2>/dev/null | grep -q "activated: yes"; then
        echo "✅ MISE activated successfully"
    else
        echo "⚠️ MISE activation may have issues - check 'mise doctor'"
    fi
fi

# Use -W flag to ensure warning suppression at Python level (belt-and-suspenders with PYTHONWARNINGS)
PYTHONPATH=/agix /opt/venv-agix/bin/python3 -W ignore::SyntaxWarning /agix/prepare.py --dockerized=true

# Auto-seed memories and projects from host if available (Asynchronous Merge)
echo "Synchronizing memory and projects from host in background..."
if [ -d "/seed" ]; then
    # Run the smart merge script in the background as a module to fix import issues
    # We use the absolute path to the venv python
    PYTHONPATH=/agix /opt/venv-agix/bin/python3 -m python.helpers.sync_memories > /agix/tmp/sync_memories.log 2>&1 &
fi

export PYTHONPATH=/agix
# Keep mise shims on PATH while adding venv (mise shims should be searchable for tools like ast-grep)
export PATH="/opt/venv-agix/bin:$HOME/.local/share/mise/shims:$PATH"
# Tokens are now managed via environment variables and .env

# Configure git if it hasn't been configured yet
if [ ! -f /root/.gitconfig ]; then
    git config --global user.email "andy@agix.ai"
    git config --global user.name "Andy"
fi

# Stabilize torch/transformers on ARM64
export PYTORCH_ENABLE_MPS_FALLBACK=1
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0
export ACCEL_USE_CPU=true
export TOKENIZERS_PARALLELISM=false

# 97: # Trust mise config to enable MCP server spawning
# 98: # This must happen before run_ui.py starts, as MCP servers use mise exec
# 99: if command -v mise &> /dev/null && [ -f /agix/.mise.toml ]; then
# 100:     echo "Trusting mise configuration at /agix/.mise.toml..."
# 101:     mise trust /agix/.mise.toml 2>/dev/null || true
# 102: fi

# Use PORT from environment if available (Railway provides this), otherwise use WEB_UI_PORT or default to 80
UI_PORT="${PORT:-${WEB_UI_PORT:-80}}"
REDIRECT_PORT="${HTTP_PORT:-0}" # Disable redirect if not explicitly set
HTTPS_UI_PORT="${HTTPS_PORT:-443}"

# =============================================================================
# FINAL STEP: Install Git Shim (OS-Level Isolation Guard)
# This MUST be the last setup step to prevent overwriting by packages.
# =============================================================================
bash /ins/install_git_shim.sh

echo "Starting AGIX on port $UI_PORT..."
exec /opt/venv-agix/bin/python3 -W ignore::SyntaxWarning /agix/run_ui.py \
    --dockerized=true \
    --port="$UI_PORT" \
    --http-port="$REDIRECT_PORT" \
    --https-port="$HTTPS_UI_PORT" \
    --host="0.0.0.0"
