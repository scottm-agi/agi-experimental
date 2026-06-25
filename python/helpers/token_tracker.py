"""
Token Usage Tracking & Analytics Module (Issue #799)

Provides SQLite-backed logging of per-call LLM token usage for analytics,
cost tracking, and optimization. Complements the Redis-backed LLM cache
(llm_cache.py) by recording usage data even when cache hits occur.

Also writes a JSONL file at logs/llm_metrics.jsonl (consistent with
logs/supervisor.log pattern from supervisor_logging.py) for easy
dashboard consumption and grep-based analysis.

Source: #726 LLM API Efficiency Audit recommendation.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.token_tracker")

# Default DB location inside the data directory (consistent with data/chats.db, data/config.db)
_DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "token_usage.db")

# JSONL log file (consistent with logs/supervisor.log pattern from supervisor_logging.py)
_LLM_METRICS_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "logs", "llm_metrics.jsonl")

# Maximum JSONL file size before rotation (10MB)
_MAX_JSONL_SIZE_BYTES = 10 * 1024 * 1024

# R-10: Per-model cost table (USD per 1M tokens)
_MODEL_COSTS = {
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-3-flash": {"input": 0.10, "output": 0.40},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-haiku-3.5": {"input": 0.80, "output": 4.00},
}


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate cost in USD based on model name and token counts (R-10).

    Matches model names by substring so provider prefixes like
    'openrouter/google/gemini-2.5-flash' are handled automatically.
    More specific keys (e.g. 'gpt-4o-mini') are checked before less
    specific ones (e.g. 'gpt-4o') because we iterate longest-first.

    Returns 0.0 for unknown models.
    """
    model_lower = model.lower()
    # Strip provider prefix (e.g. openrouter/google/)
    if '/' in model_lower:
        model_lower = model_lower.split('/')[-1]
    # Sort keys longest-first to match 'gpt-4o-mini' before 'gpt-4o'
    for key in sorted(_MODEL_COSTS.keys(), key=len, reverse=True):
        if key in model_lower:
            costs = _MODEL_COSTS[key]
            return (tokens_in * costs["input"] + tokens_out * costs["output"]) / 1_000_000
    return 0.0


