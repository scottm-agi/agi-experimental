#!/bin/bash
set -e

# update apt
apt-get update

# CRITICAL: Create /opt/venv-agix symlink at build time
# The base image has /opt/venv-agix (Python 3.12.4 via pyenv) — this is the runtime venv.
# initialize.sh creates this symlink at container start, but we also need it at build time
# so that setup_venv.sh activates the correct venv for pip installs.
if [ -d "/opt/venv-agix" ] && [ ! -e "/opt/venv-agix" ]; then
    ln -s /opt/venv-agix /opt/venv-agix
    echo "Created build-time symlink: /opt/venv-agix -> /opt/venv-agix"
fi

# Install build dependencies needed by C/Fortran extensions (curated-tokenizers, scipy, etc.)
apt-get install -y --no-install-recommends python3-dev build-essential gfortran

# fix permissions for cron files if any
if [ -f /etc/cron.d/* ]; then
    chmod 0644 /etc/cron.d/*
fi

# Prepare SSH daemon
bash /ins/setup_ssh.sh "$@"
