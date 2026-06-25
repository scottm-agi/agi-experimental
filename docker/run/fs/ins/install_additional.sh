#!/bin/bash
# STABILITY_MARKER_V3 — Verify-First Pattern
# =============================================================================
# AGIX Additional Setup Script (Boot Optimization)
#
# PHILOSOPHY: Since we build our own Docker image, everything should already
# be baked in. This script is a SAFETY NET that:
#   1. Verifies each component is present
#   2. Installs ONLY what's missing
#   3. Skips anything already working
#
# Expected runtime:
#   - Normal boot (everything baked in): ~3-5s (verify-only)
#   - Restart (marker exists):           ~1-2s (fast-path)
#   - Recovery (something missing):      ~30-60s (install missing only)
# =============================================================================
set -e

MARKER_FILE="/var/agix/.setup_complete"

# Create symlink for virtual environment
if [ -d "/opt/venv-agix" ] && [ ! -e "/opt/venv-agix" ]; then
    ln -s /opt/venv-agix /opt/venv-agix
    echo "✅ Created /opt/venv-agix -> /opt/venv-agix symlink"
fi

# Function to check if setup was already done
setup_already_done() {
    # On Railway, use persistent storage for marker to survive redeploys/restarts
    if [ -n "$RAILWAY_ENVIRONMENT" ]; then
        MARKER_FILE="/agix/data/.setup_complete"
    fi

    if [ -f "$MARKER_FILE" ]; then
        echo "AGIX setup already completed (marker exists: $MARKER_FILE)"
        return 0
    fi
    return 1
}

# Function to verify critical CLI tools are present
cli_tools_present() {
    if ! command -v rg &>/dev/null; then
        echo "⚠️ ripgrep (rg) missing"
        return 1
    fi
    if ! command -v jq &>/dev/null; then
        echo "⚠️ jq missing"
        return 1
    fi
    if ! command -v gh &>/dev/null; then
        echo "⚠️ gh CLI missing"
        return 1
    fi
    return 0
}

