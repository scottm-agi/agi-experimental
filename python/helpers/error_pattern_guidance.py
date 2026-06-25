"""Error-pattern-specific guidance hints for supervisor and structural guards.

Classifies error text into known patterns and returns concrete, actionable
guidance (not generic "try different approach"). Used by:
- _get_signal_specific_guidance() in _45_intelligent_supervisor.py
- cross_delegation_spiral handler in the same file

All classification is DETERMINISTIC — regex pattern matching, no LLM calls.
"""

import re
from typing import Optional, Tuple

# Pattern → (category, specific_guidance) tuples
# Order matters: more specific patterns should come before more general ones
# to avoid false matches (e.g., TypeScript TS2339 before generic SyntaxError).
_ERROR_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # ── Module/package not found ──
    (re.compile(r'MODULE_NOT_FOUND|Cannot find module|ModuleNotFoundError', re.I),
     'missing_module',
     '📦 MISSING MODULE: Install the required package first. '
     'Run `npm install <package>` or `pip install <package>`. '
     'Check package.json/requirements.txt for the correct package name.'),

    # ── File not found ──
    (re.compile(r'ENOENT|FileNotFoundError|No such file', re.I),
     'file_not_found',
     '📁 FILE NOT FOUND: The file path is wrong. '
     'Use `ls` or `find` to locate the correct file. '
     'Check if the file was created in a previous phase.'),

    # ── TypeScript errors (before generic type/syntax errors) ──
    (re.compile(r'TS\d{4}:|Type.*is not assignable|has no exported member', re.I),
     'typescript_error',
     '📝 TYPESCRIPT ERROR: Type mismatch. Read the full error to find which types '
     'conflict. Fix the TYPE ANNOTATION, not the runtime code. Check interface '
     'definitions and import statements.'),

    # ── JavaScript/Python type errors ──
    (re.compile(r'TypeError.*is not a function|TypeError.*undefined|Property.*does not exist', re.I),
     'type_error',
     '🔧 TYPE ERROR: API/interface mismatch. Read the documentation or source code '
     'of the library you\'re calling. The function signature or return type is different '
     'from what you expect.'),

    # ── Connection refused ──
    (re.compile(r'ECONNREFUSED|Connection refused|connect ECONNREFUSED', re.I),
     'connection_refused',
     '🔌 CONNECTION REFUSED: The service (database/API/dev server) is not running. '
     'Start the service first, then retry. Check if it needs environment variables.'),

    # ── Permission denied ──
    (re.compile(r'EACCES|Permission denied|PermissionError', re.I),
     'permission_denied',
     '🔒 PERMISSION DENIED: File/directory permissions issue. '
     'Use chmod or run with correct permissions. Check file ownership.'),

    # ── Auth/API key errors ──
    (re.compile(r'ERR_INVALID_AUTH|401|Unauthorized|API.?key', re.I),
     'auth_error',
     '🔑 AUTH ERROR: API key is missing, invalid, or expired. '
     'Check environment variables. If the API key is genuinely unavailable, '
     'implement graceful degradation (mock data, fallback UI).'),

    # ── NPM errors ──
    (re.compile(r'npm ERR!|npm warn|ERESOLVE|peer dep', re.I),
     'npm_error',
     '📦 NPM ERROR: Package installation failed. Try: '
     '(1) `npm install --legacy-peer-deps`, '
     '(2) delete node_modules and package-lock.json, retry, '
     '(3) check for version conflicts in package.json.'),

    # ── Syntax/parse errors ──
    (re.compile(r'SyntaxError|Unexpected token|Parse error', re.I),
     'syntax_error',
     '⚠️ SYNTAX ERROR: The code has a syntax error. Read the FULL error message — '
     'it includes the file and line number. Fix the syntax, don\'t rewrite the whole file.'),

    # ── Build/compilation errors ──
    (re.compile(r'build failed|Build error|Compilation failed|webpack.*error', re.I),
     'build_error',
     '🏗️ BUILD ERROR: Fix the specific compilation error shown above. '
     'Common causes: missing imports, wrong file paths, incompatible packages. '
     'DO NOT restart the build without fixing the root cause.'),
]


def classify_error_pattern(error_text: str) -> Optional[Tuple[str, str]]:
    """Classify error text into a known pattern and return guidance.

    Args:
        error_text: The error text to classify (e.g., from health ledger
                    failure_reason or signal detail).

    Returns:
        (category, guidance) tuple, or None if no pattern matches.
    """
    if not error_text:
        return None
    for pattern, category, guidance in _ERROR_PATTERNS:
        if pattern.search(error_text):
            return (category, guidance)
    return None


def get_error_specific_guidance(error_text: str) -> str:
    """Get actionable guidance for an error, or empty string.

    Convenience wrapper around classify_error_pattern that returns
    just the guidance string (or "" if unrecognized).

    Args:
        error_text: The error text to look up guidance for.

    Returns:
        Non-empty guidance string if pattern matched, else "".
    """
    result = classify_error_pattern(error_text)
    if result:
        return result[1]
    return ""
