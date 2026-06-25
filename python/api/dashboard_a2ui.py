"""
Dashboard A2UI API Endpoint

Returns an A2UI component payload with system metrics that the frontend
can render directly — no LLM round-trip required. This ensures the
dashboard always renders instantly, even after the dashboard chat
has been deleted.

Reuses data from system_metrics.aggregate_dashboard_metrics() and adds
filesystem-based project/chat/disk counts.
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Dict, List

from python.helpers.api import ApiHandler, Request, Response

logger = logging.getLogger("agix.dashboard_a2ui")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PROJECTS_DIR = os.path.join(_PROJECT_ROOT, "usr", "projects")


def _count_projects_and_chats() -> tuple[int, int, dict]:
    """Count projects, total chats, and per-project chat counts."""
    project_count = 0
    total_chats = 0
    project_chat_counts: Dict[str, int] = {}

    if os.path.exists(_PROJECTS_DIR):
        for d in os.listdir(_PROJECTS_DIR):
            full = os.path.join(_PROJECTS_DIR, d)
            if os.path.isdir(full) and not d.startswith("."):
                project_count += 1
                chats_dir = os.path.join(full, "chats")
                chat_count = 0
                if os.path.isdir(chats_dir):
                    chat_count = len(
                        [f for f in os.listdir(chats_dir) if f.endswith(".json")]
                    )
                total_chats += chat_count
                project_chat_counts[d] = chat_count

    return project_count, total_chats, project_chat_counts


def build_dashboard_a2ui() -> dict:
    """
    Build a complete A2UI payload with system metrics.

    Returns a dict with structure:
    {
        "messages": [
            {"updateComponents": {"components": [...]}}
        ]
    }
    """
    # Gather metrics from the system_metrics module
    try:
        from python.api.system_metrics import aggregate_dashboard_metrics
        metrics = aggregate_dashboard_metrics(days=30)
    except Exception as e:
        logger.warning(f"Failed to aggregate metrics: {e}")
        metrics = {
            "token_summary": {
                "total_calls": 0,
                "total_tokens_in": 0,
                "total_tokens_out": 0,
                "total_tokens": 0,
                "total_estimated_cost": 0.0,
            },
            "model_breakdown": [],
            "daily_trend": [],
            "supervisor_stats": {"total_interventions": 0, "by_type": {}},
        }

    # Filesystem-based counts
    project_count, total_chats, project_chat_counts = _count_projects_and_chats()

    # Disk usage
    try:
        disk = shutil.disk_usage("/")
        disk_pct = round(disk.used / disk.total * 100)
    except Exception:
        disk_pct = 0

    # Token data
    token_summary = metrics.get("token_summary", {})
    total_tokens = token_summary.get("total_tokens", 0)
    total_cost = token_summary.get("total_estimated_cost", 0.0)
    model_breakdown = metrics.get("model_breakdown", [])

    # ---------- Build A2UI components ----------
    components: List[Dict[str, Any]] = []
    children_ids: List[str] = []

    # Title
    components.append(
        {"id": "title", "component": "Text", "text": "AGIX System Dashboard", "variant": "h2"}
    )
    children_ids.append("title")
    components.append({"id": "div1", "component": "Divider"})
    children_ids.append("div1")

    # LLM Cache stats (Issue #836)
    cache_stats = {"hits": 0, "misses": 0, "hit_rate": 0.0, "enabled": False}
    try:
        from python.helpers.llm_cache import get_llm_cache
        cache = get_llm_cache()
        cache_stats = cache.get_stats()
    except Exception:
        pass

    # Stat cards row
    stats = [
        ("Total Projects", str(project_count), "PROJECTS"),
        ("Total Chats", str(total_chats), "CHATS"),
        ("Tokens Used", f"{total_tokens:,}", "TOKENS"),
        ("Cache Hit Rate", f"{cache_stats['hit_rate']}%", "LLM CACHE"),
        ("Disk Usage", f"{disk_pct}%", "DISK"),
    ]

    stat_row_children = []
    for i, (_label, value, cap) in enumerate(stats):
        val_id = f"sv_{i}"
        cap_id = f"sc_{i}"
        col_id = f"scol_{i}"
        components.append({"id": val_id, "component": "Text", "text": value, "variant": "h1"})
        components.append({"id": cap_id, "component": "Text", "text": cap, "variant": "caption"})
        components.append(
            {"id": col_id, "component": "Column", "children": [val_id, cap_id], "align": "center"}
        )
        stat_row_children.append(col_id)

    components.append(
        {"id": "stats_row", "component": "Row", "children": stat_row_children, "justify": "spaceAround"}
    )
    children_ids.append("stats_row")

    # Divider before model breakdown
    components.append({"id": "div2", "component": "Divider"})
    children_ids.append("div2")

    # Model breakdown (top 5)
    if model_breakdown:
        components.append(
            {"id": "model_title", "component": "Text", "text": "Token Usage by Model", "variant": "h3"}
        )
        children_ids.append("model_title")

        for j, entry in enumerate(model_breakdown[:5]):
            model_name = entry.get("model", "unknown")
            tokens = entry.get("total_tokens", 0)
            name_id = f"mn_{j}"
            tok_id = f"mt_{j}"
            row_id = f"mr_{j}"
            components.append({"id": name_id, "component": "Text", "text": model_name, "variant": "body"})
            components.append(
                {"id": tok_id, "component": "Text", "text": f"{tokens:,} tokens", "variant": "body"}
            )
            components.append(
                {"id": row_id, "component": "Row", "children": [name_id, tok_id], "justify": "spaceBetween"}
            )
            children_ids.append(row_id)

    # Project breakdown (top 5)
    if project_chat_counts:
        components.append({"id": "div3", "component": "Divider"})
        children_ids.append("div3")
        components.append(
            {"id": "proj_title", "component": "Text", "text": "Chats by Project", "variant": "h3"}
        )
        children_ids.append("proj_title")

        sorted_projects = sorted(project_chat_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        for k, (proj, count) in enumerate(sorted_projects):
            pn_id = f"pn_{k}"
            pc_id = f"pc_{k}"
            pr_id = f"pr_{k}"
            components.append({"id": pn_id, "component": "Text", "text": proj, "variant": "body"})
            components.append(
                {"id": pc_id, "component": "Text", "text": f"{count} chats", "variant": "body"}
            )
            components.append(
                {"id": pr_id, "component": "Row", "children": [pn_id, pc_id], "justify": "spaceBetween"}
            )
            children_ids.append(pr_id)

    # Daily trend chart
    daily_trend = metrics.get("daily_trend", [])
    if daily_trend and len(daily_trend) > 1:
        timeline_data = [
            {"name": entry.get("date", ""), "value": entry.get("tokens_in", 0) + entry.get("tokens_out", 0)}
            for entry in daily_trend[-14:]
        ]
        components.append({"id": "div_tl", "component": "Divider"})
        children_ids.append("div_tl")
        components.append({
            "id": "timeline",
            "component": "LineChart",
            "title": "Token Activity (last 14 days)",
            "data": timeline_data,
        })
        children_ids.append("timeline")

    # Main column + root card
    components.append({"id": "main_col", "component": "Column", "children": children_ids})
    components.append({"id": "root", "component": "Card", "child": "main_col"})

    # Add last_updated timestamp for auto-refresh (Issue #798)
    from datetime import datetime, timezone
    last_updated = datetime.now(timezone.utc).isoformat()

    return {
        "last_updated": last_updated,
        "messages": [
            {"updateComponents": {"components": components}}
        ]
    }


class DashboardA2ui(ApiHandler):
    """API handler that returns a ready-to-render A2UI dashboard payload."""

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
        try:
            return build_dashboard_a2ui()
        except Exception as e:
            logger.error(f"Dashboard A2UI generation failed: {e}")
            return {"error": str(e)}