class TokenTracker:
    """
    Persistent token usage tracker backed by SQLite + JSONL dual-write.

    - SQLite (data/token_usage.db): Queryable store for API/dashboard access
    - JSONL (logs/llm_metrics.jsonl): Append-only log for streaming/grep analysis

    Thread-safe: uses a lock for write operations and separate connections
    for reads to avoid blocking the event loop.

    Data retention: 30 days (cleanup runs on singleton init).
    """

    def __init__(self, db_path: Optional[str] = None, jsonl_path: Optional[str] = None):
        self.db_path = db_path or os.path.abspath(_DEFAULT_DB_PATH)
        self.jsonl_path = jsonl_path or os.path.abspath(_LLM_METRICS_LOG_PATH)
        self._write_lock = threading.Lock()
        self._init_db()
        # Ensure JSONL directory exists
        os.makedirs(os.path.dirname(self.jsonl_path), exist_ok=True)

    def _init_db(self) -> None:
        """Create the usage table if it doesn't exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    model TEXT NOT NULL,
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    cost_estimate REAL DEFAULT 0.0,
                    duration_ms REAL DEFAULT 0.0,
                    call_site TEXT DEFAULT '',
                    agent_id TEXT DEFAULT '',
                    cached INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_token_usage_model
                ON token_usage(model)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp
                ON token_usage(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_token_usage_agent
                ON token_usage(agent_id)
            """)
            conn.commit()
        finally:
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a new SQLite connection (thread-safe read path)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def log_usage(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        call_site: str = "",
        agent_id: str = "",
        agent_name: str = "",
        cost_estimate: float = 0.0,
        duration_ms: float = 0.0,
        cached: bool = False,
        session_id: str = "",
        chat_id: str = "",
        project: str = "",
    ) -> None:
        """
        Log a single LLM call's token usage to both SQLite and JSONL.

        Args:
            model: Full model identifier (e.g. "openrouter/google/gemini-3-flash-preview")
            tokens_in: Number of input/prompt tokens
            tokens_out: Number of output/completion tokens
            call_site: Where the call originated (e.g. "unified_call", "browser_agent")
            agent_id: Agent identifier (e.g. "0", "1")
            agent_name: Human-readable agent name (R-4, e.g. "Researcher", "Code")
            cost_estimate: Estimated cost in USD (if available; auto-computed when 0.0)
            duration_ms: Call duration in milliseconds
            cached: Whether this was a cache hit
            session_id: Trace session ID for cross-source correlation (R-5)
            chat_id: Chat context ID for cross-source correlation (R-5)
            project: Active project name for cross-source correlation (R-5)
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        # R-10: Auto-compute cost when caller doesn't provide one
        if cost_estimate == 0.0:
            cost_estimate = _estimate_cost(model, tokens_in, tokens_out)

        # Build the log entry dict (shared between SQLite and JSONL)
        entry = {
            "timestamp": timestamp,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_total": tokens_in + tokens_out,
            "cost_estimate": cost_estimate,
            "duration_ms": round(duration_ms, 2),
            "call_site": call_site,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "cached": cached,
            "session_id": session_id,
            "chat_id": chat_id,
            "project": project,
        }

        # 1. Write to SQLite (primary store — queryable)
        try:
            with self._write_lock:
                conn = sqlite3.connect(self.db_path)
                try:
                    conn.execute(
                        """INSERT INTO token_usage 
                           (timestamp, model, tokens_in, tokens_out, cost_estimate, 
                            duration_ms, call_site, agent_id, cached)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (timestamp, model, tokens_in, tokens_out, cost_estimate,
                         duration_ms, call_site, agent_id, 1 if cached else 0),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            logger.warning(f"Failed to log token usage to SQLite: {e}")

        # 2. Write to JSONL (append-only log — consistent with supervisor_logging.py)
        try:
            self._write_jsonl(entry)
        except Exception as e:
            logger.debug(f"Failed to write JSONL metrics log: {e}")

    def _write_jsonl(self, entry: Dict[str, Any]) -> None:
        """Append a JSON line to the metrics log file, with size-based rotation."""
        try:
            # Rotate if file exceeds max size
            if os.path.exists(self.jsonl_path):
                file_size = os.path.getsize(self.jsonl_path)
                if file_size > _MAX_JSONL_SIZE_BYTES:
                    self._rotate_jsonl()

            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"JSONL write failed: {e}")

    def _rotate_jsonl(self) -> None:
        """Rotate the JSONL file — keep last ~2MB of data."""
        try:
            file_size = os.path.getsize(self.jsonl_path)
            keep_bytes = 2 * 1024 * 1024  # Keep last 2MB
            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                f.seek(max(0, file_size - keep_bytes))
                content = f.read()
                # Skip partial first line
                first_newline = content.find("\n")
                if first_newline != -1:
                    content = content[first_newline + 1:]

            # Atomic write
            tmp_path = self.jsonl_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, self.jsonl_path)
            logger.info(f"Rotated llm_metrics.jsonl: {file_size} -> {len(content)} bytes")
        except Exception as e:
            logger.warning(f"JSONL rotation failed: {e}")

    def get_usage_summary(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Get aggregate usage summary, optionally filtered by time range.

        Returns:
            dict with total_calls, total_tokens_in, total_tokens_out,
            total_estimated_cost, avg_tokens_per_call
        """
        conn = self._get_conn()
        try:
            query = """
                SELECT 
                    COUNT(*) as total_calls,
                    COALESCE(SUM(tokens_in), 0) as total_tokens_in,
                    COALESCE(SUM(tokens_out), 0) as total_tokens_out,
                    COALESCE(SUM(cost_estimate), 0.0) as total_estimated_cost,
                    COALESCE(AVG(duration_ms), 0.0) as avg_duration_ms
                FROM token_usage
            """
            params: List[Any] = []
            conditions = []

            if since:
                conditions.append("timestamp >= ?")
                params.append(since.isoformat())
            if until:
                conditions.append("timestamp <= ?")
                params.append(until.isoformat())

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            row = conn.execute(query, params).fetchone()
            total_calls = row["total_calls"]
            return {
                "total_calls": total_calls,
                "total_tokens_in": row["total_tokens_in"],
                "total_tokens_out": row["total_tokens_out"],
                "total_tokens": row["total_tokens_in"] + row["total_tokens_out"],
                "total_estimated_cost": round(row["total_estimated_cost"], 6),
                "avg_duration_ms": round(row["avg_duration_ms"], 2),
                "avg_tokens_per_call": round(
                    (row["total_tokens_in"] + row["total_tokens_out"]) / total_calls, 1
                ) if total_calls > 0 else 0,
            }
        finally:
            conn.close()

    def get_usage_by_model(
        self,
        since: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get usage grouped by model.

        Returns:
            List of dicts with model, total_calls, total_tokens_in, total_tokens_out
        """
        conn = self._get_conn()
        try:
            query = """
                SELECT 
                    model,
                    COUNT(*) as total_calls,
                    SUM(tokens_in) as total_tokens_in,
                    SUM(tokens_out) as total_tokens_out,
                    SUM(cost_estimate) as total_estimated_cost,
                    AVG(duration_ms) as avg_duration_ms
                FROM token_usage
            """
            params: List[Any] = []
            if since:
                query += " WHERE timestamp >= ?"
                params.append(since.isoformat())

            query += " GROUP BY model ORDER BY total_calls DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "model": r["model"],
                    "total_calls": r["total_calls"],
                    "total_tokens_in": r["total_tokens_in"],
                    "total_tokens_out": r["total_tokens_out"],
                    "total_tokens": r["total_tokens_in"] + r["total_tokens_out"],
                    "total_estimated_cost": round(r["total_estimated_cost"], 6),
                    "avg_duration_ms": round(r["avg_duration_ms"], 2),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_usage_by_agent(
        self,
        since: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get usage grouped by agent_id.

        Returns:
            List of dicts with agent_id, total_calls, total_tokens_in, total_tokens_out
        """
        conn = self._get_conn()
        try:
            query = """
                SELECT 
                    agent_id,
                    COUNT(*) as total_calls,
                    SUM(tokens_in) as total_tokens_in,
                    SUM(tokens_out) as total_tokens_out,
                    SUM(cost_estimate) as total_estimated_cost,
                    AVG(duration_ms) as avg_duration_ms
                FROM token_usage
            """
            params: List[Any] = []
            if since:
                query += " WHERE timestamp >= ?"
                params.append(since.isoformat())

            query += " GROUP BY agent_id ORDER BY total_calls DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "agent_id": r["agent_id"],
                    "total_calls": r["total_calls"],
                    "total_tokens_in": r["total_tokens_in"],
                    "total_tokens_out": r["total_tokens_out"],
                    "total_tokens": r["total_tokens_in"] + r["total_tokens_out"],
                    "total_estimated_cost": round(r["total_estimated_cost"], 6),
                    "avg_duration_ms": round(r["avg_duration_ms"], 2),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_recent_calls(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get the most recent LLM calls."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT * FROM token_usage 
                   ORDER BY timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_daily_breakdown(
        self,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Get daily token usage breakdown for charting/dashboard.

        Args:
            days: Number of days to look back (default 30).

        Returns:
            List of dicts with date, total_calls, total_tokens_in, total_tokens_out, total_cost
        """
        from datetime import timedelta
        since = datetime.now(timezone.utc) - timedelta(days=days)
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT 
                    DATE(timestamp) as date,
                    COUNT(*) as total_calls,
                    SUM(tokens_in) as total_tokens_in,
                    SUM(tokens_out) as total_tokens_out,
                    SUM(cost_estimate) as total_estimated_cost,
                    AVG(duration_ms) as avg_duration_ms,
                    SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) as cache_hits
                FROM token_usage
                WHERE timestamp >= ?
                GROUP BY DATE(timestamp)
                ORDER BY date ASC""",
                (since.isoformat(),),
            ).fetchall()
            return [
                {
                    "date": r["date"],
                    "total_calls": r["total_calls"],
                    "total_tokens_in": r["total_tokens_in"],
                    "total_tokens_out": r["total_tokens_out"],
                    "total_tokens": r["total_tokens_in"] + r["total_tokens_out"],
                    "total_estimated_cost": round(r["total_estimated_cost"], 6),
                    "avg_duration_ms": round(r["avg_duration_ms"], 2),
                    "cache_hits": r["cache_hits"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_metrics_export(self, days: int = 30) -> Dict[str, Any]:
        """
        Export comprehensive metrics for dashboard display.
        Returns all views combined for a single API call.
        """
        from datetime import timedelta
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return {
            "summary": self.get_usage_summary(since=since),
            "by_model": self.get_usage_by_model(since=since, limit=20),
            "by_agent": self.get_usage_by_agent(since=since, limit=20),
            "daily_breakdown": self.get_daily_breakdown(days=days),
            "recent_calls": self.get_recent_calls(limit=10),
            "period_days": days,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def cleanup_old_data(self, retention_days: int = 30) -> int:
        """
        Delete records older than retention_days.
        
        Returns:
            Number of rows deleted.
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        try:
            with self._write_lock:
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.execute(
                        "DELETE FROM token_usage WHERE timestamp < ?",
                        (cutoff,),
                    )
                    deleted = cursor.rowcount
                    conn.commit()
                    if deleted > 0:
                        logger.info(f"Token tracker cleanup: removed {deleted} records older than {retention_days} days")
                    return deleted
                finally:
                    conn.close()
        except Exception as e:
            logger.warning(f"Failed to cleanup old token usage data: {e}")
            return 0

    def check_budget(
        self,
        max_tokens: int = 0,
        reset_interval: str = "day",
    ) -> Dict[str, Any]:
        """
        Check whether current usage is within the configured token budget.

        Args:
            max_tokens: Max total tokens (in+out) allowed per reset interval. 0 = no limit.
            reset_interval: "day" or "month" — when the budget counter resets.

        Returns:
            dict with:
                within_budget: bool — True if under the configured limit
                budget_type: str — "none" or "tokens"
                current_usage: int — current interval's token usage
                budget_limit: int — the configured limit
                remaining: int — how many tokens remain before limit
        """
        # If no limit set, always within budget
        if max_tokens <= 0:
            return {
                "within_budget": True,
                "budget_type": "none",
                "current_usage": 0,
                "budget_limit": 0,
                "remaining": 0,
            }

        # Determine the start timestamp based on reset interval
        from datetime import datetime
        now = datetime.now(timezone.utc)
        if reset_interval == "month":
            interval_start = now.strftime("%Y-%m-01T00:00:00")
        else:  # "day" (default)
            interval_start = now.strftime("%Y-%m-%dT00:00:00")

        conn = self._get_conn()
        try:
            row = conn.execute(
                """SELECT
                    COALESCE(SUM(tokens_in + tokens_out), 0) as total_tokens
                FROM token_usage
                WHERE timestamp >= ?""",
                (interval_start,),
            ).fetchone()
            current_tokens = row["total_tokens"]
        finally:
            conn.close()

        remaining = max(0, max_tokens - current_tokens)

        if current_tokens >= max_tokens:
            return {
                "within_budget": False,
                "budget_type": "tokens",
                "current_usage": current_tokens,
                "budget_limit": max_tokens,
                "remaining": 0,
            }

        return {
            "within_budget": True,
            "budget_type": "tokens",
            "current_usage": current_tokens,
            "budget_limit": max_tokens,
            "remaining": remaining,
        }


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------
_tracker_instance: Optional[TokenTracker] = None
_tracker_lock = threading.Lock()


def get_token_tracker() -> TokenTracker:
    """Get or create the global TokenTracker singleton."""
    global _tracker_instance
    if _tracker_instance is None:
        with _tracker_lock:
            if _tracker_instance is None:
                _tracker_instance = TokenTracker()
                # Run cleanup on first access to enforce 30-day retention
                try:
                    _tracker_instance.cleanup_old_data(retention_days=30)
                except Exception:
                    pass
    return _tracker_instance

