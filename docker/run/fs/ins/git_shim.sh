#!/bin/bash
# AGIX Git Isolation Shim
# Prevents unauthorized Git operations outside of projects/tmp directories

CWD=$(pwd -P) # Resolve physical path
REAL_GIT="/usr/share/git-agix/git"

# Whitelisted paths — must be INSIDE a project, not at the root
WHITELIST=(
    "/agix/usr/projects/"
)

# Check if CWD or any of its parents are in the whitelist
CHECK_PATH="$CWD"

# Intelligent path detection for remote execution/one-liners
# 1. Check for -C or --git-dir
i=1
for arg in "$@"; do
    if [[ "$arg" == "-C" ]]; then
        next_i=$((i+1))
        eval "TARGET_C=\${$next_i}"
        if [[ -n "$TARGET_C" ]]; then
            CHECK_PATH="$TARGET_C"
        fi
        break
    elif [[ "$arg" == -C* ]]; then
        CHECK_PATH="${arg#-C}"
        break
    elif [[ "$arg" == "clone" ]]; then
        # For clone, the last positional argument (if not starting with -)
        # is often the destination. 
        # Note: This is an approximation but safe as git_guard handles the hard blocks.
        potential_path="${@: -1}"
        if [[ ! "$potential_path" == -* ]] && [[ ! "$potential_path" == *"://"* ]]; then
             CHECK_PATH="$potential_path"
        fi
    fi
    i=$((i+1))
done

# Resolve physical path and ensure it's absolute
if [[ "$CHECK_PATH" != /* ]]; then
    CHECK_PATH="$CWD/$CHECK_PATH"
fi
CHECK_PATH=$(readlink -f "$CHECK_PATH" 2>/dev/null || echo "$CHECK_PATH")

IS_WHITELISTED=false
for path in "${WHITELIST[@]}"; do
    if [[ "$CHECK_PATH" == "$path"* ]]; then
        IS_WHITELISTED=true
        break
    fi
done

# Protection bypass for system initializations (if ENV set)
if [[ "$GIT_ISOLATION_BYPASS" == "5ff78804-system-init" ]]; then
    IS_WHITELISTED=true
fi

# Always allow version/help
if [[ "$1" == "version" ]] || [[ "$1" == "--version" ]] || [[ "$1" == "help" ]] || [[ "$1" == "--help" ]]; then
    IS_WHITELISTED=true
fi

if [[ "$IS_WHITELISTED" == "true" ]]; then
    # Pass through to real git, ensuring ceiling is set
    export GIT_CEILING_DIRECTORIES="/agix"
    exec "$REAL_GIT" "$@"
else
    echo "🚨 [GitGuard] BLOCKED: Git operation attempted in unauthorized directory: $CHECK_PATH (CWD: $CWD)"
    echo "Git operations are strictly restricted to /agix/usr/projects/ to protect the host repository."
    exit 1
fi
