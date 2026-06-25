"""
Base module for repository automation.
Contains constants, shared types, and base utilities.
"""

import logging
from typing import List

logger = logging.getLogger("repository-automation")

# =============================================================================
# CONSTANTS
# =============================================================================

DEFAULT_EXCLUDES = [
    ".git", "node_modules", "vendor", "bower_components", "venv", ".venv", 
    "__pycache__", "dist", "build", "tmp", "temp", "out", "target", "vendor",
    "*.min.js", "*.min.css", "*.map", "*.log", "*.bin", "*.exe", "*.so", "*.o", "*.json", "*.md",
    "*.pyc", "*.pyo", "*.pyd", "*.db", "*.sqlite", "*.wasm", "*.dll", "*.dylib", "*.lib"
]

# Files we ALWAYS want to include if they exist
ROOT_INCLUDES = ["package.json", "requirements.txt", "pyproject.toml", "README.md", "TASK_IDS.md", "CRITICAL_RULES.md"]

# Context window optimization: Enforce word limit for codebase digests
MAX_CODEBASE_WORDS = 125000

# Supported repository automation actions
REPO_ACTIONS = [
    "list_issues",
    "get_issue",
    "comment",
    "create_issue",
    "analyze_issue",
    "set_refinement_template",
    "list_comments",
    "answer_comment",
    "monitor_issues",
    "trigger_build_task",
    "generate_user_story_and_uat",
    "validate_issue_content",
    "classify_issue",
    "check_sweep_status",
    "reset_sweep_cursor",
    "invalidate_issue_cache",
    "check_triage_status",
    "sweep_for_responses",
    "sweep_for_expert_analysis",
    "create_issue_comment",
    "list_open_issues"
]

# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    'DEFAULT_EXCLUDES',
    'ROOT_INCLUDES',
    'MAX_CODEBASE_WORDS',
    'REPO_ACTIONS',
    'logger'
]