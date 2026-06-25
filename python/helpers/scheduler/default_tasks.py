"""
Default Scheduled Tasks (Issues #833, #820)

Seeds default scheduled tasks on first startup.
Idempotent — checks for existing tasks by name before creating.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from python.helpers.scheduler.task_list import SchedulerTaskList

logger = logging.getLogger("agix.scheduler.default_tasks")

# ── Default Task Definitions ─────────────────────────────────────────────────

GROWTH_TIP_TASK_NAME = "Daily Growth Tip"

GROWTH_TIP_SYSTEM_PROMPT = (
    "You are a marketing research analyst. Use the growth_tip_generator tool "
    "to generate a daily marketing/growth tip for SMBs. The tool handles "
    "RACE framework analysis, dedup via Jaccard similarity, and memory persistence."
)

GROWTH_TIP_PROMPT = (
    "Generate today's growth tip using the growth_tip_generator tool. "
    "If the first attempt returns a duplicate, retry with a different industry focus "
    "(e.g., SaaS, e-commerce, local services, B2B). "
    "Ensure the tip is actionable and backed by recent data."
)

GROWTH_TIP_SCOPE = (
    "Use the growth_tip_generator tool to generate and store marketing tips. "
    "Do NOT modify source code, create PRs, or alter project files. "
    "Read-only access to data for tip generation."
)

# ── Daily Dashboard Task (#820) ──────────────────────────────────────────────

DASHBOARD_TASK_NAME = "Daily Dashboard Update"

DASHBOARD_SYSTEM_PROMPT = (
    "You are a system metrics analyst. Use the system_dashboard tool to generate "
    "a comprehensive daily dashboard. After generating the A2UI payload, output it "
    "in a fenced ```a2ui block so it renders in the chat as a visual dashboard. "
    "Scan across the entire system: tokens, costs, projects, chats, disk, and "
    "recent agent work to present the top 10 most relevant metrics and visualizations."
)

DASHBOARD_PROMPT = (
    "Run the system_dashboard tool with action='full_dashboard' to generate today's "
    "personalized dashboard overview. Present the A2UI output exactly as returned. "
    "Focus on: token usage trends, active projects, recent chat activity, cost tracking, "
    "disk usage, model performance breakdown, and deliverable status. "
    "Highlight any anomalies (e.g., unusual token spikes, disk usage above 80%)."
)

DASHBOARD_SCOPE = (
    "Use the system_dashboard tool to read metrics and generate dashboard reports. "
    "Do NOT modify source code, configs, or project files. "
    "Read-only analysis and reporting."
)


async def seed_default_tasks(task_list: "SchedulerTaskList") -> int:
    """
    Seed default scheduled tasks if they don't already exist.
    
    Returns the number of tasks seeded (0 if all already exist).
    """
    from python.helpers.scheduler.models import ScheduledTask, TaskSchedule

    seeded = 0

    # ── Growth Tip Generator (daily at midnight UTC) ──────────────────────
    existing = task_list.get_task_by_name(GROWTH_TIP_TASK_NAME)
    if existing is None:
        schedule = TaskSchedule(
            minute="0",
            hour="0",
            day="*",
            month="*",
            weekday="*",
        )
        task = ScheduledTask.create(
            name=GROWTH_TIP_TASK_NAME,
            system_prompt=GROWTH_TIP_SYSTEM_PROMPT,
            prompt=GROWTH_TIP_PROMPT,
            attachments=[],
            schedule=schedule,
            context_id=None,  # Dedicated context
            project_name="automated-tasks",
            project_color="#6366f1",
            scope=GROWTH_TIP_SCOPE,
        )
        await task_list.add_task(task)
        seeded += 1
        logger.info(f"Seeded default scheduled task: {GROWTH_TIP_TASK_NAME}")

    # ── Daily Dashboard Update (daily at 6 AM UTC) ────────────────────────
    existing = task_list.get_task_by_name(DASHBOARD_TASK_NAME)
    if existing is None:
        schedule = TaskSchedule(
            minute="0",
            hour="6",
            day="*",
            month="*",
            weekday="*",
        )
        task = ScheduledTask.create(
            name=DASHBOARD_TASK_NAME,
            system_prompt=DASHBOARD_SYSTEM_PROMPT,
            prompt=DASHBOARD_PROMPT,
            attachments=[],
            schedule=schedule,
            context_id=None,  # Dedicated context
            project_name="automated-tasks",
            project_color="#6366f1",
            scope=DASHBOARD_SCOPE,
        )
        await task_list.add_task(task)
        seeded += 1
        logger.info(f"Seeded default scheduled task: {DASHBOARD_TASK_NAME}")

    if seeded:
        logger.info(f"Seeded {seeded} default task(s)")
    return seeded

