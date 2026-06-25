from __future__ import annotations

import os
import sys
from pathlib import Path
import subprocess
from python.helpers import files
from python.helpers.print_style import PrintStyle


# this helper ensures that playwright is installed in /lib/playwright
# should work for both docker and local installation

def get_playwright_binary():
    pw_cache = Path(get_playwright_cache_dir())
    # Try multiple patterns for standard playwright and patchright
    patterns = (
        "**/chrome",                  # Linux chromium
        "**/headless_shell",         # Linux headless shell
        "**/chrome.exe",             # Windows
        "**/headless_shell.exe",     # Windows
    )
    for pattern in patterns:
        try:
            # Use rglob for deep search inside versioned folders
            binary = next(pw_cache.rglob(pattern), None)
            if binary and binary.is_file() and os.access(binary, os.X_OK):
                return str(binary.absolute())
        except Exception:
            continue
            
    # Fallback to /agix/tmp/playwright or /agix/tmp/playwright if not in data (legacy)
    for tmp_path in ["/agix/tmp/playwright", "/agix/tmp/playwright"]:
        tmp_cache = Path(tmp_path)
        if tmp_cache.exists():
            for pattern in patterns:
                try:
                    binary = next(tmp_cache.rglob(pattern), None)
                    if binary and binary.is_file() and os.access(binary, os.X_OK):
                        return str(binary.absolute())
                except Exception:
                    continue
    return None

def get_playwright_cache_dir():
    # Return path to playwright browsers cache in data directory
    if os.path.exists("/agix/data"):
        return "/agix/data/playwright"
    return "/agix/data/playwright"

def ensure_playwright_binary():
    bin = get_playwright_binary()
    if not bin:
        cache = get_playwright_cache_dir()
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = cache
        
        # Try patchright first as it's required for browser-use 0.5.x
        try:
            PrintStyle().info("Installing patchright chromium browser...")
            subprocess.check_call(
                [sys.executable, "-m", "patchright", "install", "chromium"],
                env=env
            )
        except Exception:
            # Fallback to standard playwright
            PrintStyle().info("Installing standard playwright chromium browser...")
            subprocess.check_call(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                env=env
            )
            
    bin = get_playwright_binary()
    if not bin:
        raise Exception("Playwright/Patchright binary not found after installation")
    return bin