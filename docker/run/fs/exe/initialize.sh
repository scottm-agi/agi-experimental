#!/bin/bash
set -x

echo "Running initialization script..."

# Belt-and-suspenders: ensure exe scripts are executable at runtime.
# Cross-platform Docker builds (Rosetta 2 on Apple Silicon → linux/amd64)
# can lose file permissions during multi-stage layer merge.
chmod +x /exe/*.sh /exe/*.py 2>/dev/null || true

# Create venv symlink: base image has /opt/venv-agix, our code references /opt/venv-agix
if [ -d "/opt/venv-agix" ] && [ ! -e "/opt/venv-agix" ]; then
    ln -s /opt/venv-agix /opt/venv-agix
    echo "Created symlink: /opt/venv-agix -> /opt/venv-agix"
fi

# branch from parameter
if [ -z "$1" ]; then
    echo "Error: Branch parameter is empty. Please provide a valid branch name."
    exit 1
fi
BRANCH="$1"

# ==========================================
# Railway Persistent Storage Setup
# ==========================================
# On Railway, we have a single volume mounted at /agix/data
# We use symlinks to redirect other persistent directories there
# ==========================================
if [ -n "$RAILWAY_ENVIRONMENT" ] || [ -n "$RAILWAY_ENVIRONMENT_NAME" ] || [ -n "$RAILWAY_SERVICE_NAME" ] || [ -n "$RAILWAY_STATIC_URL" ]; then
    echo "Railway environment detected - setting up persistent storage symlinks..."
    
    # Audit existing volume contents for debugging
    echo "Current Volume Audit (/agix/data):"
    ls -F /agix/data || echo "Volume /agix/data is empty or not found."
    # Project Persistence Audit (/agix/data/projects) - Skip recursive list for performance
    ls -F /agix/data/projects 2>/dev/null || echo "No persistent projects found."
    
    # ==========================================
    # Unified Persistence Layer (8-Path Mapping)
    # ==========================================
    # Note: 'projects' is handled specifically to unify usr/projects and work/projects
    PERSIST_PATHS=("usr" "tmp" "memory" "knowledge" "logs" "instruments" "prompts" "playwright")

    for p in "${PERSIST_PATHS[@]}"; do
        APP_PATH="/agix/$p"
        DATA_PATH="/agix/data/$p"
        
        # Ensure data folder exists
        mkdir -p "$DATA_PATH"
        
        # If it's already a symlink, check if it points to the right place
        if [ -L "$APP_PATH" ]; then
            CURRENT_LINK=$(readlink -f "$APP_PATH")
            if [ "$CURRENT_LINK" == "$DATA_PATH" ]; then
                echo "Persistence: $APP_PATH already linked to $DATA_PATH ✅"
                continue
            else
                echo "Persistence: $APP_PATH is a symlink pointing to $CURRENT_LINK, removing and re-linking to $DATA_PATH..."
                rm -f "$APP_PATH"
            fi
        fi
        
        # Migration: Copy any ephemeral data to volume before symlinking
        if [ -d "$APP_PATH" ]; then
            echo "Persistence: Migrating any ephemeral data from $APP_PATH to $DATA_PATH..."
            # Use cp -an to avoid overwriting existing data in volume
            cp -an "$APP_PATH/." "$DATA_PATH/" 2>/dev/null || true
            rm -rf "$APP_PATH"
        fi
        
        # Final safety cleanup if path still exists (e.g. was a file)
        [ -e "$APP_PATH" ] && rm -rf "$APP_PATH"
        
        ln -sfn "$DATA_PATH" "$APP_PATH"
        echo "Persistence: Linked $APP_PATH -> $DATA_PATH ✅"
    done

    # ==========================================
    # Specific Project Unification
    # ==========================================
    # Goal: Ensure /agix/usr/projects and /agix/work/projects always see /agix/data/projects
    # CRITICAL: /agix/usr is already linked to /agix/data/usr
    mkdir -p "/agix/data/projects"
    
    # 1. Bridge the data layout: Ensure /agix/data/usr/projects links to /agix/data/projects
    # This ensures that /agix/usr/projects (which is /agix/data/usr/projects) sees the volume.
    DATA_USR_PRJ="/agix/data/usr/projects"
    if [ ! -L "$DATA_USR_PRJ" ]; then
        if [ -d "$DATA_USR_PRJ" ]; then
            echo "Persistence: Moving legacy projects from $DATA_USR_PRJ to /agix/data/projects..."
            cp -an "$DATA_USR_PRJ/." "/agix/data/projects/" 2>/dev/null || true
            rm -rf "$DATA_USR_PRJ"
        fi
        ln -sfn "/agix/data/projects" "$DATA_USR_PRJ"
        echo "Persistence: Linked $DATA_USR_PRJ -> /agix/data/projects ✅"
    fi

    # 2. Fix work/projects link
    # /agix/work is linked to /agix/data/work
    DATA_WORK_PRJ="/agix/data/work/projects"
    mkdir -p "/agix/data/work"
    if [ ! -L "$DATA_WORK_PRJ" ]; then
        if [ -d "$DATA_WORK_PRJ" ]; then
            echo "Persistence: Moving legacy projects from $DATA_WORK_PRJ to /agix/data/projects..."
            cp -an "$DATA_WORK_PRJ/." "/agix/data/projects/" 2>/dev/null || true
            rm -rf "$DATA_WORK_PRJ"
        fi
        ln -sfn "/agix/data/projects" "$DATA_WORK_PRJ"
        echo "Persistence: Linked $DATA_WORK_PRJ -> /agix/data/projects ✅"
    fi
    
    # Root-level /tmp Unification
    if [ ! -L "/tmp" ]; then
        echo "Persistence: Unifying root /tmp into /agix/data/tmp..."
        mkdir -p "/agix/data/tmp"
        # Copy existing temp data if any
        cp -an "/tmp/." "/agix/data/tmp/" 2>/dev/null || true
        rm -rf "/tmp"
        ln -sfn "/agix/data/tmp" "/tmp"
        chmod 1777 "/tmp" # Ensure stick bit for /tmp safety
        echo "Persistence: Linked /tmp -> /agix/data/tmp ✅"
    fi

    echo "Railway persistent storage setup complete."
fi

# Copy all contents from persistent /per to root directory (/) without overwriting
# Use rsync to safely merge into symlinked directories
if [ -d "/per" ] && [ "$(ls -A /per 2>/dev/null)" ] && [ "/per" != "/" ]; then
    echo "Synchronizing persistent configuration from /per to /..."
    rsync -a --no-perms --no-owner --no-group /per/ /
fi

# allow execution of /root/.bashrc and /root/.profile
chmod 444 /root/.bashrc
chmod 444 /root/.profile

# NOTE: apt-get update removed — all packages are baked into the Docker image.
# install_additional.sh will call apt-get update only if a tool is actually missing.

# ==========================================
# Resource Optimization for Railway
# ==========================================
# Limit thread pools for numerical libraries to avoid PID exhaustion
# Each MCP server or subprocess could otherwise spawn threads equal to CPU count (32 on Railway)
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export VECLIB_MAX_THREADS=1
export NUMEXPR_NUM_THREADS=1

# Attempt to bump PID limit if writable (Railway has a default of ~1000)
for pids_max_path in "/sys/fs/cgroup/pids.max" "/sys/fs/cgroup/pids/pids.max"; do
    if [ -w "$pids_max_path" ]; then
        echo "max" > "$pids_max_path" 2>/dev/null && echo "Increased PID limit in $pids_max_path" || true
    fi
done

# ==========================================
# Final Startup Audit
# ==========================================
echo "Final System Audit:"
echo "Current directory: $(pwd)"
echo "Environment: RAILWAY=$RAILWAY_ENVIRONMENT"
echo "USR path check (/agix/usr/projects):"
ls -ld /agix/usr/projects 2>/dev/null || echo "NOT FOUND"
echo "Volume audit (/agix/data/projects):"
ls -F /agix/data/projects 2>/dev/null || echo "NOT FOUND"
echo "WebUI audit (/agix/webui/public):"
ls -F /agix/webui/public/agix_logo.png 2>/dev/null || echo "LOGO NOT FOUND"

# ==========================================
# Final Middleware & Dependency Sync
# ==========================================
# Run installation and sync BEFORE starting any services.
# This prevents race conditions in supervisord where multiple services 
# compete for the same installation locks or markers.
echo "Syncing final dependencies..."
bash /ins/install_additional.sh "$@"

# let supervisord handle the services
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
