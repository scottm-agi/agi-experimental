"""
Token Usage API Endpoint (Issue #799)

Provides REST endpoints for querying token usage analytics.
Integrates with python.helpers.token_tracker.TokenTracker.
"""
from __future__ import annotations

from python.helpers.api import ApiHandler, Request, Response
from python.helpers.token_tracker import get_token_tracker
from datetime import datetime, timedelta, timezone
from typing import Optional


class TokenUsage(ApiHandler):

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
        tracker = get_token_tracker()
        
        # Parse optional time filter
        since: Optional[datetime] = None
        hours = input.get("hours")
        since_str = input.get("since")
        
        if hours:
            since = datetime.now(timezone.utc) - timedelta(hours=int(hours))
        elif since_str:
            try:
                since = datetime.fromisoformat(since_str)
            except (ValueError, TypeError):
                pass

        view = input.get("view", "summary")
        limit = int(input.get("limit", 50))

        if view == "by_model":
            data = tracker.get_usage_by_model(since=since, limit=limit)
        elif view == "by_agent":
            data = tracker.get_usage_by_agent(since=since, limit=limit)
        elif view == "recent":
            data = tracker.get_recent_calls(limit=limit)
        else:
            data = tracker.get_usage_summary(since=since)

        return {
            "view": view,
            "data": data,
            "since": since.isoformat() if since else None,
        }


# Handler class is registered by the framework via run_ui.py auto-discovery
