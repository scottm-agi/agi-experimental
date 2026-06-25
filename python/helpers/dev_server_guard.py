"""
Dev server routing guard (P2).

Intercepts dev server start commands in code_execution_tool and redirects
agents to use services_mgt instead. This ensures:
- Proper port allocation (5100-5500 range)
- Correct host binding (0.0.0.0 for Docker accessibility)
- Health check verification after startup
- Service state tracking for restart/stop operations

The guard returns a redirect message (not None) when a dev server command
is detected. Returns None for all other commands (pass-through).
"""
import re
from typing import Optional


# Patterns that match dev server start commands
_DEV_SERVER_PATTERNS = [
    # npm run dev / npm run start / npm run serve
    re.compile(r'\bnpm\s+run\s+(dev|start|serve)\b'),
    # npm start (built-in)
    re.compile(r'\bnpm\s+start\b'),
    # yarn dev / yarn start / yarn serve (shorthand scripts)
    re.compile(r'\byarn\s+(dev|start|serve)\b'),
    # yarn run dev / yarn run start
    re.compile(r'\byarn\s+run\s+(dev|start|serve)\b'),
    # pnpm run dev / pnpm dev
    re.compile(r'\bpnpm\s+(run\s+)?(dev|start|serve)\b'),
    # npx next dev / npx nuxt dev
    re.compile(r'\bnpx\s+(next|nuxt)\s+dev\b'),
    # npx vite (starts dev server)
    re.compile(r'\bnpx\s+vite\b(?!\s+build)'),
    # Direct next dev / vite (without npx)
    re.compile(r'\bnext\s+dev\b'),
    re.compile(r'\bvite\b(?!\s+build)(?!\s+preview)'),
]

# Patterns that should NOT be intercepted even if they contain
# a dev-server-like substring
_DEV_SERVER_EXCLUDE_PATTERNS = [
    # npx create-next-app, npx create-vite, etc. (init commands)
    re.compile(r'\bnpx\s+create-'),
    # npm init / npm create
    re.compile(r'\bnpm\s+(init|create)\b'),
    # yarn create
    re.compile(r'\byarn\s+create\b'),
]


def guard_dev_server_command(command: Optional[str]) -> Optional[str]:
    """Detect dev server start commands and return redirect message.

    Returns a redirect message string if the command is a dev server start
    (telling the agent to use services_mgt instead).
    Returns None if the command should be allowed through.

    Args:
        command: Shell command to check.

    Returns:
        Redirect message if intercepted, None if allowed.
    """
    if not command:
        return None

    # Check exclusions first (create-next-app, npm init, etc.)
    for pat in _DEV_SERVER_EXCLUDE_PATTERNS:
        if pat.search(command):
            return None

    # Check if command contains a dev server pattern
    matched = False
    for pat in _DEV_SERVER_PATTERNS:
        if pat.search(command):
            matched = True
            break

    if not matched:
        return None

    # Build redirect message
    return (
        f"⚠️ **DEV SERVER ROUTING GUARD**: The command `{command}` starts a dev server.\n\n"
        f"You MUST use `services_mgt` instead of `code_execution_tool` for dev servers.\n"
        f"`services_mgt` handles port allocation (5100-5500), host binding (0.0.0.0),\n"
        f"health checks, and service state tracking.\n\n"
        f"Use this instead:\n"
        f"```json\n"
        f'{{\n'
        f'  "tool_name": "services_mgt",\n'
        f'  "tool_args": {{\n'
        f'    "action": "start_service",\n'
        f'    "command": "{command}",\n'
        f'    "name": "dev-server"\n'
        f'  }}\n'
        f'}}\n'
        f"```\n\n"
        f"The port will be auto-allocated. Do NOT run dev servers via code_execution_tool."
    )
