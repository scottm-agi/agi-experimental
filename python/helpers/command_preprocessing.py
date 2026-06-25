"""
Pure utility functions for command preprocessing in code_execution.

Extracted to a standalone module to enable unit testing without heavy
dependencies (paramiko, tty_session, etc.) that code_execution.py requires.

RCA-238: Auto-injection of -y for npx commands.
RCA-ITR49: Auto-injection of CI=true and missing non-interactive flags for
scaffold commands (create-next-app, create-vite, etc.) to prevent interactive
prompts from stalling agents.
"""
from __future__ import annotations

import re

# ── RCA-238 Fix A: Auto-inject -y for npx commands ──
# Prevents "Need to install the following packages... Ok to proceed? (y)"
# dialogs from stalling agents. Even if the agent forgets --yes,
# this safety net ensures npx commands auto-accept package installs.
_NPX_WITHOUT_YES = re.compile(r'\bnpx\s+(?!-y\b)(?!--yes\b)')


def inject_npx_yes(command: str) -> str:
    """Auto-inject -y flag for npx commands that don't already have it.
    
    Prevents interactive "Ok to proceed?" prompts from stalling agents.
    Handles both simple and compound commands (e.g., cd /dir && npx ...).
    
    If npx already has -y or --yes, the command is returned unchanged.
    
    Args:
        command: Shell command to potentially modify.
    
    Returns:
        Command with -y injected after npx if applicable, otherwise unchanged.
    """
    if not command:
        return command
    if 'npx' not in command:
        return command
    # Replace `npx ` with `npx -y ` where -y/--yes is not already present
    return _NPX_WITHOUT_YES.sub('npx -y ', command)


# ── RCA-ITR49: Non-interactive scaffold enforcement ──
# Known scaffold commands that support interactive prompts.
# For each, we define the flags that MUST be present to skip all prompts.
# Format: {regex_pattern: [(flag_to_check, flag_to_inject), ...]}

# Matches `create-next-app` with optional version suffix
_CREATE_NEXT_APP = re.compile(r'\bcreate-next-app(@\S+)?\b')
# Matches `create-vite` with optional version suffix
_CREATE_VITE = re.compile(r'\bcreate-vite(@\S+)?\b')
# Matches `create-react-app` with optional version suffix
_CREATE_REACT_APP = re.compile(r'\bcreate-react-app(@\S+)?\b')

# Known scaffold patterns and their required non-interactive flags
_SCAFFOLD_FLAGS: list[tuple[re.Pattern, list[tuple[str, str]]]] = [
    # create-next-app: must have --turbopack or --no-turbopack (v15+)
    # --yes skips "saved preferences" but NOT the turbopack prompt
    (_CREATE_NEXT_APP, [
        ("--turbopack", "--turbopack"),  # If neither present, inject --turbopack
    ]),
]


def inject_scaffold_flags(command: str) -> str:
    """Auto-inject missing non-interactive flags for scaffold commands.
    
    When agents run scaffold commands like `npx create-next-app`, they may
    forget flags that suppress interactive prompts. This function detects
    known scaffold commands and injects any missing required flags.
    
    Args:
        command: Shell command to potentially modify.
    
    Returns:
        Command with missing scaffold flags injected.
    """
    if not command:
        return command
    
    for pattern, flags in _SCAFFOLD_FLAGS:
        if not pattern.search(command):
            continue
        for check_flag, inject_flag in flags:
            # If neither the flag nor its --no- variant is present, inject it
            no_variant = check_flag.replace("--", "--no-")
            if check_flag not in command and no_variant not in command:
                # Append the flag at the end of the command
                command = command.rstrip() + f" {inject_flag}"
    
    return command


# ── RCA-ITR49: CI=true for all scaffold commands ──
# `CI=true` is the UNIVERSAL non-interactive switch. Most Node.js CLI tools
# (including prompts, inquirer, @clack/prompts) check process.env.CI and
# skip interactive prompts when it's set. This catches ALL current and
# FUTURE interactive prompts without needing per-flag maintenance.

_SCAFFOLD_PATTERNS = [
    re.compile(r'\bcreate-next-app\b'),
    re.compile(r'\bcreate-vite\b'),
    re.compile(r'\bcreate-react-app\b'),
    re.compile(r'\bcreate-remix\b'),
    re.compile(r'\bcreate-astro\b'),
    re.compile(r'\bcreate-svelte\b'),
    re.compile(r'\bcreate-t3-app\b'),
    re.compile(r'\bshadcn\b'),
    re.compile(r'\bnpx\s+-y?\s*init\b'),
]


