"""
Dashboard refresh helper (Issue #798)

Provides a function to refresh dashboard metrics and return a
timestamped summary. Used by the scheduled task and the auto-refresh
frontend polling.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict


def refresh_dashboard_data() -> Dict[str, Any]:
    """
    Refresh and return dashboard data with a timestamp.

    Returns a dict with 'last_updated' ISO timestamp and summary metrics.
    """
    from python.api.dashboard_a2ui import build_dashboard_a2ui

    payload = build_dashboard_a2ui()
    timestamp = datetime.now(timezone.utc).isoformat()

    return {
        "last_updated": timestamp,
        "payload": payload,
    }


def get_last_update_timestamp() -> str:
    """Return the current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).isoformat()
