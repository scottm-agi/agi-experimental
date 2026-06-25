"""
BLOB Decomposition Helper — Enhanced BLOB Blocker Messages.

RCA comprehensive Fix 5: Provides specific, actionable decomposition
suggestions when a fullstack blob delegation is detected, rather than
generic "split into frontend and backend" advice.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("agix.blob_decomposition")


def build_blob_decomposition(
    frontend_signals: list[str],
    backend_signals: list[str],
) -> str:
    """Build a specific decomposition suggestion based on detected signals.

    Args:
        frontend_signals: List of frontend-related terms detected.
        backend_signals: List of backend-related terms detected.

    Returns:
        Formatted decomposition message with numbered tasks.
    """
    backend_tasks = _infer_backend_tasks(backend_signals)
    frontend_tasks = _infer_frontend_tasks(frontend_signals)

    lines = [
        "⛔ FULLSTACK BLOB DETECTED — Decompose into separate delegations:\n",
        "### 1. Backend Delegation (profile='code')",
    ]
    for task in backend_tasks:
        lines.append(f"   - {task}")

    lines.append("")
    lines.append("### 2. Frontend Delegation (profile='code')")
    for task in frontend_tasks:
        lines.append(f"   - {task}")

    lines.extend([
        "",
        "### Why Decompose?",
        "Mixed delegations cause type mismatches, import conflicts, and ",
        "parallel write corruption. Separate delegations build cleanly.",
        "",
        "Each delegation MUST include `requirement_ids` from the manifest.",
    ])

    return "\n".join(lines)


def _infer_backend_tasks(signals: list[str]) -> list[str]:
    """Infer specific backend tasks from detected signals."""
    tasks = []
    signal_set = {s.lower() for s in signals}

    if any(kw in signal_set for kw in {"api", "route", "endpoint"}):
        tasks.append("Implement API route handlers (src/app/api/*/route.ts)")
    if any(kw in signal_set for kw in {"database", "prisma", "db", "schema"}):
        tasks.append("Set up database schema and Prisma models")
    if any(kw in signal_set for kw in {"auth", "authentication", "login"}):
        tasks.append("Configure authentication middleware")
    if any(kw in signal_set for kw in {"email", "outreach", "notification"}):
        tasks.append("Build email/notification service integration")

    if not tasks:
        tasks.append("Implement backend API routes and business logic")
        tasks.append("Set up data models and database integration")

    return tasks


def _infer_frontend_tasks(signals: list[str]) -> list[str]:
    """Infer specific frontend tasks from detected signals."""
    tasks = []
    signal_set = {s.lower() for s in signals}

    if any(kw in signal_set for kw in {"react", "component", "card", "ui"}):
        tasks.append("Build React components for UI rendering")
    if any(kw in signal_set for kw in {"page", "layout", "navigation", "nav"}):
        tasks.append("Create page layouts and navigation structure")
    if any(kw in signal_set for kw in {"form", "input", "submit"}):
        tasks.append("Implement form components with validation")
    if any(kw in signal_set for kw in {"style", "css", "tailwind", "theme"}):
        tasks.append("Apply styling and theme configuration")

    if not tasks:
        tasks.append("Build page components and layouts")
        tasks.append("Wire UI to API endpoints via fetch()")

    return tasks