def inject_ci_env(command: str) -> str:
    """Prepend CI=true to scaffold commands that don't already have it.
    
    CI=true is the universal signal that suppresses interactive prompts
    in Node.js CLI tools. This is the broadest safety net — it catches
    prompts that we don't have specific flag overrides for.
    
    Args:
        command: Shell command to potentially modify.
    
    Returns:
        Command with CI=true prepended if it's a scaffold command.
    """
    if not command:
        return command
    
    # Already has CI= set
    if re.search(r'\bCI\s*=', command):
        return command
    
    # Check if any scaffold pattern matches
    for pattern in _SCAFFOLD_PATTERNS:
        if pattern.search(command):
            # Handle compound commands: cd /path && npx create-...
            # We need to inject CI=true right before the npx/scaffold command
            # For simple commands: CI=true npx create-next-app ...
            # For compound: cd /path && CI=true npx create-next-app ...
            
            # Find the last && or ; before the scaffold command
            match = pattern.search(command)
            if match:
                pos = match.start()
                # Walk backwards to find && or ; or start of string
                prefix = command[:pos]
                rest = command[pos:]
                
                # Check if there's a cd && prefix
                last_chain = max(prefix.rfind('&&'), prefix.rfind(';'))
                if last_chain >= 0:
                    # Insert CI=true after the chain operator
                    before_chain = command[:last_chain + 2].rstrip()
                    after_chain = command[last_chain + 2:].lstrip()
                    return f"{before_chain} CI=true {after_chain}"
                else:
                    # Simple command — prepend CI=true
                    return f"CI=true {command}"
    
    return command


def preprocess_command(command: str) -> str:
    """Apply all command preprocessing steps in the correct order.
    
    This is the single entry point for all command preprocessing.
    Call this instead of individual functions.
    
    Order matters:
    1. inject_npx_yes — ensure -y flag for npx
    2. inject_ci_env — set CI=true for scaffold commands
    3. inject_scaffold_flags — add missing per-tool flags
    
    Args:
        command: Raw shell command from the agent.
    
    Returns:
        Preprocessed command ready for execution.
    """
    command = inject_npx_yes(command)
    command = inject_ci_env(command)
    command = inject_scaffold_flags(command)
    return command


# ── ADR-82: Dual-mode shell auto-detection ──
# Commands that should NEVER run with interactive stdin.
# These are build/install/scaffold commands that may prompt but
# should always be run non-interactively (stdin=/dev/null).
_FORCE_NON_INTERACTIVE_PATTERNS = [
    re.compile(r'\bcreate-next-app\b'),
    re.compile(r'\bcreate-vite\b'),
    re.compile(r'\bcreate-react-app\b'),
    re.compile(r'\bcreate-remix\b'),
    re.compile(r'\bcreate-astro\b'),
    re.compile(r'\bcreate-svelte\b'),
    re.compile(r'\bcreate-t3-app\b'),
    re.compile(r'\bnpm\s+(install|ci|run\s+\S+|test|build|lint)\b'),
    re.compile(r'\bnpx\s+'),
    re.compile(r'\bpip3?\s+install\b'),
    re.compile(r'\bcargo\s+(build|install|test|run)\b'),
    re.compile(r'\bmake\b'),
    re.compile(r'\bgit\s+(clone|pull|push|fetch|checkout|merge|rebase|reset|stash)\b'),
    re.compile(r'\bapt-get\s+'),
    re.compile(r'\bprisma\s+(generate|migrate|db\s+push)\b'),
    re.compile(r'\bshadcn\b'),
]

# Commands that should be allowed to run interactively.
# These are legitimate interactive use cases where the agent
# needs to use the input tool to send keystrokes.
_INTERACTIVE_ALLOWLIST_PATTERNS = [
    re.compile(r'\bpsql\b'),
    re.compile(r'\bmysql\b'),
    re.compile(r'\bredis-cli\b'),
    re.compile(r'\bpython3?\s+-i\b'),
    re.compile(r'^node\s*$'),       # bare 'node' = REPL
    re.compile(r'\bgdb\b'),
    re.compile(r'\bpdb\b'),
    re.compile(r'\bsqlite3\b'),
    re.compile(r'\bmongo\b'),
]


def should_force_non_interactive(command: str) -> bool:
    """Determine if a command should be FORCED to non-interactive mode.
    
    This is the safety net that prevents scaffold/build commands from
    running with interactive stdin even if the agent explicitly requests
    interactive=True. Used in dual-mode shell routing (ADR-82).
    
    Returns True if the command should ALWAYS run non-interactively.
    Returns False if the command is safe to run interactively.
    
    Args:
        command: Shell command to check.
    
    Returns:
        True if the command should be forced non-interactive.
    """
    if not command:
        return False
    
    # Check allowlist first — these are always safe for interactive
    for pat in _INTERACTIVE_ALLOWLIST_PATTERNS:
        if pat.search(command):
            return False
    
    # Check force-non-interactive patterns
    for pat in _FORCE_NON_INTERACTIVE_PATTERNS:
        if pat.search(command):
            return True
    
    return False

