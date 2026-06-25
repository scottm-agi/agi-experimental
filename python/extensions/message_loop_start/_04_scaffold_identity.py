"""
Scaffold Identity Cleanup — message_loop_start extension.

Runs at the start of the message loop (first iteration only).
Detects projects with generic scaffold names in package.json and renames
them to match the project directory name.

Common scaffold names that trigger cleanup:
- tmp_scaffold (default AGIX scaffold)
- vite-project (Vite default)
- my-app (CRA / Next.js default)
- next-app (Next.js default)

Universal fix: works for any project, never modifies source code.

Hooks into: message_loop_start (order 04)
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from python.helpers.extension import Extension

logger = logging.getLogger("agix.scaffold_identity")

# Generic scaffold names that should be renamed
SCAFFOLD_NAMES = frozenset({
    "tmp_scaffold",
    "vite-project",
    "my-app",
    "next-app",
    "my-next-app",
    "app",
    "starter",
})


class ScaffoldIdentity(Extension):
    """Auto-rename generic scaffold package names to match the project.

    One-shot: only runs on the first iteration, tracked via agent.data
    to prevent re-running on subsequent loops.
    """

    async def execute(self, loop_data: Any = None, **kwargs):
        # One-shot guard: only run once per agent lifecycle
        if self.agent.data.get("_scaffold_identity_done"):
            return

        self.agent.data["_scaffold_identity_done"] = True

        # Find project directory
        project_dir = (
            self.agent.data.get("_project_dir")
            or self.agent.data.get("_active_project_dir")
            or self.agent.data.get("_working_directory")
        )

        if not project_dir or not os.path.isdir(project_dir):
            return

        # Check for package.json
        pkg_path = os.path.join(project_dir, "package.json")
        if not os.path.isfile(pkg_path):
            return

        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pkg_data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"[SCAFFOLD IDENTITY] Could not read {pkg_path}: {e}")
            return

        current_name = pkg_data.get("name", "")
        if not current_name or current_name not in SCAFFOLD_NAMES:
            return

        # Derive new name from project directory
        new_name = os.path.basename(project_dir)
        # Sanitize to valid npm package name
        new_name = re.sub(r"[^a-z0-9\-_]", "-", new_name.lower())
        new_name = re.sub(r"-+", "-", new_name).strip("-")

        if not new_name or new_name == current_name:
            return

        # Rename
        pkg_data["name"] = new_name

        try:
            with open(pkg_path, "w", encoding="utf-8") as f:
                json.dump(pkg_data, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except OSError as e:
            logger.error(f"[SCAFFOLD IDENTITY] Could not write {pkg_path}: {e}")
            return

        logger.info(
            f"[SCAFFOLD IDENTITY] Renamed package '{current_name}' → '{new_name}' "
            f"in {pkg_path}"
        )
