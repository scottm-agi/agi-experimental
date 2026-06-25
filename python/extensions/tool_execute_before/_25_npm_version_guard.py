"""
npm Version Guard Extension (RCA-288 — Unpinned Dependency Prevention)

Intercepts `code_execution_tool` calls containing npm install/i commands
that target known-breaking packages WITHOUT a version pin suffix (@X.Y.Z).

When detected, blocks the command and returns the correct version-pinned
command to the agent.

This is a FAIL-SAFE (Layer 2) — the root cause fix is in
agent.system.custom_instructions.md which provides explicit version-pinned
install commands. This guard catches cases where the LLM ignores the prompt.

Dynamic Versions: The guard reads researcher-found versions from agent_data
(keys: _researcher_versions, _framework_research) and merges them into
PINNED_PACKAGES. Researcher versions take priority over hardcoded defaults.
Hardcoded versions remain as fallback when no researcher data exists.

5-Why RCA (RCA-288):
  1. Code agent cancelled at iter=26 in same-message loop
  2. Prisma 7.8.0 (BANNED) was installed → build failures
  3. Agent ran `npm install @prisma/client` without version pin
  4. Prompt says "Prisma 7 BANNED" but gives no explicit install command
  5. ROOT: LLM training-data defaults override abstract version guidance;
     no runtime enforcement intercepts unpinned installs
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.universal_gate_budget import gate_check

logger = logging.getLogger("agix.npm_version_guard")

# ─── Known-breaking packages and their required pinned versions ─────────
# Key: package name (lowercase), Value: (required version suffix, human label)
# These serve as FALLBACK defaults when no researcher data is available.
PINNED_PACKAGES: dict[str, Tuple[str, str]] = {
    "prisma":           ("@5.22.0",  "Prisma 7 is BANNED — use 5.22.0"),
    "@prisma/client":   ("@5.22.0",  "Prisma 7 is BANNED — use 5.22.0"),
    "@prisma/cli":      ("@5.22.0",  "Prisma 7 is BANNED — use 5.22.0"),
    "react":            ("@18.3.1",  "React 19 is BANNED — use 18.3.1"),
    "react-dom":        ("@18.3.1",  "React 19 is BANNED — use 18.3.1"),
    "tailwindcss":      ("@3.4.17",  "Tailwind v4 is BANNED — use 3.4.17"),
    "postcss":          ("@8.4.49",  "PostCSS 8.5+ is BANNED — use 8.4.49"),
    "autoprefixer":     ("@10.4.20", "Use autoprefixer 10.4.20"),
}

# ─── Name normalization map ─────────────────────────────────────────────
# Maps common prose names to their npm package names.
_NAME_NORMALIZATION: dict[str, str] = {
    "next.js": "next",
    "nextjs": "next",
    "react.js": "react",
    "reactjs": "react",
    "tailwind css": "tailwindcss",
    "tailwind": "tailwindcss",
    "postcss": "postcss",
    "auto prefixer": "autoprefixer",
}

# Regex to detect npm install/i commands
NPM_INSTALL_RE = re.compile(
    r'\b(?:npm\s+(?:install|i|add))\s+',
    re.IGNORECASE,
)

# Regex to extract individual package specs from an npm install command
# Matches: @scope/pkg@version, @scope/pkg, pkg@version, pkg
PACKAGE_SPEC_RE = re.compile(
    r'(?:^|\s)((?:@[\w\-]+/)?[\w\-]+(?:@[\w\.\-]+)?)\b'
)

# ─── Version Extraction Patterns ───────────────────────────────────────

# Matches markdown table rows: | package-name | 1.2.3 | ... |
# or | @scope/package | 1.2.3 | ... |
_TABLE_ROW_RE = re.compile(
    r'^\s*\|\s*'
    r'((?:@[\w\-]+/)?[\w\.\-]+(?:\s+[\w\.\-]+)?)'  # Package/library name (may have space, e.g. "Tailwind CSS")
    r'\s*\|\s*'
    r'@?(\d+\.\d+[\.\d]*)'  # Version (optional @ prefix)
    r'\s*\|',
    re.MULTILINE,
)

# Matches inline prose: "React 18.4.0", "Next.js 14.2.15", "Prisma 5.22.0"
_INLINE_VERSION_RE = re.compile(
    r'\b((?:@[\w\-]+/)?[\w\.\-]+(?:\s+CSS)?)\s+(\d+\.\d+[\.\d]*)\b'
)

# Matches npm/yarn/pnpm install commands with pinned packages:
# npm install react@18.4.0, yarn add next@14.2.15, pnpm add prisma@5.22.0
_PKG_MANAGER_INSTALL_RE = re.compile(
    r'\b(?:npm\s+(?:install|i|add)|yarn\s+add|pnpm\s+(?:add|install))\s+',
    re.IGNORECASE,
)

# Matches a single package@version spec from install commands
_PKG_AT_VERSION_RE = re.compile(
    r'((?:@[\w\-]+/)?[\w\-]+)@(\d+[\w\.\-]*)'
)


def _normalize_package_name(name: str) -> str:
    """Normalize a prose package name to its npm package name.

    E.g., "Next.js" → "next", "Tailwind CSS" → "tailwindcss", "React" → "react".
    """
    lowered = name.lower().strip()

    # Check normalization map first
    if lowered in _NAME_NORMALIZATION:
        return _NAME_NORMALIZATION[lowered]

    # Remove trailing dots (e.g., "Next.js" → after lower → "next.js" → mapped above)
    # For simple names, just lowercase and strip dots
    cleaned = lowered.replace(".", "").replace(" ", "")
    if cleaned in _NAME_NORMALIZATION:
        return _NAME_NORMALIZATION[cleaned]

    return lowered


def extract_versions_from_research(research_text: Optional[str]) -> Dict[str, Tuple[str, str]]:
    """Extract package versions from researcher output text.

    Parses three formats:
    1. Compatibility matrix tables: | Library | Version | Notes |
    2. Inline version mentions: "React 18.4.0", "Next.js 14.2.15"
    3. npm/yarn/pnpm install commands: npm install react@18.4.0

    Args:
        research_text: The raw researcher output string.

    Returns:
        Dict mapping package name → (version_suffix, label).
        E.g., {"react": ("@18.4.0", "Researcher resolved: react 18.4.0")}
    """
    if not research_text or not isinstance(research_text, str):
        return {}

    versions: Dict[str, Tuple[str, str]] = {}

    # ── Pass 1: Inline prose versions (lowest priority — overridden by later passes) ──
    for match in _INLINE_VERSION_RE.finditer(research_text):
        raw_name = match.group(1)
        version = match.group(2)

        # Skip table header/separator artifacts and non-package words
        if raw_name in ("Version", "version", "Library", "Package", "Notes"):
            continue

        pkg_name = _normalize_package_name(raw_name)
        versions[pkg_name] = (f"@{version}", f"Researcher resolved: {pkg_name} {version}")

    # ── Pass 2: npm/yarn/pnpm install commands (medium priority) ──
    for install_match in _PKG_MANAGER_INSTALL_RE.finditer(research_text):
        # Get the rest of the line after the install command
        rest_of_line = research_text[install_match.end():]
        # Stop at newline
        eol = rest_of_line.find("\n")
        if eol >= 0:
            rest_of_line = rest_of_line[:eol]

        for pkg_match in _PKG_AT_VERSION_RE.finditer(rest_of_line):
            pkg_name = pkg_match.group(1).lower()
            version = pkg_match.group(2)
            # Keep @ prefix for scoped packages
            if pkg_match.group(0).startswith("@"):
                pkg_name = "@" + pkg_name.lstrip("@")
            versions[pkg_name] = (f"@{version}", f"Researcher resolved: {pkg_name} {version}")

    # ── Pass 3: Compatibility matrix table rows (highest priority) ──
    for match in _TABLE_ROW_RE.finditer(research_text):
        raw_name = match.group(1).strip()
        version = match.group(2)

        # Skip header rows
        if raw_name.lower() in ("library", "package", "name", "dependency"):
            continue
        # Skip separator rows (dashes)
        if set(raw_name) <= {"-", " "}:
            continue

        pkg_name = _normalize_package_name(raw_name)
        versions[pkg_name] = (f"@{version}", f"Researcher resolved: {pkg_name} {version}")

    return versions


def extract_versions_from_package_json(pkg_json_path: str) -> Dict[str, Tuple[str, str]]:
    """Extract package versions from an existing package.json file.

    Reads both dependencies and devDependencies, strips semver range
    prefixes (^, ~, >=) to get exact versions.

    Args:
        pkg_json_path: Absolute path to package.json.

    Returns:
        Dict mapping package name → (version_suffix, label).
    """
    try:
        with open(pkg_json_path, "r", encoding="utf-8") as f:
            pkg_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

    versions: Dict[str, Tuple[str, str]] = {}

    for section in ("dependencies", "devDependencies", "peerDependencies"):
        deps = pkg_data.get(section, {})
        if not isinstance(deps, dict):
            continue
        for pkg_name, version_str in deps.items():
            if not isinstance(version_str, str):
                continue
            # Strip semver range prefixes: ^, ~, >=, >, <=, <, =
            clean_version = re.sub(r'^[~^>=<]+', '', version_str).strip()
            # Only accept versions that look like semver (X.Y.Z)
            if re.match(r'^\d+\.\d+', clean_version):
                pkg_lower = pkg_name.lower()
                versions[pkg_lower] = (
                    f"@{clean_version}",
                    f"package.json: {pkg_name} {clean_version}",
                )

    return versions


def get_effective_pinned_packages(
    agent_data: Optional[dict] = None,
) -> Dict[str, Tuple[str, str]]:
    """Build the effective pinned packages dict by merging sources.

    Priority (highest wins):
    1. agent_data["_researcher_versions"] — pre-parsed researcher versions
    2. Parsed from agent_data["_framework_research"] — raw research text
    3. PINNED_PACKAGES — hardcoded fallback defaults

    Results are cached in agent_data["_npm_version_guard_cache"] to avoid
    re-parsing on every tool invocation.

    Args:
        agent_data: The agent's data dict (mutable). May be None.

    Returns:
        Dict mapping package name → (version_suffix, label).
    """
    if agent_data is None:
        return dict(PINNED_PACKAGES)

    # ── Check cache ──
    cached = agent_data.get("_npm_version_guard_cache")
    if cached is not None and isinstance(cached, dict):
        return cached

    # ── Start with hardcoded fallback ──
    effective: Dict[str, Tuple[str, str]] = dict(PINNED_PACKAGES)

    # ── Layer 1: Parse _framework_research raw text ──
    research_text = agent_data.get("_framework_research")
    if isinstance(research_text, str) and research_text.strip():
        try:
            parsed = extract_versions_from_research(research_text)
            effective.update(parsed)
            logger.info(
                f"[NPM VERSION GUARD] Parsed {len(parsed)} versions from "
                f"_framework_research: {list(parsed.keys())}"
            )
        except Exception as e:
            logger.warning(
                f"[NPM VERSION GUARD] Failed to parse _framework_research: {e}"
            )

    # ── Layer 2: Use pre-parsed _researcher_versions (highest priority) ──
    researcher_versions = agent_data.get("_researcher_versions")
    if isinstance(researcher_versions, dict):
        try:
            for pkg_name, value in researcher_versions.items():
                if isinstance(value, (tuple, list)) and len(value) >= 2:
                    effective[pkg_name.lower()] = (str(value[0]), str(value[1]))
            logger.info(
                f"[NPM VERSION GUARD] Merged {len(researcher_versions)} versions from "
                f"_researcher_versions: {list(researcher_versions.keys())}"
            )
        except Exception as e:
            logger.warning(
                f"[NPM VERSION GUARD] Failed to merge _researcher_versions: {e}"
            )

    # ── Cache the result ──
    agent_data["_npm_version_guard_cache"] = effective

    return effective


def _check_unpinned_packages(
    command: str,
    effective_packages: Optional[Dict[str, Tuple[str, str]]] = None,
) -> List[Tuple[str, str]]:
    """
    Check a shell command for unpinned npm installs of known-breaking packages.

    Args:
        command: The full shell command string
        effective_packages: Optional override for the package dict to check against.
                           If None, uses the global PINNED_PACKAGES fallback.

    Returns:
        List of (package_name, required_version) tuples for unpinned packages.
        Empty list if all packages are properly pinned or not in the known list.
    """
    packages_to_check = effective_packages if effective_packages is not None else PINNED_PACKAGES
    flagged: List[Tuple[str, str]] = []

    # Find npm install commands in the string
    # Handle chained commands (&&) and pipes
    segments = re.split(r'&&|\|\||;', command)

    for segment in segments:
        segment = segment.strip()
        if not NPM_INSTALL_RE.search(segment):
            continue

        # Extract the part after "npm install/i"
        install_match = NPM_INSTALL_RE.search(segment)
        if not install_match:
            continue

        packages_str = segment[install_match.end():]

        # Remove flags (--save, --save-dev, -D, -S, etc.)
        packages_str = re.sub(r'\s+--?\w[\w\-]*(?:\s+\S+)?', ' ', packages_str)

        # Extract individual package specs
        specs = PACKAGE_SPEC_RE.findall(packages_str)

        for spec in specs:
            # Skip flags that leaked through
            if spec.startswith('-'):
                continue

            # Split package name and version
            if '@' in spec and not spec.startswith('@'):
                # pkg@version
                pkg_name = spec.split('@')[0]
                has_version = True
            elif spec.startswith('@'):
                # @scope/pkg or @scope/pkg@version
                parts = spec.split('@')
                # @scope/pkg → ['', 'scope/pkg']
                # @scope/pkg@version → ['', 'scope/pkg', 'version']
                if len(parts) >= 3:
                    pkg_name = '@' + parts[1]
                    has_version = True
                else:
                    pkg_name = spec
                    has_version = False
            else:
                pkg_name = spec
                has_version = False

            # Check if this package is in our effective pinned list
            pkg_lower = pkg_name.lower()
            if pkg_lower in packages_to_check and not has_version:
                required_ver, reason = packages_to_check[pkg_lower]
                flagged.append((pkg_name, f"{pkg_name}{required_ver} — {reason}"))

    return flagged


class NpmVersionGuard(Extension):
    # Context-aware: only fire for code agents, on code execution
    PROFILES = {"code"}
    TOOLS = frozenset({"code_execution_tool", "code_execution"})

    """
    Blocks npm install commands that target known-breaking packages
    without a version pin. Returns the correct pinned command.

    Dynamically reads researcher-found versions from agent_data,
    falling back to hardcoded PINNED_PACKAGES when no researcher data exists.
    """

    async def execute(self, **kwargs: Any) -> Optional[Any]:
        # Only intercept code_execution_tool
        tool_name = kwargs.get("tool_name", "")
        if tool_name != "code_execution_tool":
            return None

        tool_args = kwargs.get("tool_args", {})
        code = tool_args.get("code", "")
        if not code:
            return None

        # Build effective package list from researcher data + hardcoded fallback
        agent_data = getattr(self.agent, "data", None) if hasattr(self, "agent") else None
        effective = get_effective_pinned_packages(agent_data=agent_data)

        # Check for unpinned installs against the effective package list
        flagged = _check_unpinned_packages(code, effective_packages=effective)

        if not flagged:
            return None

        # Escape hatch — prevent infinite blocking loops
        if gate_check(self.agent.data, "npm_version_guard"):
            return None

        # Build the corrective message — dynamically from effective packages
        corrections = "\n".join(
            f"  ❌ {pkg} → ✅ {fix}" for pkg, fix in flagged
        )

        # Build dynamic install suggestions from flagged packages
        install_suggestions = []
        for pkg, _ in flagged:
            pkg_lower = pkg.lower()
            if pkg_lower in effective:
                ver = effective[pkg_lower][0]
                install_suggestions.append(f"{pkg}{ver}")

        install_cmd = " ".join(install_suggestions) if install_suggestions else ""

        block_msg = (
            f"🚫 **npm Version Guard (RCA-288)**: Blocked unpinned install of "
            f"known-breaking package(s).\n\n"
            f"The following packages MUST have explicit version pins:\n"
            f"{corrections}\n\n"
            f"**Correct install command** (copy verbatim):\n"
            f"```bash\n"
            f"npm install {install_cmd}\n"
            f"```\n\n"
            f"Re-run your command with the pinned versions above."
        )

        logger.warning(
            f"[NPM VERSION GUARD] Blocked unpinned install: "
            f"{[pkg for pkg, _ in flagged]} in command: {code[:100]}..."
        )

        return Response(
            message=block_msg,
            break_loop=False,
        )
