"""
Config Coherence Validator — S5 Pipeline Hardening.

Validates that environment variables match package.json dependencies.
Detects missing env vars for database drivers, API keys, etc.
"""
from __future__ import annotations

import json
import os
import re
import glob
import logging
from typing import Any

from python.helpers.project_scan_constants import DEFAULT_PROJECT_SKIP_DIRS
from python.helpers.source_scanner import list_project_files, read_project_files

logger = logging.getLogger("agix.config_coherence")

# Map of package names → required env vars
_DEP_ENV_REQUIREMENTS: dict[str, list[str]] = {
    "@prisma/client": ["DATABASE_URL"],
    "prisma": ["DATABASE_URL"],
    "@supabase/supabase-js": ["SUPABASE_URL", "SUPABASE_ANON_KEY"],
    "mongoose": ["MONGODB_URI"],
    "pg": ["DATABASE_URL"],
    "stripe": ["STRIPE_SECRET_KEY"],
    "@clerk/nextjs": ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "CLERK_SECRET_KEY"],
    "next-auth": ["NEXTAUTH_SECRET", "NEXTAUTH_URL"],
    "@auth/core": ["AUTH_SECRET"],
    "resend": ["RESEND_API_KEY"],
    "nodemailer": ["SMTP_HOST"],
}

# Source code patterns → required env vars
_CODE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"openrouter\.ai", re.IGNORECASE), "OPENROUTER_API_KEY"),
    (re.compile(r"OPENAI_API_KEY|openai\.com/v1", re.IGNORECASE), "OPENAI_API_KEY"),
    (re.compile(r"anthropic\.com", re.IGNORECASE), "ANTHROPIC_API_KEY"),
]


def validate_config_coherence(project_dir: str) -> list[dict[str, Any]]:
    """Validate env vars match package.json deps and source code patterns.

    Args:
        project_dir: Root directory of the project.

    Returns:
        List of findings, each with 'type' and 'detail' keys.
        Empty list if everything is consistent.
    """
    findings: list[dict[str, Any]] = []

    # Read package.json
    pkg_path = os.path.join(project_dir, "package.json")
    if not os.path.isfile(pkg_path):
        return findings

    try:
        with open(pkg_path, "r") as f:
            pkg = json.load(f)
    except (IOError, json.JSONDecodeError):
        return findings

    # Collect all deps
    all_deps = {}
    all_deps.update(pkg.get("dependencies", {}))
    all_deps.update(pkg.get("devDependencies", {}))

    # Collect existing env vars
    env_vars = _collect_env_vars(project_dir)

    # Check dep → env requirements
    for dep_name, required_vars in _DEP_ENV_REQUIREMENTS.items():
        if dep_name in all_deps:
            for var in required_vars:
                if var not in env_vars:
                    findings.append({
                        "type": "missing_env",
                        "detail": (
                            f"Package `{dep_name}` requires `{var}` "
                            f"but it's not defined in .env.local or .env"
                        ),
                    })

    # Check source code patterns
    # OVL-3: Use centralized scanner instead of inline os.walk
    src_dir = os.path.join(project_dir, "src")
    if os.path.isdir(src_dir):
        _valid_exts = {".ts", ".tsx", ".js", ".jsx"}
        src_files = read_project_files(src_dir, extensions=_valid_exts)
        for _rel_path, content in src_files.items():
            for pattern, required_var in _CODE_PATTERNS:
                if pattern.search(content) and required_var not in env_vars:
                    findings.append({
                        "type": "missing_env",
                        "detail": (
                            f"Source code references `{pattern.pattern}` "
                            f"but `{required_var}` is not in env"
                        ),
                    })
                    break  # One finding per pattern per project

    if findings:
        logger.warning(
            f"[CONFIG COHERENCE] Found {len(findings)} issue(s) in {project_dir}"
        )

    return findings


