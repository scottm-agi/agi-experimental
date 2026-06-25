from __future__ import annotations
"""
Daily Improvement Task Initialization Extension

Registers a daily scheduled task that analyzes recent chat history 
to suggest improvements for the user and the system.
"""

import asyncio
import logging
from python.agent import Agent
from python.helpers.task_scheduler import TaskScheduler, ScheduledTask, TaskSchedule
from python.helpers.print_style import PrintStyle

logger = logging.getLogger(__name__)

async def extension(agent: Agent):
    """
    Register the daily improvement task if it doesn't already exist.
    This runs once per agent initialization, but we check if the task is already registered.
    """
    # Only run for the main supervisor or a primary user agent to avoid duplicates
    if agent.number != 0:
        return

    try:
        scheduler = TaskScheduler.get()
        task_name = "Daily System & User Improvement Analysis"
        
        # Check if task already exists
        existing_task = scheduler.get_task_by_name(task_name)
        if existing_task:
            return

        # Define the task
        system_prompt = (
            "You are a system optimization specialist. Your goal is to analyze the last 24 hours of "
            "interactive sessions (chat history) and identify the most impactful single improvement "
            "for the human user (to be more productive) and the single most impactful improvement "
            "for the AGIX system (to be more capable or efficient)."
        )
        
        prompt = (
            "Analyze all chat contexts from the last 24 hours. "
            "1. Identify one specific, actionable habit or technique the user can adopt to get better results. "
            "2. Identify one specific technical or behavioral improvement for the AGIX agent system. "
            "Present these clearly in a concise summary. Use a neutral, helpful tone."
        )
        
        # Schedule for once a day at midnight (00:00)
        schedule = TaskSchedule(
            minute="0",
            hour="0",
            day="*",
            month="*",
            weekday="*"
        )
        
        task = ScheduledTask.create(
            name=task_name,
            system_prompt=system_prompt,
            prompt=prompt,
            schedule=schedule,
            dedicated_context=True # Run in its own context
        )
        
        await scheduler.add_task(task)
        
        PrintStyle(
            font_color="green",
            padding=False,
            italic=True
        ).print(f"[Daily Improvement] Registered task: {task_name}")

    except Exception as e:
        logger.error(f"Failed to register daily improvement task: {e}")
