"""
Tool Error Pattern Registry — Domain-Specific Error Classification

Maps regex patterns to specific fix suggestions for known tool errors.
Used by the Tool Failure Tracker to inject TARGETED remediation hints
instead of generic "try something different" messages.

This eliminates spin loops where agents retry the same failing command
because the generic hint doesn't tell them WHAT is wrong or HOW to fix it.

Extension point: Add new entries to ERROR_PATTERN_REGISTRY to support
additional tools, frameworks, and error classes.

Created: 2026-04-19 (Iteration 10 — LaunchPad Deep Audit)
Root Cause: RCA #1 + RCA #5 — No error classification layer
"""

from __future__ import annotations

import re
from python.helpers.output_truncation import truncate_output_middle_out
from typing import Any, Dict, List, Optional

# ─── Pattern Registry ────────────────────────────────────────────────
# Structure: category → list of {pattern, fix, severity}
# Patterns are compiled once at module load for performance.
# Order within a category matters: more specific patterns should come FIRST.

ERROR_PATTERN_REGISTRY: Dict[str, List[Dict[str, Any]]] = {
    "prisma": [
        {
            "pattern": re.compile(
                r"datasource\.url property is required", re.IGNORECASE
            ),
            "fix": (
                "Add `url = env(\"DATABASE_URL\")` to the datasource block in "
                "schema.prisma. Ensure your .env file has "
                "DATABASE_URL=file:./dev.db (for SQLite) or the correct "
                "connection string for your provider."
            ),
            "severity": "critical",
        },
        {
            "pattern": re.compile(
                r"Cannot find module ['\"]@prisma/client['\"]|prisma generate.*not found",
                re.IGNORECASE,
            ),
            "fix": (
                "Run `npx prisma generate` to generate the Prisma Client "
                "before importing it. This must be done after every "
                "schema.prisma change."
            ),
            "severity": "high",
        },
        {
            "pattern": re.compile(
                r"Unique constraint failed|Unique constraint violation",
                re.IGNORECASE,
            ),
            "fix": (
                "A database record with this unique value already exists. "
                "Use upsert instead of create, or check for existing records first."
            ),
            "severity": "medium",
        },
    ],
    "npm": [
        {
            "pattern": re.compile(
                r"ERESOLVE.*peer dep|peer dependency conflict|could not resolve",
                re.IGNORECASE,
            ),
            "fix": (
                "Peer dependency conflict. Run with --legacy-peer-deps flag: "
                "`npm install --legacy-peer-deps`. If that fails, pin the "
                "conflicting package to a compatible version."
            ),
            "severity": "high",
        },
        {
            "pattern": re.compile(
                r"ERR! code ENOENT.*package\.json|ENOENT.*no such file.*package\.json",
                re.IGNORECASE | re.DOTALL,
            ),
            "fix": (
                "No package.json found in the current directory. Verify you are "
                "in the project root. Run `ls package.json` to confirm, or "
                "run `npm init -y` to create one."
            ),
            "severity": "critical",
        },
        {
            "pattern": re.compile(
                r"ERR!.*404.*Not Found|ERR!.*Registry returned 404",
                re.IGNORECASE,
            ),
            "fix": (
                "Package not found in the npm registry. Check the package name "
                "for typos. The package may have been renamed or deprecated."
            ),
            "severity": "high",
        },
        {
            "pattern": re.compile(
                r"EACCES.*permission denied.*npm",
                re.IGNORECASE,
            ),
            "fix": (
                "Permission denied. Do NOT use sudo. Fix npm permissions: "
                "`mkdir ~/.npm-global && npm config set prefix '~/.npm-global'`"
            ),
            "severity": "high",
        },
    ],
    "typescript": [
        {
            "pattern": re.compile(
                r"Cannot find module ['\"]@/",
                re.IGNORECASE,
            ),
            "fix": (
                "TypeScript path alias '@/' not resolved. Check tsconfig.json: "
                "ensure baseUrl is '.' and paths has '\"@/*\": [\"./src/*\"]'. "
                "For Next.js, this should also be in tsconfig.json under "
                "compilerOptions."
            ),
            "severity": "high",
        },
        {
            "pattern": re.compile(
                r"TS\d+:.*Type.*is not assignable to type",
                re.IGNORECASE,
            ),
            "fix": (
                "TypeScript type mismatch. Check the expected type signature "
                "and ensure your value conforms to it. Add explicit type "
                "annotations if inference is failing."
            ),
            "severity": "medium",
        },
    ],
    "shell": [
        {
            "pattern": re.compile(
                r"syntax error near unexpected token.*heredoc|"
                r"unexpected EOF while looking for.*heredoc|"
                r"here-document.*delimited by end-of-file",
                re.IGNORECASE,
            ),
            "fix": (
                "Heredoc syntax error. Heredoc delimiters (EOF) must NOT be "
                "indented. Use `echo 'content' > file` or `cat > file << 'EOF'` "
                "with 'EOF' flush-left on its own line. Alternatively, use "
                "printf or a series of echo commands."
            ),
            "severity": "critical",
        },
        {
            "pattern": re.compile(
                r"command not found:\s*\S+",
                re.IGNORECASE,
            ),
            "fix": (
                "Command not found. Check if the binary is installed. For npm "
                "binaries, use `npx <command>` instead. For system binaries, "
                "check if the package needs to be installed via apt/apk."
            ),
            "severity": "high",
        },
    ],
    "nextjs": [
        {
            "pattern": re.compile(
                r"You're importing a component that needs.*useState|"
                r"React Hook.*cannot be called at the top level|"
                r"only works in a Client Component",
                re.IGNORECASE,
            ),
            "fix": (
                "This file uses React hooks (useState, useEffect, etc.) but "
                "is treated as a Server Component. Add `'use client'` as the "
                "FIRST line of the file (before any imports)."
            ),
            "severity": "high",
        },
        {
            "pattern": re.compile(
                r"Module not found:.*Can't resolve",
                re.IGNORECASE,
            ),
            "fix": (
                "Missing npm package or incorrect import path. Run "
                "`npm install <package-name>` for external deps, or check "
                "the relative/absolute path for local modules."
            ),
            "severity": "high",
        },
        {
            "pattern": re.compile(
                r"Error:.*NEXT_NOT_FOUND|.*notFound\(\).*not exported",
                re.IGNORECASE,
            ),
            "fix": (
                "Import `notFound` from 'next/navigation' (App Router) not "
                "'next/router' (Pages Router)."
            ),
            "severity": "medium",
        },
    ],
    "python": [
        {
            "pattern": re.compile(
                r"ModuleNotFoundError: No module named",
                re.IGNORECASE,
            ),
            "fix": (
                "Python module not found. Install it with `pip install <module>` "
                "or check if the module path is correct. For local modules, "
                "ensure __init__.py exists in the package directory."
            ),
            "severity": "high",
        },
        {
            "pattern": re.compile(
                r"IndentationError:",
                re.IGNORECASE,
            ),
            "fix": (
                "Python indentation error. Check for mixed tabs/spaces. Use "
                "4 spaces consistently. The error line number points to where "
                "Python first detected the inconsistency."
            ),
            "severity": "high",
        },
    ],
    "docker": [
        {
            "pattern": re.compile(
                r"port is already allocated|address already in use",
                re.IGNORECASE,
            ),
            "fix": (
                "Port conflict. Another process is using this port. Find and "
                "kill it: `lsof -i :<port> | grep LISTEN` then "
                "`kill -9 <PID>`, or use a different port."
            ),
            "severity": "high",
        },
    ],
    # RCA-301 Issue 7: ESM/CJS execution context mismatch
    # code_execution_tool's nodejs runtime uses vm.createContext() with CJS
    # require(). ESM `import` statements are syntactically invalid there.
    "esm_cjs": [
        {
            "pattern": re.compile(
                r"Cannot use import statement outside a module|"
                r"SyntaxError.*import\s+statement|"
                r"ERR_REQUIRE_ESM|"
                r"require\(\) of ES Module",
                re.IGNORECASE,
            ),
            "fix": (
                "ESM/CJS MISMATCH: The code_execution_tool Node.js runtime "
                "uses CommonJS (require/module.exports). ESM `import` "
                "statements are NOT supported. Options:\n"
                "1. Use `require()` instead of `import` for REPL testing\n"
                "2. Write the code to a .ts file using `write_to_file` and "
                "run with `npx tsx filename.ts` via `terminal` runtime\n"
                "3. For application code: write directly to the project "
                "file -- do NOT test in the REPL"
            ),
            "severity": "critical",
        },
    ],
    # RCA-301 Issue 5: API authentication / rate-limit errors
    "api_auth": [
        {
            "pattern": re.compile(
                r"HTTP\s*(?:status\s*)?(?:code\s*)?(?:401|403)|"
                r"Unauthorized|Forbidden|invalid.?api.?key|"
                r"authentication.?required",
                re.IGNORECASE,
            ),
            "fix": (
                "API AUTHENTICATION ERROR: The request was rejected due to "
                "invalid or missing credentials. Check:\n"
                "1. API key / token is set in environment variables\n"
                "2. The key has not expired or been revoked\n"
                "3. NEVER retry with the same credentials -- switch to a "
                "fallback service or report the issue"
            ),
            "severity": "critical",
        },
        {
            "pattern": re.compile(
                r"HTTP\s*(?:status\s*)?(?:code\s*)?429|"
                r"rate.?limit|too.?many.?requests|quota.?exceeded",
                re.IGNORECASE,
            ),
            "fix": (
                "RATE LIMIT: The API is throttling requests. Do NOT retry "
                "immediately. Switch to a fallback service (e.g., "
                "Perplexity -> Tavily -> Web Search) or wait and reduce "
                "request frequency."
            ),
            "severity": "high",
        },
    ],
}


