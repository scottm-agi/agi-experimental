#!/bin/bash
set -e

# Exit immediately if a command exits with a non-zero status.
# set -e

# branch from parameter
if [ -z "$1" ]; then
    echo "Error: Branch parameter is empty. Please provide a valid branch name."
    exit 1
fi
BRANCH="$1"

if [ "$BRANCH" = "local" ]; then
    # For local branch, use the files
    echo "Using local dev files in /git/agix"
    # List all files recursively in the target directory
    # echo "All files in /git/agix (recursive):"
    # find "/git/agix" -type f | sort
else
    # For other branches, clone from Forgejo
    echo "Cloning repository from branch $BRANCH..."
    git clone -b "$BRANCH" "https://your-forgejo-instance.example.com/your-org/agi-experimental" "/git/agix" || {
        echo "CRITICAL ERROR: Failed to clone repository. Branch: $BRANCH"
        exit 1
    }
fi

. "/ins/setup_venv.sh" "$@"

# moved to base image
# # Ensure the virtual environment and pip setup
# pip install --upgrade pip ipython requests
# # Install some packages in specific variants
# pip install torch --index-url https://download.pytorch.org/whl/cpu

# PERMANENT FIX: openai-whisper + Python 3.13 compatibility
# - setuptools 82+ dropped pkg_resources (needed at build time)
# - openai-whisper 20240930 has KeyError __version__ on Python 3.13
# Solution: use latest openai-whisper (20250625) with setuptools<81 constraint
# NOTE: setup_venv.sh (line 29) already activated the correct venv
echo "setuptools<81" > /tmp/build-constraints.txt
PIP_CONSTRAINT=/tmp/build-constraints.txt pip install --no-cache-dir openai-whisper==20250625
rm -f /tmp/build-constraints.txt

# PERMANENT FIX: langchain-unstructured has onnxruntime<=1.19.2 hard dep (no cp313 wheels)
# Install with --no-deps — only UnstructuredLoader is used, which doesn't need onnxruntime
pip install --no-cache-dir --no-deps langchain-unstructured==0.1.6

uv pip install -r /git/agix/requirements.txt
# override for packages that have unnecessarily strict dependencies
if [ -f /git/agix/requirements2.txt ]; then
    uv pip install -r /git/agix/requirements2.txt
fi

# install playwright
bash /ins/install_playwright.sh "$@"

# Preload AGIX
python /git/agix/preload.py --dockerized=true || echo "WARNING: Preload failed, continuing build..."
