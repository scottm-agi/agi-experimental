"""
Mock Data Detector (ADR-011) — Scans file content for hardcoded mock data
patterns and returns warnings for inline tool response injection.

Root cause (Iteration 144): Frontend agents created components with hardcoded
arrays (MOCK_BUSINESSES = [...]) instead of fetch() calls, defeating the
purpose of backend API routes. 55% prompt alignment was partially caused by
agents substituting mock data for real API integration.

Detection heuristics:
1. Named constants with MOCK_, SAMPLE_, DUMMY_, PLACEHOLDER_ prefixes
2. Hardcoded arrays with >3 objects in API route files (higher sensitivity)
3. Excludes: test files, small arrays (≤3), navigation configs, useState
"""
from __future__ import annotations
import re
import os
import logging
from typing import Optional

logger = logging.getLogger("agix.mock_data_detector")

# ── Mock data prefix patterns ──
_MOCK_PREFIX_PATTERN = re.compile(
    r"(?:const|let|var)\s+"
    r"((?:MOCK|SAMPLE|DUMMY|PLACEHOLDER|FAKE|TEST)_\w+)"
    r"\s*(?::\s*\w+(?:\[\])?)?\s*=\s*\[",
    re.IGNORECASE,
)

# ── Inline array detection (for API routes) ──
# Matches: const/let/var name = [ ... { ... }, { ... }, { ... }, { ... } ... ]
_ARRAY_WITH_OBJECTS_PATTERN = re.compile(
    r"(?:const|let|var)\s+(\w+)\s*(?::\s*[^=]+)?\s*=\s*\["
    r"(?:[^]]*\{[^}]*\}[^]]*){4,}"  # At least 4 objects
    r"\s*\]",
    re.DOTALL,
)

# ── RCA-322: Return-array detection ──
# Catches: return [ { ... }, { ... }, { ... } ]  (3+ objects)
# These bypass const/let/var patterns when agents write API stubs as functions
_RETURN_ARRAY_PATTERN = re.compile(
    r"return\s*\["
    r"(?:[^]]*\{[^}]*\}[^]]*){3,}"  # At least 3 objects
    r"\s*\]",
    re.DOTALL,
)

# ── Patterns to EXCLUDE (false positive protection) ──
_NAV_CONFIG_NAMES = {
    "nav_items", "navitems", "menu_items", "menuitems",
    "routes", "links", "nav_links", "navlinks",
    "breadcrumbs", "tabs", "steps", "options",
    "columns", "headers", "fields", "statuses",
}

# ── File path patterns that indicate test files ──
_TEST_PATH_PATTERNS = re.compile(
    r"(?:__tests__|\.test\.|\.spec\.|test_|_test\.|\.stories\.)",
    re.IGNORECASE,
)