def classify_error(output: Optional[str]) -> Optional[Dict[str, str]]:
    """Classify tool output against known error patterns (L1 regex).

    Scans the output text against all registered patterns, returning the
    first match with its category and targeted fix suggestion.

    Args:
        output: The tool's stdout/stderr output text.

    Returns:
        Dict with "category", "fix", "severity" keys if a pattern matches.
        None if no known pattern matches (caller should use generic hint).
    """
    if not output:
        return None

    for category, patterns in ERROR_PATTERN_REGISTRY.items():
        for entry in patterns:
            if entry["pattern"].search(output):
                return {
                    "category": category,
                    "fix": entry["fix"],
                    "severity": entry.get("severity", "medium"),
                }

    return None


async def classify_error_l2(
    output: str,
    tool_name: str = "",
    agent_profile: str = "",
) -> Optional[Dict[str, str]]:
    """P2-1: L2 LLM-based error classification for unknown errors.

    When L1 regex (classify_error) returns None, this function uses a
    lightweight LLM call to classify the error and suggest a fix.

    2-layer architecture: L1 regex is fast and handles known patterns.
    L2 LLM handles novel/unseen errors with semantic understanding.

    Args:
        output: The tool's error output (truncated to 500 chars internally).
        tool_name: The tool that produced the error.
        agent_profile: The agent's profile (for profile-aware suggestions).

    Returns:
        Dict with "category", "fix", "severity" if LLM classifies successfully.
        None if LLM call fails or times out.
    """
    import logging
    _logger = logging.getLogger("agix.tool_error_patterns.l2")

    if not output or len(output.strip()) < 10:
        return None

    try:
        from python.helpers.llm_utils import lightweight_llm_call
        import json

        truncated = truncate_output_middle_out(output, max_chars=500, head_ratio=0.3)
        system_prompt = (
            "You are a build/tool error classifier. Given a tool error output, "
            "classify it into a category and suggest a specific fix.\n\n"
            "Respond with ONLY valid JSON:\n"
            '{"category": "<one of: build, runtime, config, dependency, auth, network, syntax, permission, unknown>",\n'
            ' "fix": "<specific actionable fix in 1-2 sentences>",\n'
            ' "severity": "<critical|high|medium|low>"}\n\n'
            "Be specific. Don't say 'check your code'. Say exactly what to do."
        )
        user_msg = f"Tool: {tool_name}\nProfile: {agent_profile}\nError output:\n```\n{truncated}\n```"

        response = await lightweight_llm_call(
            system_message=system_prompt,
            user_message=user_msg,
            timeout=10,
            agix_retry_attempts=1,
        )

        result = json.loads(response.strip())
        return {
            "category": str(result.get("category", "unknown")),
            "fix": str(result.get("fix", "Review the error output carefully")),
            "severity": str(result.get("severity", "medium")),
        }
    except Exception as e:
        _logger.debug(f"L2 error classification failed: {e}")
        return None

