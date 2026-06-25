"""Env File Placeholder Validator — tool_execute_after extension.

Runs AFTER `code_execution_tool` and `write_to_file` invocations and
detects TWO classes of issues in `.env*` file writes:

1. **Template placeholders** (F2): `{{SECRET_OPENROUTER_API_KEY}}` patterns
   that cause 500 errors because API keys are literal template strings.
2. **Actual secret values** (F2b): Real API keys (sk-, re_, pplx-, AIza
   prefixes) written directly to .env files, risking secret leakage if
   the code is pushed to a repository.

Both detections are advisory — they return Response nudges without blocking.

Root Cause Reference:
  F2:  MSR Smoke Test 1777237623, entries 716-720
  F2b: Iter 159 deep audit — .env.local contained real API keys

Hooks into: tool_execute_after (order 23 — advisory, non-blocking)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, List, Optional

from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.universal_gate_budget import gate_check

logger = logging.getLogger("agix.env_placeholder_validator")

# Regex: detect {{...}} double-brace template patterns in content
_PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Z][A-Z0-9_]+)\}\}")

# Regex patterns for actual API key prefixes in env file VALUES
# Each pattern matches KEY=VALUE where VALUE starts with a known secret prefix.
# We anchor to '=' to avoid matching keys in URLs or other non-secret contexts.
_SECRET_VALUE_PATTERNS = [
    # OpenRouter / OpenAI (sk-or-v1-..., sk-proj-..., sk-...)
    re.compile(r"^([A-Z_]+)=sk-[a-zA-Z0-9][-a-zA-Z0-9_]{10,}", re.MULTILINE),
    # Resend (re_...)
    re.compile(r"^([A-Z_]+)=re_[a-zA-Z0-9]{8,}", re.MULTILINE),
    # Perplexity (pplx-...)
    re.compile(r"^([A-Z_]+)=pplx-[a-zA-Z0-9]{8,}", re.MULTILINE),
    # Google API Key (AIza...)
    re.compile(r"^([A-Z_]+)=AIza[a-zA-Z0-9_-]{20,}", re.MULTILINE),
    # Stripe secret keys (sk_live_..., sk_test_...)
    re.compile(r"^([A-Z_]+)=sk_(live|test)_[a-zA-Z0-9]{10,}", re.MULTILINE),
]

# File path patterns that indicate .env files
_ENV_FILE_PATTERNS = [
    re.compile(r"\.env$"),
    re.compile(r"\.env\.\w+$"),  # .env.local, .env.production, etc.
]

# Tool names that may write files
_FILE_WRITE_TOOLS = {"code_execution_tool", "code_execution", "write_to_file", "replace_in_file"}


def detect_env_placeholders(content: str) -> List[str]:
    """Detect {{PLACEHOLDER}} patterns in content.

    Args:
        content: File content to scan.

    Returns:
        List of matched placeholder names (e.g., ["SECRET_OPENROUTER_API_KEY"]).
    """
    if not content:
        return []
    return _PLACEHOLDER_PATTERN.findall(content)


def detect_secret_patterns(content: str) -> List[str]:
    """Detect actual API key patterns in .env content.

    Scans for known secret prefixes (sk-, re_, pplx-, AIza, sk_live_,
    sk_test_) in KEY=VALUE pairs.  Returns a list of var names that
    contain what appears to be a real secret.

    Args:
        content: .env file content to scan.

    Returns:
        List of environment variable names containing detected secrets
        (e.g., ["OPENROUTER_API_KEY", "RESEND_API_KEY"]).
    """
    if not content:
        return []
    found: List[str] = []
    for pattern in _SECRET_VALUE_PATTERNS:
        for match in pattern.finditer(content):
            var_name = match.group(1)
            if var_name not in found:
                found.append(var_name)
    return found


def is_env_file_path(path: str) -> bool:
    """Check if a file path refers to an .env file.

    Args:
        path: Absolute or relative file path.

    Returns:
        True if the path ends with .env or .env.<something>.
    """
    if not path:
        return False
    basename = os.path.basename(path)
    return any(p.search(basename) for p in _ENV_FILE_PATTERNS)


class EnvPlaceholderValidator(Extension):
    # Context-aware: code agents, write and execution tools
    PROFILES = {"code"}
    TOOLS = frozenset({"code_execution_tool", "code_execution", "write_to_file", "replace_in_file"})

    """Detect unresolved template placeholders in .env file writes."""

    async def execute(self, tool_name: str = "", **kwargs):
        if not tool_name:
            return None

        # Only inspect file-writing tools
        if tool_name.lower() not in _FILE_WRITE_TOOLS:
            return None

        tool_args = kwargs.get("tool_args", {})
        if not tool_args or not isinstance(tool_args, dict):
            return None

        # Determine what content was written and where
        content_to_check = ""
        target_path = ""

        if tool_name.lower() in ("code_execution_tool", "code_execution"):
            # Extract code and look for .env file write patterns
            code = tool_args.get("code", "")
            if not code:
                return None

            # Look for file paths in the code that indicate .env writes
            env_path_match = re.search(r"[\s>]+([\S]*\.env[\S]*)", code)
            if not env_path_match:
                return None

            target_path = env_path_match.group(1).strip("'\"")
            if not is_env_file_path(target_path):
                return None

            # Check if the content actually exists on disk
            if os.path.exists(target_path):
                try:
                    with open(target_path, "r") as f:
                        content_to_check = f.read()
                except Exception:
                    # Fall back to checking the code itself
                    content_to_check = code
            else:
                content_to_check = code

        elif tool_name.lower() in ("write_to_file", "replace_in_file"):
            target_path = tool_args.get("target_file", "") or tool_args.get("path", "")
            if not is_env_file_path(target_path):
                return None
            content_to_check = tool_args.get("new_string", "") or tool_args.get("content", "")

        if not content_to_check:
            return None

        # Detect placeholders (F2) and actual secrets (F2b)
        placeholders = detect_env_placeholders(content_to_check)
        secrets = detect_secret_patterns(content_to_check)

        if not placeholders and not secrets:
            return None

        # Escape hatch — prevent infinite blocking loops
        if gate_check(self.agent.data, "env_placeholder_validator"):
            return None

        # Build advisory warning
        parts: List[str] = []

        if placeholders:
            placeholder_list = "\n".join(f"  - `{{{{{p}}}}}`" for p in placeholders)
            parts.append(
                f"⚠️ **Env Placeholder Warning**\n\n"
                f"The `.env` file `{os.path.basename(target_path)}` contains "
                f"**{len(placeholders)} unresolved template placeholder(s)**:\n\n"
                f"{placeholder_list}\n\n"
                f"These `{{{{SECRET_...}}}}` patterns are template syntax — they will "
                f"NOT work as real API keys.\n\n"
                f"**Action Required:**\n"
                f"1. Use `secret_set` tool to store the actual secret values\n"
                f"2. Use `secret_get` to retrieve them when writing .env files\n"
                f"3. Or ask the user for the real API key values\n"
            )
            logger.warning(
                f"[ENV_PLACEHOLDER] {self.agent.agent_name}: "
                f"Detected {len(placeholders)} placeholder(s) in {target_path}: "
                f"{', '.join(placeholders)}"
            )

        if secrets:
            secret_list = "\n".join(f"  - `{s}`" for s in secrets)
            parts.append(
                f"🔐 **Secret Leakage Advisory**\n\n"
                f"The `.env` file `{os.path.basename(target_path)}` contains "
                f"**{len(secrets)} variable(s) with actual API key values**:\n\n"
                f"{secret_list}\n\n"
                f"Real API keys in `.env` files risk leakage if the project is "
                f"pushed to a repository.\n\n"
                f"**Recommendation:**\n"
                f"1. Use `secret_set` to store keys in the vault\n"
                f"2. Reference them via `secret_get` in your .env generation\n"
                f"3. Ensure `.env*` is in `.gitignore`\n"
            )
            logger.warning(
                f"[ENV_SECRET_LEAK] {self.agent.agent_name}: "
                f"Detected {len(secrets)} actual secret(s) in {target_path}: "
                f"{', '.join(secrets)}"
            )

        msg = "\n".join(parts)

        # Counter already incremented by gate_check above
        return Response(
            message=msg,
            break_loop=False,
        )