def detect_mock_data(content: str, file_path: str) -> Optional[str]:
    """Scan file content for hardcoded mock data patterns.

    Args:
        content: The full file content to scan.
        file_path: The file path (used for context — API routes get
                   higher sensitivity, test files are exempt).

    Returns:
        Warning message string if mock data detected, None otherwise.
    """
    if not content or not file_path:
        return None

    # ── Exempt test files ──
    basename = os.path.basename(file_path)
    if _TEST_PATH_PATTERNS.search(file_path) or _TEST_PATH_PATTERNS.search(basename):
        return None

    # ── Exempt import/export barrel files ──
    # Files that are mostly export/import statements
    lines = content.strip().split("\n")
    non_empty_lines = [l.strip() for l in lines if l.strip()]
    if non_empty_lines:
        export_import_lines = sum(
            1 for l in non_empty_lines
            if l.startswith("export ") or l.startswith("import ")
        )
        if export_import_lines / len(non_empty_lines) > 0.8:
            return None

    warnings = []

    # ── Check 1: MOCK_ prefixed constants ──
    for match in _MOCK_PREFIX_PATTERN.finditer(content):
        var_name = match.group(1)
        warnings.append(
            f"🔴 MOCK DATA DETECTED: `{var_name}` is a hardcoded mock array. "
            f"Replace with a `fetch()` call to the appropriate API endpoint. "
            f"Mock data in production code defeats the purpose of backend APIs."
        )

    if warnings:
        return "\n".join(warnings)

    # ── Check 2: Hardcoded arrays in API routes ──
    is_api_route = "/api/" in file_path.replace("\\", "/")

    if is_api_route:
        for match in _ARRAY_WITH_OBJECTS_PATTERN.finditer(content):
            var_name = match.group(1)
            # Skip known config-like names
            if var_name.lower() in _NAV_CONFIG_NAMES:
                continue
            warnings.append(
                f"🔴 HARDCODED DATA IN API ROUTE: `{var_name}` contains a "
                f"hardcoded array with 4+ objects. API routes should fetch data "
                f"from a database or external API, not return static arrays."
            )

    if warnings:
        return "\n".join(warnings)

    # ── Check 2b (RCA-322): Return-array stubs ──
    # Agents create function stubs like `return [{ ... }, { ... }, { ... }]`
    # which bypass const/let/var patterns entirely.
    # Strip block comments first to avoid false positives from documentation.
    content_no_comments = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    for match in _RETURN_ARRAY_PATTERN.finditer(content_no_comments):
        # Find the function containing this return
        match_start = match.start()
        preceding = content_no_comments[max(0, match_start - 200):match_start]
        # Skip test helpers, mocks, fixtures
        if any(kw in preceding.lower() for kw in ['mock', 'test', 'fixture', 'describe(']):
            continue
        warnings.append(
            f"🔴 HARDCODED RETURN ARRAY: Found `return [{{...}}, ...]` with 3+ "
            f"hardcoded objects. Functions should fetch data from APIs or databases, "
            f"not return static arrays. Replace with a `fetch()` call or database query."
        )

    if warnings:
        return "\n".join(warnings)

    # ── Check 3: Non-prefixed hardcoded arrays in page components ──
    # U-5 upgrade: Agents frequently create hardcoded arrays like
    # `const reviews = [...]` without MOCK_ prefix in page components.
    # These bypass the prefix-only Check 1. We detect them by looking for
    # arrays with 4+ objects in route-level page files (page.tsx, page.jsx).
    is_page_component = bool(
        re.search(r'(?:^|/)(?:page|layout)\.(?:tsx|jsx|ts|js)$',
                  file_path.replace("\\", "/"))
    )

    if is_page_component:
        for match in _ARRAY_WITH_OBJECTS_PATTERN.finditer(content):
            var_name = match.group(1)
            # Skip known config-like names
            if var_name.lower() in _NAV_CONFIG_NAMES:
                continue
            # Skip if already caught by prefix check
            if re.match(r'(?i)(?:MOCK|SAMPLE|DUMMY|PLACEHOLDER|FAKE|TEST)_', var_name):
                continue
            # Skip if it's inside a function (likely useState initializer)
            # Check if preceded by useState( or similar React pattern
            match_start = match.start()
            preceding = content[max(0, match_start - 50):match_start]
            if 'useState' in preceding or 'useMemo' in preceding:
                continue
            warnings.append(
                f"🔴 HARDCODED DATA IN PAGE COMPONENT: `{var_name}` contains a "
                f"hardcoded array with 4+ objects in a route-level page. "
                f"Page components should fetch data from API endpoints, not "
                f"embed static arrays. Replace with a `fetch()` or `useSWR()` call."
            )

    if warnings:
        return "\n".join(warnings)

    # ── Check 4: Scaffold sentinel values (REPLACE_WITH_*) ──
    # F-01/F-06: Scaffold templates use REPLACE_WITH_* sentinels for metadata.
    # If these remain in delivered code, the agent never replaced them.
    sentinel_pattern = re.compile(r'REPLACE_WITH_\w+')
    sentinel_matches = sentinel_pattern.findall(content)
    if sentinel_matches:
        unique_sentinels = set(sentinel_matches)
        for sentinel in unique_sentinels:
            warnings.append(
                f"🔴 SCAFFOLD SENTINEL NOT REPLACED: `{sentinel}` is a placeholder "
                f"value from the scaffold template. Replace it with the actual project "
                f"value (e.g., real project title, description, or name)."
            )

    if warnings:
        return "\n".join(warnings)

    # ── Check 5: Mock data comments ──
    # RCA-04: Agents write "In a real app, this would..." comments alongside
    # hardcoded data, explicitly acknowledging the mock nature of their code.
    mock_comment_patterns = [
        (r'//\s*[Ii]n a real app', "In a real app"),
        (r'//\s*[Tt]his would normally', "This would normally"),
        (r'//\s*[Rr]eplace with real', "Replace with real"),
        (r'//\s*[Tt]his is (a )?placeholder', "This is a placeholder"),
        (r'//\s*[Tt]odo:?\s*(?:fetch|integrate|connect|wire)', "TODO: integrate"),
    ]
    for pattern, label in mock_comment_patterns:
        if re.search(pattern, content):
            warnings.append(
                f"🔴 MOCK DATA COMMENT: Found '{label}' comment indicating "
                f"placeholder data. Either implement real API integration or "
                f"escalate via TASK_INJECTION if the backend doesn't exist yet."
            )

    if warnings:
        return "\n".join(warnings)

    # ── Check 6: Bracket placeholder text in JSX ──
    # RCA-06: Agents use [Dashboard Preview] or [Your Company Name] as
    # visible placeholder text in components instead of real content.
    bracket_placeholder_pattern = re.compile(
        r'\[(?:Dashboard|Preview|Your |Company|Business|Team|User|'
        r'Image|Logo|Icon|Chart|Graph|Map|Video|Audio|'
        r'Placeholder|Sample|Example|Widget)\s*\w*\]'
    )
    bracket_matches = bracket_placeholder_pattern.findall(content)
    if bracket_matches:
        for match_text in bracket_matches[:3]:  # Cap at 3 to avoid noise
            warnings.append(
                f"🔴 BRACKET PLACEHOLDER TEXT: `{match_text}` looks like placeholder "
                f"content in the UI. Replace with real content from the requirements "
                f"or data fetched from the API."
            )

    if warnings:
        return "\n".join(warnings)

    # ── Check 7: Dead links (href="#") ──
    # RCA-10: Agents use href="#" for links to pages that don't exist yet,
    # creating a broken user experience.
    dead_link_pattern = re.compile(r'href\s*=\s*["\']#["\']')
    dead_link_matches = dead_link_pattern.findall(content)
    if dead_link_matches:
        count = len(dead_link_matches)
        warnings.append(
            f"🔴 DEAD LINKS DETECTED: Found {count} instance(s) of `href=\"#\"`. "
            f"Dead links break user navigation. Either create the target page, "
            f"use a real anchor ID (e.g., `href=\"#pricing-section\"`), or use a "
            f"disabled/coming-soon pattern instead."
        )

    if warnings:
        return "\n".join(warnings)

    # ── Check 8 (ITR-31): Unbracketed placeholder text in JSX ──
    # Agents write placeholder text WITHOUT brackets (e.g., "Dashboard Preview",
    # "Hero Section", "Content Area") which bypasses Check 6's bracket regex.
    # These are visible in the rendered UI as placeholder content that was never
    # replaced with real data or components.
    #
    # Only triggers in files that contain JSX (angle brackets with components).
    # Exempt: nav labels (single words like "Dashboard"), config objects,
    # and files that don't contain JSX.
    is_jsx_file = bool(re.search(r'<\w+[\s/>]', content))
    if is_jsx_file:
        # Pattern: Match 2+ word phrases that are common placeholder text.
        # These appear as visible text content in JSX, not as variable names.
        _UNBRACKETED_PLACEHOLDER_PATTERN = re.compile(
            r'(?:>|["\'])\s*'  # preceded by > (JSX text) or quotes (prop value)
            r'('
            r'(?:Dashboard|Hero|Banner|Content|Image|Logo|Placeholder|Section|'
            r'Widget|Chart|Graph|Preview|Video|Audio|Map|Icon|Feature|Card|'
            r'Sidebar|Header|Footer|Modal|Form|Table)\s+'
            r'(?:Preview|Section|Area|Here|Placeholder|Image|Content|Block|'
            r'Zone|Slot|Container|Region|Panel|Widget|Component|Space|Wrapper)'
            r')',
            re.IGNORECASE,
        )

        placeholder_matches = _UNBRACKETED_PLACEHOLDER_PATTERN.findall(content)
        if placeholder_matches:
            unique_matches = list(dict.fromkeys(placeholder_matches))[:3]
            for match_text in unique_matches:
                warnings.append(
                    f"🔴 PLACEHOLDER TEXT DETECTED: `{match_text.strip()}` looks like "
                    f"placeholder UI content. Replace with real content from the "
                    f"requirements, or use a proper component with fetched data."
                )

    if warnings:
        return "\n".join(warnings)

    # ── Check 9 (RCA-ITR32-A): Unreplaced template markers ──
    # Code agents sometimes use Handlebars/Mustache-style {{SECRET_...}} or
    # {{STRIPE_KEY}} syntax in source code, which has NO runtime replacement
    # engine. The env bridge's _TEMPLATE_MARKER_RE only scans vault values,
    # not generated source files. This check catches any {{VARIABLE_NAME}}
    # patterns that were never replaced with process.env.* calls.
    # FIX-4 (ITR-32 audit): Expanded from uppercase-only to also catch
    # lowercase markers like {{email}}, {{name}}, {{businessId}}.
    _TEMPLATE_MARKER_PATTERN = re.compile(r'\{\{[a-zA-Z][a-zA-Z0-9_]*\}\}')
    template_markers = _TEMPLATE_MARKER_PATTERN.findall(content)
    if template_markers:
        unique_markers = list(dict.fromkeys(template_markers))[:5]
        for marker in unique_markers:
            var_name = marker[2:-2]
            env_hint = (
                f"`process.env.{var_name}` (server component) or "
                f"`process.env.NEXT_PUBLIC_{var_name}` (client component)"
            ) if var_name.isupper() else (
                f"a JavaScript expression like `${{recipientEmail}}`"
            )
            warnings.append(
                f"🔴 UNREPLACED TEMPLATE MARKER: `{marker}` is a Handlebars/Mustache-style "
                f"template literal with NO runtime replacement engine. Replace with "
                f"{env_hint} with a hardcoded fallback value."
            )

    if warnings:
        return "\n".join(warnings)

    # ── Check 10 (ITR-32 ISS-8): Mock/stub/fake function names ──
    # Agents create functions literally named mock*(), stub*(), fake*()
    # in production source code. These bypass all other mock data checks
    # because they're functions, not arrays or constants.
    # Only fires in non-test files (test files are already exempt above).
    _MOCK_FUNC_PATTERN = re.compile(
        r'(?:async\s+)?function\s+(mock|stub|fake)\w+\s*\(',
        re.IGNORECASE,
    )
    mock_func_matches = _MOCK_FUNC_PATTERN.findall(content)
    if mock_func_matches:
        # Also check for arrow function pattern: const mockX = async (...) =>
        _MOCK_ARROW_PATTERN = re.compile(
            r'(?:const|let|var)\s+(mock|stub|fake)\w+\s*=\s*(?:async\s+)?\(',
            re.IGNORECASE,
        )
        mock_func_matches += _MOCK_ARROW_PATTERN.findall(content)

    if mock_func_matches:
        warnings.append(
            f"🔴 MOCK FUNCTION DETECTED: Found {len(mock_func_matches)} function(s) with "
            f"'mock'/'stub'/'fake' prefix in production code. Replace with real "
            f"implementation or emit a TASK_INJECTION if the backend doesn't exist."
        )

    if warnings:
        return "\n".join(warnings)

    return None