def _collect_env_vars(project_dir: str) -> set[str]:
    """Collect all env var names from .env files."""
    env_vars: set[str] = set()

    for env_file in (".env", ".env.local", ".env.example"):
        path = os.path.join(project_dir, env_file)
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key = line.split("=", 1)[0].strip()
                            if key:
                                env_vars.add(key)
            except IOError:
                pass

    return env_vars


# ─── RCA-262: Env Var Value Verification ──────────────────────────────

def collect_env_var_values(project_dir: str) -> dict[str, str]:
    """Collect all env var key-value pairs from .env files.

    Unlike _collect_env_vars (which returns only names), this returns
    key→value pairs so callers can verify values are non-placeholder.

    Merges .env, .env.local, .env.example (later files override earlier).

    Args:
        project_dir: Root directory of the project.

    Returns:
        Dict mapping env var names to their string values.
    """
    env_vars: dict[str, str] = {}

    for env_file in (".env", ".env.local", ".env.example"):
        path = os.path.join(project_dir, env_file)
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, _, value = line.partition("=")
                            key = key.strip()
                            value = value.strip()
                            if key:
                                env_vars[key] = value
            except IOError:
                pass

    return env_vars


# Patterns that indicate a placeholder value (not a real config)
_PLACEHOLDER_PATTERNS = re.compile(
    r'^(?:'
    r'your[_-].*[_-]here'       # your-api-key-here
    r'|xxx+'                    # xxx
    r'|TODO'                    # TODO
    r'|CHANGEME'                # CHANGEME
    r'|replace[_-]?me'          # replace_me, replaceme
    r'|FIXME'                   # FIXME
    r'|INSERT[_-]?HERE'         # INSERT_HERE
    r'|put[_-].*[_-]here'       # put-key-here
    r'|#'                       # fallback href '#'
    r')$',
    re.IGNORECASE,
)


def detect_placeholder_env_vars(env_vars: dict[str, str]) -> list[str]:
    """Detect env vars with placeholder or empty values.

    Args:
        env_vars: Dict of env var name → value (from collect_env_var_values).

    Returns:
        List of env var names that have placeholder/empty values.
    """
    placeholders: list[str] = []

    for key, value in env_vars.items():
        # Empty value is always a placeholder
        if not value:
            placeholders.append(key)
            continue

        # Check against placeholder patterns
        if _PLACEHOLDER_PATTERNS.match(value):
            placeholders.append(key)

    return placeholders


# ─── F-1 (RCA-343 ISSUE-1): Reverse SDK Verification ─────────────────
# Maps env var key patterns → {package_name, import_patterns}
# This is the REVERSE of _DEP_ENV_REQUIREMENTS:
# Forward: package → env vars (existing)
# Reverse: env var → packages + import patterns (NEW)

_ENV_SDK_REQUIREMENTS: dict[str, dict] = {
    "RESEND_API_KEY": {
        "packages": ["resend"],
        "import_patterns": ["resend"],
    },
    "STRIPE_SECRET_KEY": {
        "packages": ["stripe"],
        "import_patterns": ["stripe"],
    },
    "STRIPE_PUBLISHABLE_KEY": {
        "packages": ["stripe", "@stripe/stripe-js"],
        "import_patterns": ["stripe"],
    },
    "DATABASE_URL": {
        "packages": ["@prisma/client", "prisma"],
        "import_patterns": ["@prisma/client", "prisma"],
    },
    "SUPABASE_URL": {
        "packages": ["@supabase/supabase-js"],
        "import_patterns": ["@supabase/supabase-js", "supabase"],
    },
    "SUPABASE_ANON_KEY": {
        "packages": ["@supabase/supabase-js"],
        "import_patterns": ["@supabase/supabase-js", "supabase"],
    },
    "MONGODB_URI": {
        "packages": ["mongoose", "mongodb"],
        "import_patterns": ["mongoose", "mongodb"],
    },
    "CLERK_SECRET_KEY": {
        "packages": ["@clerk/nextjs", "@clerk/clerk-sdk-node"],
        "import_patterns": ["@clerk"],
    },
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": {
        "packages": ["@clerk/nextjs"],
        "import_patterns": ["@clerk"],
    },
    "NEXTAUTH_SECRET": {
        "packages": ["next-auth"],
        "import_patterns": ["next-auth"],
    },
    "AUTH_SECRET": {
        "packages": ["@auth/core"],
        "import_patterns": ["@auth/core", "@auth/"],
    },
    "OPENAI_API_KEY": {
        "packages": ["openai"],
        "import_patterns": ["openai"],
    },
    "OPENROUTER_API_KEY": {
        "packages": ["openai"],  # OpenRouter uses OpenAI SDK
        "import_patterns": ["openai", "openrouter"],
    },
    "ANTHROPIC_API_KEY": {
        "packages": ["@anthropic-ai/sdk"],
        "import_patterns": ["@anthropic-ai/sdk", "anthropic"],
    },
    "SMTP_HOST": {
        "packages": ["nodemailer"],
        "import_patterns": ["nodemailer"],
    },
    "SENTRY_DSN": {
        "packages": ["@sentry/nextjs", "@sentry/node", "@sentry/react"],
        "import_patterns": ["@sentry/"],
    },
}


