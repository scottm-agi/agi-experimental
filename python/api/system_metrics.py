"""
System Metrics API Endpoint (Issue #802)

Provides aggregated system metrics from multiple data sources:
- Token usage (data/token_usage.db via token_tracker)
- Supervisor interventions (logs/supervisor.log)
- Chat activity (data/chats.db)

Used by the SystemDashboard tool and the dashboard page.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from python.helpers.api import ApiHandler, Request, Response

logger = logging.getLogger("agix.system_metrics")

# Default paths (relative to project root)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_TOKEN_DB = os.path.join(_PROJECT_ROOT, "data", "token_usage.db")
_DEFAULT_SUPERVISOR_LOG = os.path.join(_PROJECT_ROOT, "logs", "supervisor.log")
_DEFAULT_CHATS_DB = os.path.join(_PROJECT_ROOT, "data", "chats.db")


def _get_disk_usage() -> Dict[str, Any]:
    """Get disk usage metrics."""
    disk = shutil.disk_usage("/")
    return {
        "total": disk.total,
        "used": disk.used,
        "free": disk.free,
        "used_pct": round(disk.used / disk.total * 100, 2),
    }


def aggregate_dashboard_metrics(
    db_path: Optional[str] = None,
    supervisor_log_path: Optional[str] = None,
    chats_db_path: Optional[str] = None,
    days: int = 30,
) -> Dict[str, Any]:
    """
    Aggregate metrics from all data sources into a single dashboard payload.

    Returns:
        {
            "token_summary": {...},
            "model_breakdown": [...],
            "daily_trend": [...],
            "supervisor_stats": {...},
            "disk_usage": {...},
        }
    """
    db = db_path or _DEFAULT_TOKEN_DB
    sup_log = supervisor_log_path or _DEFAULT_SUPERVISOR_LOG
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_str = since.isoformat()

    return {
        "token_summary": _get_token_summary(db, since_str),
        "model_breakdown": _get_model_breakdown(db, since_str),
        "daily_trend": _get_daily_trend(db, since_str),
        "supervisor_stats": _get_supervisor_stats(sup_log, since_str),
        "disk_usage": _get_disk_usage(),
    }


def _get_token_summary(db_path: str, since: str) -> Dict[str, Any]:
    """Get aggregate token usage summary."""
    default = {
        "total_calls": 0,
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "total_tokens": 0,
        "total_estimated_cost": 0.0,
        "avg_duration_ms": 0.0,
        "cache_hit_rate": 0.0,
    }
    if not os.path.exists(db_path):
        return default

    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            """SELECT 
                COUNT(*) as total_calls,
                COALESCE(SUM(tokens_in), 0) as total_tokens_in,
                COALESCE(SUM(tokens_out), 0) as total_tokens_out,
                COALESCE(SUM(cost_estimate), 0.0) as total_cost,
                COALESCE(AVG(duration_ms), 0.0) as avg_duration,
                COALESCE(SUM(cached), 0) as cached_count
            FROM token_usage WHERE timestamp >= ?""",
            (since,),
        ).fetchone()
        conn.close()

        total_calls = row[0] if row[0] else 0
        return {
            "total_calls": total_calls,
            "total_tokens_in": row[1],
            "total_tokens_out": row[2],
            "total_tokens": row[1] + row[2],
            "total_estimated_cost": round(row[3], 4),
            "avg_duration_ms": round(row[4], 2),
            "cache_hit_rate": round(row[5] / total_calls, 4) if total_calls > 0 else 0.0,
        }
    except Exception as e:
        logger.warning(f"Failed to get token summary: {e}")
        return default


def _get_model_breakdown(db_path: str, since: str) -> List[Dict[str, Any]]:
    """Get per-model token usage breakdown."""
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """SELECT model,
                SUM(tokens_in + tokens_out) as total_tokens,
                COUNT(*) as call_count,
                SUM(cost_estimate) as total_cost,
                AVG(duration_ms) as avg_duration
            FROM token_usage
            WHERE timestamp >= ?
            GROUP BY model
            ORDER BY total_tokens DESC""",
            (since,),
        ).fetchall()
        conn.close()
        return [
            {
                "model": r[0],
                "total_tokens": r[1],
                "call_count": r[2],
                "total_cost": round(r[3], 4),
                "avg_duration_ms": round(r[4], 2),
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"Failed to get model breakdown: {e}")
        return []


def _get_daily_trend(db_path: str, since: str) -> List[Dict[str, Any]]:
    """Get daily token usage trend."""
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """SELECT 
                DATE(timestamp) as date,
                SUM(tokens_in) as tokens_in,
                SUM(tokens_out) as tokens_out,
                COUNT(*) as calls
            FROM token_usage
            WHERE timestamp >= ?
            GROUP BY DATE(timestamp)
            ORDER BY date ASC""",
            (since,),
        ).fetchall()
        conn.close()
        return [
            {
                "date": r[0],
                "tokens_in": r[1],
                "tokens_out": r[2],
                "calls": r[3],
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"Failed to get daily trend: {e}")
        return []


def _get_supervisor_stats(log_path: str, since: str) -> Dict[str, Any]:
    """Parse supervisor JSONL log for intervention stats."""
    default = {"total_interventions": 0, "by_type": {}}
    if not os.path.exists(log_path):
        return default
    try:
        total = 0
        by_type: Dict[str, int] = {}
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("timestamp", "")
                    if ts >= since:
                        total += 1
                        itype = entry.get("type", "unknown")
                        by_type[itype] = by_type.get(itype, 0) + 1
                except json.JSONDecodeError:
                    continue
        return {"total_interventions": total, "by_type": by_type}
    except Exception as e:
        logger.warning(f"Failed to parse supervisor log: {e}")
        return default


class SystemMetrics(ApiHandler):
    """API handler for system-wide analytics dashboard metrics."""

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        action = input.get("action", "get_dashboard_metrics")
        days = int(input.get("days", 30))

        if action == "get_dashboard_metrics":
            return self._aggregate_metrics(days=days)
        elif action == "get_token_summary":
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            return {"data": _get_token_summary(_DEFAULT_TOKEN_DB, since)}
        elif action == "get_system_health":
            return self._get_system_health()
        else:
            return {"error": f"Unknown action: {action}"}

    def _aggregate_metrics(self, days: int = 30) -> dict:
        """Aggregate all metrics."""
        return aggregate_dashboard_metrics(days=days)

    def _get_system_health(self) -> dict:
        """Basic system health check."""
        return {
            "disk_usage": _get_disk_usage(),
            "token_db_exists": os.path.exists(_DEFAULT_TOKEN_DB),
            "supervisor_log_exists": os.path.exists(_DEFAULT_SUPERVISOR_LOG),
            "chats_db_exists": os.path.exists(_DEFAULT_CHATS_DB),
        }


# Handler class is registered by the framework via run_ui.py auto-discovery
