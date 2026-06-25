"""
G-13: Environment Validator — message_loop_start extension.

Validates that the project's .env file is correctly configured before
allowing the agent to proceed. Sets _env_validated_project ONLY after
successful validation to prevent premature flag setting.

Order: 36 (after project auto-detect at 35, before budget advisor at 37)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from python.helpers.extension import Extension

logger = logging.getLogger("agix.env_validator")


def validate_env_file(project_dir: str) -> tuple[bool, list[str]]:
    """Validate the project's .env file.

    Returns:
        (is_valid, list_of_issues)
    """
    env_path = os.path.join(project_dir, ".env")
    env_example_path = os.path.join(project_dir, ".env.example")

    issues = []

    if not os.path.isfile(env_example_path):
        # No .env.example to validate against — pass
        return True, []

    if not os.path.isfile(env_path):
        issues.append(f".env file missing (expected at {env_path})")
        return False, issues

    try:
        with open(env_example_path, "r", encoding="utf-8") as f:
            example_keys = {
                line.split("=")[0].strip()
                for line in f
                if line.strip() and not line.startswith("#") and "=" in line
            }
        with open(env_path, "r", encoding="utf-8") as f:
            env_keys = {
                line.split("=")[0].strip()
                for line in f
                if line.strip() and not line.startswith("#") and "=" in line
            }
        missing = example_keys - env_keys
        if missing:
            issues.append(f"Missing keys from .env.example: {', '.join(sorted(missing))}")
    except (IOError, OSError) as e:
        issues.append(f"Error reading env files: {e}")

    return len(issues) == 0, issues


def validate_and_fix(project_dir: str, agent_data: dict) -> bool:
    """Validate env and attempt auto-fix where possible.

    G-13: This function must complete BEFORE _env_validated_project is set.

    Returns:
        True if validation passed (with or without fixes), False if still invalid.
    """
    is_valid, issues = validate_env_file(project_dir)

    if not is_valid:
        logger.warning(
            f"[ENV VALIDATOR] Validation issues: {'; '.join(issues)}"
        )
        # Attempt auto-fix: copy .env.example to .env if .env is missing
        env_path = os.path.join(project_dir, ".env")
        env_example_path = os.path.join(project_dir, ".env.example")
        if not os.path.isfile(env_path) and os.path.isfile(env_example_path):
            try:
                import shutil
                shutil.copy2(env_example_path, env_path)
                logger.info(
                    f"[ENV VALIDATOR] Auto-fixed: copied .env.example → .env"
                )
                return True
            except (IOError, OSError) as e:
                logger.warning(f"[ENV VALIDATOR] Auto-fix failed: {e}")
        return False

    return True


class EnvValidatorExtension(Extension):
    """G-13: Validate project .env before agent runs.

    Sets _env_validated_project ONLY after successful validation.
    """

    async def execute(self, loop_data=None, **kwargs):
        agent_data = self.agent.data

        project_dir = agent_data.get("_active_project_dir", "")
        if not project_dir or not os.path.isdir(project_dir):
            return

        already_validated = agent_data.get("_env_validated_project", False)
        if already_validated:
            return

        # G-13: Validation MUST complete before setting the flag
        validation_passed = validate_and_fix(project_dir, agent_data)

        # Set flag ONLY after validation completes (not before)
        agent_data["_env_validated_project"] = validation_passed

        if validation_passed:
            logger.info(
                f"[ENV VALIDATOR] Project env validated: {project_dir}"
            )
        else:
            logger.warning(
                f"[ENV VALIDATOR] Project env validation failed: {project_dir}"
            )