def verify_sdk_completeness(project_dir: str) -> list[dict]:
    """Reverse SDK verification: env var → package.json dep → source import.

    For each env var found in .env files, checks:
    1. Is the corresponding SDK installed in package.json?
    2. Is the SDK actually imported in source code?

    This is the REVERSE of validate_config_coherence() which checks
    forward: package → env vars. Together they form a bidirectional check.

    Args:
        project_dir: Root directory of the project.

    Returns:
        List of findings, each with:
        - type: 'sdk_not_installed' | 'sdk_not_imported'
        - env_var: The env var that triggered the check
        - detail: Human-readable explanation
    """
    findings: list[dict] = []

    # Collect env vars
    env_vars = _collect_env_vars(project_dir)
    if not env_vars:
        return findings

    # Read package.json
    pkg_path = os.path.join(project_dir, "package.json")
    if not os.path.isfile(pkg_path):
        return findings

    try:
        with open(pkg_path, "r") as f:
            pkg = json.load(f)
    except (IOError, json.JSONDecodeError):
        return findings

    all_deps: dict = {}
    all_deps.update(pkg.get("dependencies", {}))
    all_deps.update(pkg.get("devDependencies", {}))

    # Check each env var against the reverse mapping
    for env_var in env_vars:
        req = _ENV_SDK_REQUIREMENTS.get(env_var)
        if not req:
            continue  # Unknown env var — skip

        required_packages = req["packages"]
        import_patterns = req["import_patterns"]

        # Check 1: Is ANY of the required packages in package.json?
        has_package = any(pkg_name in all_deps for pkg_name in required_packages)
        if not has_package:
            findings.append({
                "type": "sdk_not_installed",
                "env_var": env_var,
                "detail": (
                    f"Env var `{env_var}` is defined but none of "
                    f"{required_packages} found in package.json"
                ),
            })
            continue  # No point checking imports if package isn't installed

        # Check 2: Is the SDK actually imported in source?
        has_import = _check_source_import(project_dir, import_patterns)
        if not has_import:
            findings.append({
                "type": "sdk_not_imported",
                "env_var": env_var,
                "detail": (
                    f"Env var `{env_var}` is defined and package is in "
                    f"package.json, but no import of {import_patterns} "
                    f"found in source code"
                ),
            })

    if findings:
        logger.warning(
            f"[CONFIG COHERENCE] SDK completeness: {len(findings)} gap(s) in {project_dir}"
        )

    return findings


def _check_source_import(project_dir: str, import_patterns: list[str]) -> bool:
    """Check if any of the import patterns appear in project source files.

    Scans for both ESM (import ... from "X") and CJS (require("X")) patterns.
    """
    _scan_extensions = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

    import_re = re.compile(
        r'(?:import\s+.*?\s+from\s+["\']|require\s*\(\s*["\'])('
        + "|".join(re.escape(p) for p in import_patterns)
        + r')',
        re.IGNORECASE
    )

    # OVL-3: Use centralized scanner to list files.
    file_paths = list_project_files(
        project_dir,
        extensions=_scan_extensions,
    )

    for fpath in file_paths:
        try:
            with open(fpath, "r", errors="ignore") as f:
                content = f.read()
            if import_re.search(content):
                return True
        except (IOError, OSError):
            continue

    return False


