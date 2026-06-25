#!/bin/bash
# STABILITY_MARKER_V2
set -x
export PYTHONUNBUFFERED=1
# Wait until run_tunnel.py exists
echo "Starting tunnel API..."

sleep 1
while [ ! -f /agix/run_tunnel.py ]; do
    echo "Waiting for /agix/run_tunnel.py to be available..."
    sleep 1
done

. "/ins/setup_venv.sh" "$@"
# Explicitly disable exit-on-error to ensure we reach run_tunnel.py
set +e

export PYTHONPATH=/agix
exec /opt/venv-agix/bin/python3 /agix/run_tunnel.py \
    --dockerized=true \
    --port=55520 \
    --tunnel_api_port=55520 \
    --host="0.0.0.0" \
    --code_exec_docker_enabled=false \
    --code_exec_ssh_enabled=true
