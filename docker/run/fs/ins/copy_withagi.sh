#!/bin/bash
# STABILITY_MARKER_V3
# =============================================================================
# copy_agix.sh — Code-only sync from image to persistent volume
# =============================================================================
# PURPOSE: Propagate code updates from the Docker image (/git/agix) to the
#          persistent volume (/agix) so new deploys take effect even when
#          a Railway volume is mounted.
#
# WHY THIS EXISTS: Railway mounts a persistent volume at /agix/data.
#          initialize.sh creates symlinks from /agix/prompts → /agix/data/prompts,
#          etc. New prompt files and code from the image at /git/agix/ would
#          never reach the running app without this sync.
#
# SECURITY: This script MUST NEVER sync user state. All user-generated data
#           is excluded below. When adding new data paths to the app, add
#           corresponding exclusions here.
# =============================================================================

SOURCE_DIR=/git/agix
TARGET_DIR=/agix

# Safety: abort if paths are empty or root
if [ -z "$SOURCE_DIR" ] || [ -z "$TARGET_DIR" ] || [ "$SOURCE_DIR" == "/" ] || [ "$TARGET_DIR" == "/" ]; then
    echo "ERROR: Invalid SOURCE_DIR ($SOURCE_DIR) or TARGET_DIR ($TARGET_DIR)"
    exit 1
fi

# Safety: verify source exists
if [ ! -d "$SOURCE_DIR" ]; then
    echo "ERROR: Source directory $SOURCE_DIR does not exist"
    exit 1
fi

# Only sync on Railway or if the target is missing the entry point
if [ -n "$RAILWAY_ENVIRONMENT" ] || [ ! -f "$TARGET_DIR/run_ui.py" ]; then
    echo "Synchronizing CODE ONLY from $SOURCE_DIR to $TARGET_DIR..."
    rsync -a --no-perms --no-owner --no-group --keep-dirlinks \
        \
        `# === USER DATA DIRECTORIES (persistent volume) ===` \
        --exclude "/data/"                  \
        --exclude "/tmp/"                   \
        --exclude "/logs/"                  \
        --exclude "/memory/"               \
        --exclude "/knowledge/"            \
        --exclude "/work/"                 \
        --exclude "/usr/"                  \
        --exclude "/chats/"                \
        --exclude "/output/"               \
        --exclude "/delete/"               \
        --exclude "/playwright/"           \
        --exclude "/instruments/"          \
        \
        `# === USER-GENERATED PROMPTS ===` \
        --exclude "prompts/golden_*.md"    \
        \
        `# === DATABASES (SQLite user state) ===` \
        --exclude "*.db"                   \
        --exclude "*.db-shm"               \
        --exclude "*.db-wal"               \
        --exclude "*.db.bak*"              \
        --exclude "*.db.corrupt*"          \
        --exclude "*.db.malformed*"         \
        --exclude "*.sqlite"               \
        --exclude "*.sqlite3"              \
        \
        `# === SECRETS & CREDENTIALS ===` \
        --exclude "settings.json"          \
        --exclude ".env"                   \
        --exclude ".env.*"                 \
        --exclude "token.json"             \
        --exclude "client_secret*.json"    \
        --exclude "credentials.json"       \
        --exclude "cookies.txt"            \
        \
        `# === RUNTIME ARTIFACTS ===` \
        --exclude "__pycache__/"           \
        --exclude "*.pyc"                  \
        --exclude "*.pyo"                  \
        --exclude ".DS_Store"              \
        --exclude "node_modules/"          \
        --exclude ".git/"                  \
        --exclude ".pytest_cache/"         \
        --exclude "*.log"                  \
        \
        "$SOURCE_DIR/" "$TARGET_DIR/"
    
    echo "Sync complete. Verifying entry point..."
    if [ -f "$TARGET_DIR/run_ui.py" ]; then
        echo "✅ run_ui.py present at $TARGET_DIR"
    else
        echo "❌ CRITICAL: run_ui.py missing after sync!"
        exit 1
    fi
fi