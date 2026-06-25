"""
Tailwind CSS config validator — backward-compat validator module.

Checks whether a Tailwind CSS project has the required configuration file.
Supports Tailwind v3 (tailwind.config.js/ts) and v4 (CSS-first @import 'tailwindcss').
"""

import json
import os
import re
from typing import Optional


def check_tailwind_config(project_dir: str) -> Optional[dict]:
    """Check Tailwind CSS configuration presence.

    Rules:
    - If no package.json → return None (skip, not a node project)
    - If tailwindcss not in any deps → return None (skip, not using Tailwind)
    - If tailwindcss v4+ and globals.css contains `@import 'tailwindcss'` → pass (CSS-first)
    - If tailwind.config.ts or tailwind.config.js exists → pass
    - Otherwise → fail

    Returns:
        None  — not a Tailwind project (skip check)
        dict  — {
            'has_config': bool,
            'tailwind_version': str,
            'config_file': str | None,
            'is_v4_css_first': bool,
        }
    """
    pkg_path = os.path.join(project_dir, "package.json")
    if not os.path.isfile(pkg_path):
        return None

    try:
        with open(pkg_path, encoding="utf-8") as f:
            pkg = json.load(f)
    except Exception:
        return None

    # Collect all dependencies
    all_deps: dict = {}
    all_deps.update(pkg.get("dependencies", {}))
    all_deps.update(pkg.get("devDependencies", {}))
    all_deps.update(pkg.get("peerDependencies", {}))

    if "tailwindcss" not in all_deps:
        return None  # Not using Tailwind — skip check

    tailwind_version = all_deps["tailwindcss"].lstrip("^~>=")

    # Check for Tailwind v4 CSS-first config
    is_v4_css_first = False
    major = 0
    try:
        major = int(tailwind_version.split(".")[0])
    except Exception:
        pass

    if major >= 4:
        # v4 uses CSS-first config: `@import 'tailwindcss'` in a CSS file
        css_glob_dirs = [
            os.path.join(project_dir, "src", "app"),
            os.path.join(project_dir, "src"),
            project_dir,
        ]
        for css_dir in css_glob_dirs:
            if not os.path.isdir(css_dir):
                continue
            for fname in os.listdir(css_dir):
                if not fname.endswith(".css"):
                    continue
                try:
                    css_content = open(os.path.join(css_dir, fname), encoding="utf-8").read()
                    if re.search(r'@import\s+["\']tailwindcss["\']', css_content):
                        is_v4_css_first = True
                        break
                except Exception:
                    continue
            if is_v4_css_first:
                break

    if is_v4_css_first:
        return {
            "has_config": True,
            "tailwind_version": tailwind_version,
            "config_file": None,
            "is_v4_css_first": True,
        }

    # Check for v3-style config file
    config_file = None
    for candidate in ["tailwind.config.ts", "tailwind.config.js", "tailwind.config.mjs", "tailwind.config.cjs"]:
        full = os.path.join(project_dir, candidate)
        if os.path.isfile(full):
            config_file = candidate
            break

    return {
        "has_config": config_file is not None,
        "tailwind_version": tailwind_version,
        "config_file": config_file,
        "is_v4_css_first": False,
    }
