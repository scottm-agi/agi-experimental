#!/bin/bash
# =============================================================================
# Playwright/Patchright Browser Installer
# Ensures Chromium is installed in persistent storage ($PLAYWRIGHT_BROWSERS_PATH)
# =============================================================================
set -e

# activate venv
. "/ins/setup_venv.sh" "$@"

VENV_PYTHON="/opt/venv-agix/bin/python"
VENV_PIP="/opt/venv-agix/bin/pip"
INSTALL_CMD="$VENV_PIP install"

if command -v uv &>/dev/null; then
    INSTALL_CMD="uv pip install --python $VENV_PYTHON"
fi

export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/playwright}"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"

# Remove stale playwright locks if any
rm -rf /tmp/playwright-installer-lock* 2>/dev/null || true

# CRITICAL: Ensure the Python packages are installed BEFORE attempting browser install.
# Previously this script silently skipped browser install if the pip package was missing,
# which was the root cause of "ModuleNotFoundError: No module named 'playwright'" (#915).
echo "Ensuring playwright and patchright Python packages are installed..."
if ! $VENV_PYTHON -c "import playwright" 2>/dev/null; then
    echo "Installing playwright Python package..."
    $INSTALL_CMD --quiet "playwright>=1.52.0" 2>/dev/null || echo "⚠️ playwright pip install failed"
fi
if ! $VENV_PYTHON -c "import patchright" 2>/dev/null; then
    echo "Installing patchright Python package..."
    $INSTALL_CMD --quiet patchright 2>/dev/null || echo "⚠️ patchright pip install failed"
fi

# Function to install browser for a specific module
install_browser() {
    local module=$1
    echo "Installing Chromium browser using $module..."
    if ! $VENV_PYTHON -m $module install chromium > /tmp/${module}_install.log 2>&1; then
        echo "⚠️ $module chromium install failed, trying with --with-deps fallback..."
        $VENV_PYTHON -m $module install chromium --with-deps >> /tmp/${module}_install.log 2>&1 || echo "❌ $module chromium install failed"
    fi
}

# Install for patchright (used by browser-use)
if $VENV_PYTHON -m patchright --version &>/dev/null; then
    install_browser "patchright"
else
    echo "⚠️ patchright module not available, skipping browser install for patchright"
fi

# Install for playwright (used by crawl4ai and others)
if $VENV_PYTHON -m playwright --version &>/dev/null; then
    install_browser "playwright"
else
    echo "⚠️ playwright module not available, skipping browser install for playwright"
fi

# Verify installation (check for any chromium directory)
if ls "$PLAYWRIGHT_BROWSERS_PATH"/chromium-* &>/dev/null; then
    echo "✅ Chromium binaries verified in $PLAYWRIGHT_BROWSERS_PATH"
else
    echo "❌ Chromium installation FAILED — browser agent delegation will not work"
    echo "   Check /tmp/playwright_install.log and /tmp/patchright_install.log for details"
fi