# ─── RCA-354 / SS-11: Prisma .env ↔ schema.prisma Coherence Gate ─────
# Detects when .env DATABASE_URL protocol doesn't match the provider
# declared in prisma/schema.prisma. This is a CRITICAL gate because
# the mismatch causes Prisma P1012 errors at runtime/migration time.
#
# Root cause: `npx prisma init --datasource-provider sqlite` writes
# `postgresql://` to .env (known Prisma CLI bug). Even after the
# Layer 1 sed fix in _ORM_PACKAGES, this gate provides defense-in-depth.

# Map of Prisma provider → expected URL protocol prefixes
_PRISMA_PROVIDER_URL_PREFIXES: dict[str, list[str]] = {
    "sqlite": ["file:"],
    "postgresql": ["postgresql://", "postgres://"],
    "mysql": ["mysql://"],
    "sqlserver": ["sqlserver://"],
    "mongodb": ["mongodb://", "mongodb+srv://"],
    "cockroachdb": ["postgresql://", "postgres://"],
}


def validate_prisma_env_coherence(project_dir: str) -> list[dict[str, Any]]:
    """Validate .env DATABASE_URL matches prisma/schema.prisma provider.

    This is a CRITICAL gate — a protocol mismatch causes Prisma P1012
    errors at runtime. It provides defense-in-depth beyond the Layer 1
    sed fix in _ORM_PACKAGES['prisma']['init_cmd'].

    Args:
        project_dir: Root directory of the project.

    Returns:
        List of findings. Each finding has:
        - type: 'prisma_env_mismatch'
        - severity: 'critical'
        - detail: Human-readable explanation
        Empty list if no mismatch detected or files missing.
    """
    findings: list[dict[str, Any]] = []

    # ── Read schema.prisma ──
    schema_path = os.path.join(project_dir, "prisma", "schema.prisma")
    if not os.path.isfile(schema_path):
        return findings  # No schema = nothing to check

    try:
        with open(schema_path, "r") as f:
            schema_content = f.read()
    except (IOError, OSError):
        return findings

    # Extract provider from datasource block
    # Matches: provider = "sqlite" (with optional whitespace and quotes)
    provider_match = re.search(
        r'provider\s*=\s*["\'](\w+)["\']',
        schema_content,
    )
    if not provider_match:
        return findings  # Can't determine provider — skip

    provider = provider_match.group(1).lower()

    # ── Read .env DATABASE_URL ──
    env_vars = collect_env_var_values(project_dir)
    db_url = env_vars.get("DATABASE_URL", "")
    if not db_url:
        return findings  # No DATABASE_URL — nothing to validate

    # Strip surrounding quotes if present
    db_url = db_url.strip().strip('"').strip("'")

    # ── Check alignment ──
    expected_prefixes = _PRISMA_PROVIDER_URL_PREFIXES.get(provider)
    if expected_prefixes is None:
        return findings  # Unknown provider — can't validate

    url_matches = any(db_url.startswith(prefix) for prefix in expected_prefixes)
    if not url_matches:
        findings.append({
            "type": "prisma_env_mismatch",
            "severity": "critical",
            "detail": (
                f"Prisma schema.prisma declares provider='{provider}' "
                f"but .env DATABASE_URL starts with '{db_url[:30]}...' "
                f"(expected one of: {expected_prefixes}). "
                f"This will cause Prisma P1012 errors. "
                f"Fix .env to use the correct protocol for '{provider}'."
            ),
        })
        logger.error(
            f"[CONFIG COHERENCE] CRITICAL: Prisma env mismatch in {project_dir}: "
            f"provider={provider}, DATABASE_URL={db_url[:40]}..."
        )

    return findings