# Skip if already set up AND all tools are present
if setup_already_done; then
    # Quick check that critical components are available
    # 1. Python packages
    # 2. CLI tools
    # 3. Playwright browsers (if on Railway)
    # Fast core check (0.5s) — includes ALL critical imports that cause crashes if missing
    CORE_PYTHON_OK=$(/opt/venv-agix/bin/python -c "import aiosqlite, redis, googleapiclient, google_auth_oauthlib, asyncpg, tiktoken, pathspec, gitingest; print('OK')" 2>/dev/null || echo "MISSING")
    
    # Optional slow ML check (only if core is OK)
    ML_PYTHON_OK="OK"
    if [ "$CORE_PYTHON_OK" = "OK" ]; then
        ML_PYTHON_OK=$(/opt/venv-agix/bin/python -c "import sentence_transformers; print('OK')" 2>/dev/null || echo "MISSING")
    fi
    
    PLAYWRIGHT_OK=true
    if [ -n "$RAILWAY_ENVIRONMENT" ]; then
        if ! ls /opt/playwright/chromium-* &>/dev/null; then
            echo "⚠️ Playwright Chromium binaries missing from /opt/playwright"
            PLAYWRIGHT_OK=false
        fi
    fi

    # Even on fast-path, check if requirements.txt changed (new deps added)
    REQUIREMENTS_HASH_FILE_FP="/var/agix/.requirements_hash"
    VENV_PIP_FP="/opt/venv-agix/bin/pip"
    INSTALL_CMD_FP="$VENV_PIP_FP install"
    command -v uv &>/dev/null && INSTALL_CMD_FP="uv pip install --python /opt/venv-agix/bin/python"
    if [ -f "/agix/requirements.txt" ]; then
        CURRENT_HASH_FP=$(md5sum /agix/requirements.txt 2>/dev/null | cut -d' ' -f1 || echo "")
        STORED_HASH_FP=$(cat "$REQUIREMENTS_HASH_FILE_FP" 2>/dev/null || echo "")
        if [ -n "$CURRENT_HASH_FP" ] && [ "$CURRENT_HASH_FP" != "$STORED_HASH_FP" ]; then
            echo "📦 Fast-path: requirements.txt changed, syncing new deps..."
            mkdir -p "$(dirname "$REQUIREMENTS_HASH_FILE_FP")"
            $INSTALL_CMD_FP --quiet -r /agix/requirements.txt 2>/dev/null || true
            echo "$CURRENT_HASH_FP" > "$REQUIREMENTS_HASH_FILE_FP"
            # Re-check core imports after sync
            CORE_PYTHON_OK=$(/opt/venv-agix/bin/python -c "import aiosqlite, redis, googleapiclient, google_auth_oauthlib, asyncpg, tiktoken, pathspec, gitingest; print('OK')" 2>/dev/null || echo "MISSING")
        fi
    fi

    if [ "$CORE_PYTHON_OK" = "OK" ] && [ "$ML_PYTHON_OK" = "OK" ] && cli_tools_present && [ "$PLAYWRIGHT_OK" = true ]; then
        # Verify Redis TCP connectivity (mirrors get_redis_config() resolution chain)
        # Priority: REDIS_URL → REDISHOST/REDISPORT (Railway) → REDIS_HOST/REDIS_PORT → defaults
        REDIS_PING_FP=$(/opt/venv-agix/bin/python -c "
import os, socket
from urllib.parse import urlparse
host, port = 'localhost', 6379
redis_url = os.getenv('REDIS_URL', '')
if redis_url:
    p = urlparse(redis_url)
    host = p.hostname or 'localhost'
    port = p.port or 6379
else:
    host = os.getenv('REDISHOST') or os.getenv('REDIS_HOST') or 'redis'
    port = int(os.getenv('REDISPORT') or os.getenv('REDIS_PORT') or 6379)
try:
    s = socket.create_connection((host, port), timeout=3)
    s.send(b'PING
')
    resp = s.recv(64).decode().strip()
    s.close()
    print(f'PONG:{host}:{port}' if 'PONG' in resp else f'FAIL:{host}:{port}')
except Exception as e:
    print(f'FAIL:{host}:{port}:{e}')
" 2>/dev/null || echo "FAIL")
        if [[ "$REDIS_PING_FP" == PONG:* ]]; then
            echo "✅ Redis connectivity verified (${REDIS_PING_FP#PONG:})"
        else
            echo "⚠️ Redis not reachable (${REDIS_PING_FP}). MemoryBankCache will use disk fallback."
        fi
        echo "Critical packages, CLI tools, and browser binaries verified present."
        exit 0
    else
        echo "Marker exists but components missing - re-running setup..."
        rm -f "$MARKER_FILE"
    fi
fi

# Locking mechanism to prevent parallel execution
LOCKDIR="/tmp/install_additional.lock"

# Clean stale lock from previous container kill.
# Railway sends SIGKILL on restart, so the EXIT trap never fires.
# /tmp is persistent on Railway (symlinked to /agix/data/tmp),
# so stale locks survive across deploys.
if [ -d "$LOCKDIR" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCKDIR" 2>/dev/null || echo "0") ))
    if [ "$LOCK_AGE" -gt 600 ]; then
        echo "Removing stale lock (${LOCK_AGE}s old, likely from killed container)..."
        rm -rf "$LOCKDIR"
    fi
fi

if ! mkdir "$LOCKDIR" 2>/dev/null; then
    echo "Another instance of install_additional.sh is running, waiting for it to finish..."
    # Wait for the other process to finish (max 120 seconds)
    for i in {1..60}; do
        if [ ! -d "$LOCKDIR" ]; then break; fi
        sleep 2
    done
    # Re-check if setup was finished by other instance
    if setup_already_done; then
        echo "Setup completed by another process, exiting."
        exit 0
    fi
    # Try to acquire lock again after wait
    if ! mkdir "$LOCKDIR" 2>/dev/null; then
        echo "Lock still held after 120s, exiting to avoid duplicate work."
        exit 0
    fi
fi
trap 'rm -rf "$LOCKDIR"' EXIT

echo "=== AGIX Additional Setup Starting (Verify-First Mode) ==="

VENV_PYTHON="/opt/venv-agix/bin/python"
VENV_PIP="/opt/venv-agix/bin/pip"
INSTALL_CMD="$VENV_PIP install"

if command -v uv &>/dev/null; then
    INSTALL_CMD="uv pip install --python $VENV_PYTHON"
    echo "✅ Using 'uv' for faster installation"
fi

# Track if apt-get update has been run (lazy — only when needed)
APT_UPDATED=false
ensure_apt_updated() {
    if [ "$APT_UPDATED" = false ]; then
        echo "Running apt-get update (first missing package detected)..."
        for i in {1..3}; do
            if apt-get update -qq; then APT_UPDATED=true; return 0; fi
            echo "apt-get update failed, retrying in 5s..."
            sleep 5
        done
        return 1
    fi
}

# =============================================================================
# 1. CLI TOOLS — Verify first, install only what's missing
# =============================================================================
echo "--- Verifying CLI tools ---"
CLI_TOOLS_NEEDED=()

command -v rg &>/dev/null       && echo "✅ ripgrep OK"   || CLI_TOOLS_NEEDED+=("ripgrep")
command -v tree &>/dev/null     && echo "✅ tree OK"      || CLI_TOOLS_NEEDED+=("tree")
command -v jq &>/dev/null       && echo "✅ jq OK"        || CLI_TOOLS_NEEDED+=("jq")
command -v fdfind &>/dev/null   && echo "✅ fd-find OK"   || CLI_TOOLS_NEEDED+=("fd-find")
command -v batcat &>/dev/null   && echo "✅ bat OK"       || CLI_TOOLS_NEEDED+=("bat")
command -v gosu &>/dev/null     && echo "✅ gosu OK"      || CLI_TOOLS_NEEDED+=("gosu")
command -v rsync &>/dev/null    && echo "✅ rsync OK"     || CLI_TOOLS_NEEDED+=("rsync")
command -v lsof &>/dev/null     && echo "✅ lsof OK"      || CLI_TOOLS_NEEDED+=("lsof")
command -v sqlite3 &>/dev/null  && echo "✅ sqlite3 OK"   || CLI_TOOLS_NEEDED+=("sqlite3")
command -v ncdu &>/dev/null     && echo "✅ ncdu OK"      || CLI_TOOLS_NEEDED+=("ncdu")
command -v zstd &>/dev/null     && echo "✅ zstd OK"      || CLI_TOOLS_NEEDED+=("zstd")
# libpq-dev is a build dep (no binary to check), check via dpkg
dpkg -s libpq-dev &>/dev/null   && echo "✅ libpq-dev OK" || CLI_TOOLS_NEEDED+=("libpq-dev")

if [ ${#CLI_TOOLS_NEEDED[@]} -eq 0 ]; then
    echo "All CLI tools present — skipping apt-get install"
else
    echo "⚠️ Missing CLI tools: ${CLI_TOOLS_NEEDED[*]}"
    ensure_apt_updated
    apt-get install -y --no-install-recommends "${CLI_TOOLS_NEEDED[@]}" 2>/dev/null || echo "Warning: Some CLI tools failed to install"
fi

# Create symlinks for common tool names (idempotent, fast)
ln -sf /usr/bin/fdfind /usr/local/bin/fd 2>/dev/null || true
ln -sf /usr/bin/batcat /usr/local/bin/bat 2>/dev/null || true

# GitHub CLI (gh) — separate from apt because it needs its own repo
echo "--- Verifying GitHub CLI ---"
if command -v gh &>/dev/null; then
    echo "✅ gh CLI OK ($(gh --version 2>/dev/null | head -1 || echo 'version unknown'))"
else
    echo "⚠️ gh CLI missing, installing..."
    ensure_apt_updated
    # gh is not in default Debian repos; install from GitHub's official deb
    (type -p wget >/dev/null || apt-get install -y wget) \
        && mkdir -p -m 755 /etc/apt/keyrings \
        && wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
        && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
        && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
        && apt-get update -qq \
        && apt-get install -y gh 2>/dev/null \
        && echo "✅ gh CLI installed" \
        || echo "⚠️ gh CLI install failed (non-critical)"
fi

# =============================================================================
# 2. PLAYWRIGHT BROWSER DEPS — Verify first (same packages as Dockerfile L6-12)
# =============================================================================
echo "--- Verifying Playwright browser library deps ---"
BROWSER_DEPS_NEEDED=()

# Check a representative subset of the browser shared libs
dpkg -s libnss3 &>/dev/null          && echo "✅ libnss3 OK"      || BROWSER_DEPS_NEEDED+=("libnss3")
dpkg -s libatk1.0-0t64 &>/dev/null   && echo "✅ libatk OK"       || BROWSER_DEPS_NEEDED+=("libatk1.0-0t64")
dpkg -s libgbm1 &>/dev/null          && echo "✅ libgbm1 OK"      || BROWSER_DEPS_NEEDED+=("libgbm1")
dpkg -s libpango-1.0-0 &>/dev/null   && echo "✅ libpango OK"     || BROWSER_DEPS_NEEDED+=("libpango-1.0-0")
dpkg -s fonts-ubuntu &>/dev/null      && echo "✅ fonts-ubuntu OK" || BROWSER_DEPS_NEEDED+=("fonts-ubuntu")

if [ ${#BROWSER_DEPS_NEEDED[@]} -eq 0 ]; then
    echo "All browser deps present — skipping apt-get install"
else
    echo "⚠️ Missing browser deps: ${BROWSER_DEPS_NEEDED[*]}"
    ensure_apt_updated
    apt-get install -y --no-install-recommends \
        fonts-unifont libasound2t64 libnss3 libatk1.0-0t64 libatk-bridge2.0-0t64 \
        libcups2t64 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxext6 \
        libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 fonts-ubuntu \
        2>/dev/null || true
fi

# =============================================================================
# 3. MISE — Verify first
# =============================================================================
echo "--- Verifying MISE ---"
if command -v mise &> /dev/null; then
    echo "✅ MISE already installed"
else
    echo "⚠️ MISE missing, installing..."
    curl https://mise.run | MISE_INSTALL_PATH=/usr/local/bin/mise sh
    echo 'eval "$(/usr/local/bin/mise activate bash)"' >> /etc/bash.bashrc
fi

# =============================================================================
# 4. MCP SERVERS — Verify binaries, install only if missing
# =============================================================================
echo "--- Verifying MCP servers ---"
MCP_BINS_NEEDED=0

command -v mcp-server-github &>/dev/null              && echo "✅ mcp-server-github OK"              || { echo "⚠️ mcp-server-github missing"; MCP_BINS_NEEDED=1; }
command -v context7-mcp &>/dev/null                    && echo "✅ context7-mcp OK"                    || { echo "⚠️ context7-mcp missing"; MCP_BINS_NEEDED=1; }
command -v perplexity-mcp &>/dev/null                  && echo "✅ perplexity-mcp OK"                  || { echo "⚠️ perplexity-mcp missing"; MCP_BINS_NEEDED=1; }
command -v mcp-server-sequential-thinking &>/dev/null  && echo "✅ mcp-server-sequential-thinking OK"  || { echo "⚠️ mcp-server-sequential-thinking missing"; MCP_BINS_NEEDED=1; }
command -v google-drive-mcp &>/dev/null                && echo "✅ google-drive-mcp OK"                || { echo "⚠️ google-drive-mcp missing"; MCP_BINS_NEEDED=1; }
command -v tavily-mcp &>/dev/null                      && echo "✅ tavily-mcp OK"                      || { echo "⚠️ tavily-mcp missing"; MCP_BINS_NEEDED=1; }

if [ "$MCP_BINS_NEEDED" -eq 0 ]; then
    echo "All MCP server binaries present — skipping npm install"
else
    echo "⚠️ Some MCP servers missing, installing..."
    MCP_DIR=""
    if [ -f "/agix/mcps/package.json" ]; then
        MCP_DIR="/agix/mcps"
    elif [ -f "/git/agix/mcps/package.json" ]; then
        MCP_DIR="/git/agix/mcps"
    elif [ -f "mcps/package.json" ]; then
        MCP_DIR="mcps"
    fi

    if [ -n "$MCP_DIR" ]; then
        echo "Installing MCP servers from $MCP_DIR/package.json..."
        cd "$MCP_DIR"
        npm install -g $(jq -r '.dependencies | to_entries | .[] | "\(.key)@\(.value)"' package.json) 2>/dev/null || echo "⚠️ Some MCP servers failed to install globally"
    else
        echo "⚠️ mcps/package.json not found, using manual list..."
        npm install -g \
            @modelcontextprotocol/server-github@latest \
            @upstash/context7-mcp@latest \
            @perplexity-ai/mcp-server@latest \
            @modelcontextprotocol/server-sequential-thinking@latest \
            @piotr-agier/google-drive-mcp@latest \
            tavily-mcp@latest \
            2>/dev/null || echo "⚠️ Some MCP servers failed to install globally"
    fi
fi

# =============================================================================
# 5. SANDBOX PYTHON PACKAGES (MISE Python) — Verify first
# =============================================================================
echo "--- Verifying sandbox Python packages ---"
if command -v mise &> /dev/null; then
    if mise exec -- python3 -c "import requests; print(f'✅ requests {requests.__version__} in MISE Python')" 2>/dev/null; then
        echo "✅ Sandbox packages already present"
    else
        echo "⚠️ Sandbox packages missing, installing..."
        SANDBOX_PKGS="requests beautifulsoup4 lxml"
        mise exec -- pip install --quiet $SANDBOX_PKGS 2>/dev/null || echo "⚠️ MISE Python sandbox install failed"
    fi
else
    # Fallback: try system pip if MISE not available
    if ! /usr/bin/python3 -c "import requests" 2>/dev/null; then
        /usr/bin/python3 -m pip install --quiet --break-system-packages requests beautifulsoup4 lxml 2>/dev/null || echo "⚠️ System Python sandbox install failed"
    fi
fi

# =============================================================================
# 6. MISE TOOLS (ast-grep, ruff, bandit) — Verify first
# =============================================================================
echo "--- Verifying MISE tools ---"
if command -v mise &> /dev/null; then
    MISE_TOOLS_NEEDED=0
    command -v ast-grep &>/dev/null && echo "✅ ast-grep OK" || { echo "⚠️ ast-grep missing"; MISE_TOOLS_NEEDED=1; }
    command -v ruff &>/dev/null     && echo "✅ ruff OK"     || { echo "⚠️ ruff missing"; MISE_TOOLS_NEEDED=1; }
    command -v bandit &>/dev/null   && echo "✅ bandit OK"   || { echo "⚠️ bandit missing"; MISE_TOOLS_NEEDED=1; }

    if [ "$MISE_TOOLS_NEEDED" -eq 0 ]; then
        echo "All MISE tools present — skipping install"
    else
        echo "Installing missing MISE tools..."
        cd /agix
        mise trust 2>/dev/null || true
        mise settings set trusted_config_paths /agix/usr/projects:/agix 2>/dev/null || true
        mise use --global ruff@latest bandit@latest ast-grep@latest 2>/dev/null || true
        mise install -y 2>/dev/null || true
    fi

    # Ensure /usr/local/bin symlinks exist (idempotent, fast)
    for tool in ast-grep ruff bandit; do
        if [ ! -L "/usr/local/bin/$tool" ] || [ ! -f "/usr/local/bin/$tool" ]; then
            TOOL_PATH=$(mise which $tool 2>/dev/null || true)
            if [ -z "$TOOL_PATH" ] || [ ! -f "$TOOL_PATH" ]; then
                TOOL_PATH=$(find /root/.local/share/mise/installs/$tool -name "$tool" -type f 2>/dev/null | head -1 || true)
            fi
            if [ -n "$TOOL_PATH" ] && [ -f "$TOOL_PATH" ]; then
                ln -sf "$TOOL_PATH" /usr/local/bin/$tool
                echo "  ✅ $tool -> $TOOL_PATH"
            fi
        fi
    done
else
    echo "MISE not found, installing tools via pip..."
    $INSTALL_CMD --quiet ast-grep ruff bandit 2>/dev/null || true
fi

# =============================================================================
# 7. GritQL CLI — Verify first (already guarded)
# =============================================================================
echo "--- Verifying GritQL CLI ---"
if command -v grit &>/dev/null; then
    echo "✅ grit CLI OK ($(grit --version 2>/dev/null || echo 'version unknown'))"
else
    echo "⚠️ grit missing, installing..."
    npm install -g @getgrit/cli 2>&1 || {
        echo "⚠️ npm global install failed, trying npx approach..."
        npx -y @getgrit/cli --version 2>/dev/null || echo "⚠️ npx fallback also failed"
    }
fi

# Add environment variables for MCP servers
if [ -f "/agix/.env" ]; then
    export GITHUB_TOKEN=$(grep GITHUB_TOKEN /agix/.env | cut -d= -f2- | tr -d '"' | tr -d "'")
    export FORGEJO_TOKEN=$(grep FORGEJO_TOKEN /agix/.env | cut -d= -f2- | tr -d '"' | tr -d "'")
fi

# =============================================================================
# 8. PYTHON PACKAGES (gitingest, crawl4ai) — Verify first (already guarded)
# =============================================================================
echo "--- Verifying optional Python packages ---"
if ! $VENV_PYTHON -c "import gitingest" 2>/dev/null; then
    echo "⚠️ gitingest missing, installing..."
    $INSTALL_CMD --quiet gitingest || echo "⚠️ gitingest install failed"
else
    echo "✅ gitingest already installed"
fi

if ! $VENV_PYTHON -c "import crawl4ai" 2>/dev/null; then
    echo "⚠️ crawl4ai missing, installing..."
    $INSTALL_CMD --quiet crawl4ai || echo "⚠️ crawl4ai install failed"
else
    echo "✅ crawl4ai already installed"
fi

# =============================================================================
# 9. PLAYWRIGHT BROWSERS — Verify first
# =============================================================================
echo "--- Verifying Playwright browsers ---"
export PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
if ls "$PLAYWRIGHT_BROWSERS_PATH"/chromium-* &>/dev/null; then
    echo "✅ Chromium binaries present in $PLAYWRIGHT_BROWSERS_PATH"
else
    echo "⚠️ Chromium binaries missing, installing..."
    bash /ins/install_playwright.sh
fi

# =============================================================================
# 10. CORE PYTHON PACKAGES — Verify first, install only if missing
# =============================================================================
echo "--- Verifying core Python packages ---"

# Pin versions early (fast check: only install if wrong version)
echo "Checking critical version pins..."
HF_OK=$($VENV_PYTHON -c "import huggingface_hub; v=huggingface_hub.__version__; parts=v.split('.'); print('OK' if int(parts[0])==0 else 'UPGRADE')" 2>/dev/null || echo "MISSING")
if [ "$HF_OK" != "OK" ]; then
    echo "⚠️ huggingface-hub needs pinning, installing..."
    $INSTALL_CMD --quiet "huggingface-hub>=0.34.0,<1.0" "pydantic>=2.0.0,<2.12.0" || echo "⚠️ Critical pins failed"
else
    echo "✅ huggingface-hub version OK"
fi

# Core UI + Tool deps: check representative imports, install all if any missing
CORE_OK=$($VENV_PYTHON -c "import aiosqlite, redis, nest_asyncio, cryptography, pydantic_settings; print('OK')" 2>/dev/null || echo "MISSING")
TOOL_OK=$($VENV_PYTHON -c "import litellm, openai, aiohttp, git, googleapiclient, google_auth_oauthlib, httpx, asyncpg, requests; print('OK')" 2>/dev/null || echo "MISSING")

if [ "$CORE_OK" = "OK" ] && [ "$TOOL_OK" = "OK" ]; then
    echo "✅ Core Python packages all present — skipping pip install"
else
    echo "⚠️ Some core Python packages missing (core=$CORE_OK, tool=$TOOL_OK), installing..."
    UI_DEPS="aiosqlite redis nest-asyncio cryptography pydantic-settings setuptools"
    # SECURITY: litellm 1.82.7-1.82.8 compromised by TeamPCP supply chain attack (2026-03-24)
    TOOL_DEPS="litellm<=1.82.6 openai patchright browser-use aiohttp GitPython google-api-python-client google-auth-oauthlib google-auth-httplib2 google-genai httpx asyncpg psycopg2-binary requests"
    $INSTALL_CMD --quiet $UI_DEPS $TOOL_DEPS || echo "Warning: venv package installation had minor issues"
fi

# =============================================================================
# 11. SENTENCE-TRANSFORMERS — Verify first (already guarded)
# =============================================================================
echo "--- Verifying sentence-transformers ---"
if $VENV_PYTHON -c "import sentence_transformers; print(f'✅ sentence-transformers v{sentence_transformers.__version__}')" 2>/dev/null; then
    echo "✅ sentence-transformers already working"
else
    echo "⚠️ sentence-transformers not working, installing..."
    # Install torch CPU first (smaller, faster)
    echo "Installing torch CPU version..."
    $VENV_PIP install --quiet torch --index-url https://download.pytorch.org/whl/cpu 2>/dev/null || {
        echo "⚠️ torch CPU install failed, trying default torch..."
        $VENV_PIP install --quiet torch 2>/dev/null || echo "❌ torch install FAILED"
    }
    echo "Installing sentence-transformers (with pinned tokenizers)..."
    # CRITICAL: Pin transformers+tokenizers FIRST to prevent version drift.
    # Without this, sentence-transformers pulls tokenizers==0.23.1 which conflicts
    # with transformers' <=0.23.0 requirement.
    $VENV_PIP install --quiet "transformers>=4.52.0,<5.0" "tokenizers>=0.22.0,<0.23.1" 2>/dev/null || echo "⚠️ transformers/tokenizers pin failed"
    # Install sentence-transformers WITHOUT resolving deps (they're already pinned above)
    $VENV_PIP install --quiet --no-deps "sentence-transformers>=3.0.0" 2>/dev/null || echo "❌ sentence-transformers install FAILED"
    # Install remaining ST transitive deps that --no-deps skipped
    $VENV_PIP install --quiet tqdm scipy scikit-learn Pillow 2>/dev/null || true
    
    # Verify installation
    if $VENV_PYTHON -c "import sentence_transformers; print(f'✅ sentence-transformers v{sentence_transformers.__version__} installed successfully')" 2>/dev/null; then
        echo "✅ sentence-transformers installation verified"
    else
        echo "❌ CRITICAL: sentence-transformers still not working after install!"
        $VENV_PYTHON -c "import sentence_transformers" 2>&1 | head -20 || true
    fi
fi

# =============================================================================
# 12. MISE PYTHON ENVS — Verify first
# =============================================================================
if command -v mise &> /dev/null; then
    echo "--- Verifying MISE Python environments ---"
    mise trust /agix 2>/dev/null || true
    
    # Forgejo/Git Automation / Search / LLM (Condensed check)
    if ! mise exec -- python3 -c "import requests, git" 2>/dev/null; then
        echo "⚠️ MISE Python deps missing, installing..."
        mise use -g python@3.12 2>/dev/null || true
        mise exec -- pip install --quiet GitPython requests pydantic 2>/dev/null || true
    else
        echo "✅ MISE Python envs OK"
    fi
    
    # Upgrade litellm only if version is too old or compromised
    LITELLM_OK=$($VENV_PYTHON -c "import litellm; v=litellm.__version__; parts=v.split('.'); print('OK' if int(parts[1])>=82 and int(parts[2])<=6 else 'UPGRADE')" 2>/dev/null || echo "MISSING")
    if [ "$LITELLM_OK" != "OK" ]; then
        echo "⚠️ litellm needs upgrade, installing..."
        # SECURITY: cap at 1.82.6 — versions 1.82.7-1.82.8 compromised (TeamPCP, 2026-03-24)
        $INSTALL_CMD --upgrade "litellm<=1.82.6" "huggingface-hub<1.0" 2>/dev/null || true
    else
        echo "✅ litellm version OK"
    fi
    
    if [ -f /agix/tests/patch_litellm.py ]; then
        echo "Applying litellm patch..."
        $VENV_PYTHON /agix/tests/patch_litellm.py || echo "⚠️ Patch failed (non-critical)"
    fi
fi

if [ -d "/usr/local/searxng/searx-pyenv" ]; then
    /usr/local/searxng/searx-pyenv/bin/pip install --quiet --upgrade aiohttp 2>/dev/null || true
fi

# =============================================================================
# 13. REQUIREMENTS.TXT SYNC — Hash-based (already guarded)
# =============================================================================
echo "--- Checking requirements.txt ---"
MARKER_DIR="/var/agix"
REQUIREMENTS_HASH_FILE="$MARKER_DIR/.requirements_hash"
mkdir -p "$MARKER_DIR"
if [ -f "/agix/requirements.txt" ]; then
    CURRENT_HASH=$(md5sum /agix/requirements.txt 2>/dev/null | cut -d' ' -f1 || echo "")
    STORED_HASH=$(cat "$REQUIREMENTS_HASH_FILE" 2>/dev/null || echo "")
    
    if [ -n "$CURRENT_HASH" ] && [ "$CURRENT_HASH" != "$STORED_HASH" ]; then
        echo "📦 requirements.txt change detected, syncing..."
        # Sync requirements — DO NOT add numpy<2 constraint (causes scipy ABI mismatch)
        $INSTALL_CMD --quiet -r /agix/requirements.txt "huggingface-hub<1.0" 2>/dev/null || true
        # Force-reinstall scipy+scikit-learn AFTER requirements sync.
        # requirements.txt transitive deps can pull in numpy versions that break
        # scipy's ABI (ValueError in _multiufuncs). This restores compatibility.
        echo "Restoring scipy ABI compatibility after requirements sync..."
        $VENV_PIP install --force-reinstall --no-cache-dir scipy scikit-learn 2>/dev/null || true
        echo "$CURRENT_HASH" > "$REQUIREMENTS_HASH_FILE"
    else
        echo "✅ requirements.txt unchanged — skipping sync"
    fi
fi

# =============================================================================
# 14. FINAL VERIFICATION (fast — just import checks, no installs)
# =============================================================================
echo "--- Final verification ---"
$VENV_PYTHON -c "import huggingface_hub; print(f'✅ huggingface-hub: {huggingface_hub.__version__}')" 2>/dev/null || echo "❌ huggingface-hub verification failed"
$VENV_PYTHON -c "import numpy; print(f'✅ numpy: {numpy.__version__}')" 2>/dev/null || echo "❌ numpy verification failed"
$VENV_PYTHON -c "import tiktoken; print(f'✅ tiktoken: {tiktoken.__version__}')" 2>/dev/null || echo "❌ tiktoken verification failed"
$VENV_PYTHON -c "import pytest; print(f'✅ pytest: {pytest.__version__}')" 2>/dev/null || { echo "⚠️ pytest missing — installing..."; $VENV_PIP install pytest >/dev/null 2>&1 && echo "✅ pytest installed"; }

# Verify sentence_transformers runtime stability
if ! $VENV_PYTHON -c "import sentence_transformers; from sentence_transformers import SentenceTransformer; print('✅ ST Runtime OK')" 2>/dev/null; then
    echo "❌ CRITICAL: sentence-transformers failed runtime verification!"
    $VENV_PYTHON -c "import sentence_transformers" 2>&1 | tail -5 || true
fi

# Google API verification
if $VENV_PYTHON -c "import googleapiclient, google_auth_oauthlib; print('✅ Google APIs OK')" 2>/dev/null; then
    echo "✅ Google API dependencies verified"
else
    echo "❌ CRITICAL: Google API dependencies missing after install!"
    $VENV_PYTHON -c "import googleapiclient" 2>&1 | head -20 || true
fi

# Redis connectivity verification (import + TCP ping + self-heal)
echo "--- Verifying Redis connectivity ---"
REDIS_IMPORT_OK=$($VENV_PYTHON -c "import redis; print('OK')" 2>/dev/null || echo "MISSING")
if [ "$REDIS_IMPORT_OK" != "OK" ]; then
    echo "⚠️ Redis Python package missing — self-healing..."
    $INSTALL_CMD --quiet "redis>=5.0.0" 2>/dev/null || echo "❌ redis pip install failed"
    REDIS_IMPORT_OK=$($VENV_PYTHON -c "import redis; print('OK')" 2>/dev/null || echo "MISSING")
fi
if [ "$REDIS_IMPORT_OK" = "OK" ]; then
    echo "✅ Redis Python package OK"
    # TCP connectivity check — mirrors get_redis_config() resolution chain
    # Priority: REDIS_URL → REDISHOST/REDISPORT (Railway) → REDIS_HOST/REDIS_PORT → defaults
    REDIS_PING=$($VENV_PYTHON -c "
import os, socket
from urllib.parse import urlparse
host, port = 'localhost', 6379
redis_url = os.getenv('REDIS_URL', '')
if redis_url:
    p = urlparse(redis_url)
    host = p.hostname or 'localhost'
    port = p.port or 6379
else:
    host = os.getenv('REDISHOST') or os.getenv('REDIS_HOST') or 'redis'
    port = int(os.getenv('REDISPORT') or os.getenv('REDIS_PORT') or 6379)
try:
    s = socket.create_connection((host, port), timeout=3)
    s.send(b'PING
')
    resp = s.recv(64).decode().strip()
    s.close()
    print(f'PONG:{host}:{port}' if 'PONG' in resp else f'FAIL:{host}:{port}')
except Exception as e:
    print(f'FAIL:{host}:{port}:{e}')
" 2>/dev/null || echo "FAIL")
    if [[ "$REDIS_PING" == PONG:* ]]; then
        echo "✅ Redis TCP connectivity OK (${REDIS_PING#PONG:})"
    else
        echo "⚠️ Redis TCP connectivity FAILED: $REDIS_PING"
        echo "   MemoryBankCache will degrade to disk I/O until Redis is reachable."
    fi
else
    echo "❌ CRITICAL: Redis Python package still missing after self-heal attempt!"
fi

# =============================================================================
# 15. CLEANUP & MARKER
# =============================================================================
echo "Clearing Python bytecode cache from /agix..."
find /agix -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find /agix -name "*.pyc" -delete 2>/dev/null || true

echo "Clearing npm and npx caches..."
rm -rf /root/.npm/_npx 2>/dev/null || true
npm cache clean --force 2>/dev/null || true

# Trust mise config globally for AGIX
if command -v mise &> /dev/null; then
    [ -f /agix/.mise.toml ] && mise trust /agix/.mise.toml 2>/dev/null || true
    [ -f /git/agix/.mise.toml ] && mise trust /git/agix/.mise.toml 2>/dev/null || true
fi

# Create marker file to skip on future restarts
mkdir -p "$(dirname "$MARKER_FILE")"
echo "Setup completed: $(date -Iseconds)" > "$MARKER_FILE"
# Final cache clear
find /opt/venv-agix -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find /agix -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find /agix -name "*.pyc" -delete 2>/dev/null || true

echo "=== AGIX Additional Setup Complete ==="
