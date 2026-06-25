#!/bin/bash
# STABILITY_MARKER_V2
# The runtime (run_agix.sh) uses /opt/venv-agix (Python 3.12.4 via pyenv)
# Build-time installs MUST target the same venv to avoid ModuleNotFoundError at runtime
# /opt/venv is Python 3.13 (system) — NOT used by runtime scripts
if [ -d "/opt/venv-agix" ]; then
    source /opt/venv-agix/bin/activate
else
    # Fallback for environments without the agix venv
    source /opt/venv/bin/activate
fi