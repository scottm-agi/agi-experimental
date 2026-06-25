"""
Route Reachability Validator

Parses <Link href="..."> and <a href="..."> from TSX/JSX files and verifies
each internal route has a corresponding page file in Next.js App Router.

Forgejo #1168: Quality gate bypasses route validation when _verification_delegated=True
"""

import os
import re
import json
import shutil
import logging
import asyncio
import subprocess
import time
from typing import Optional
from python.helpers.planning_paths import get_path as _planning_path

logger = logging.getLogger("agix.validators.route_reachability")

# Patterns to extract href values from Link and a tags
# Matches: <Link href="/path"> and <a href="/path"> (both ' and " quotes)
HREF_PATTERN = re.compile(
    r'<(?:Link|a)\s+[^>]*href\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# External/non-route patterns to exclude
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#", "javascript:")

# ── Soft-404 Detection ─────────────────────────────────────────────────
# Pages that return HTTP 200 but display generic 404 content.
# This catches Next.js "404 | This page could not be found" and similar
# frameworks that use client-side routing with catch-all error pages.

# Patterns in page body that indicate a soft 404 (case-insensitive)
_SOFT_404_PATTERNS = [
    re.compile(r'\b404\b[^0-9]*(?:page|not\s*found|could\s*not\s*be\s*found)', re.IGNORECASE),
    re.compile(r'page\s*(?:could\s*)?not\s*(?:be\s*)?found', re.IGNORECASE),
    re.compile(r'does\s*not\s*exist', re.IGNORECASE),
    re.compile(r'<title>\s*404', re.IGNORECASE),
]

# Minimum real content length (after stripping HTML tags)
_MIN_CONTENT_LENGTH = 50

# G-6 (ITR-24): Minimum file size in bytes for a page file to be considered
# non-stub. Files under this threshold are likely scaffold stubs.
_PAGE_MIN_BYTES = 100

# ── Server Error Detection (RCA-233, U-4) ─────────────────────────────
# 2-Layer detection:
#   Layer 1: Fast deterministic patterns for fatal server errors
#   Layer 2: LLM content review for subtle rendering failures
#
# Layer 1 patterns match fatal conditions in the VISIBLE body (excluding
# <script>/<style> tags, same as soft-404) PLUS specific __NEXT_DATA__
# JSON patterns that require <script> inspection.

# Fatal patterns checked against visible body (scripts stripped)
_SERVER_ERROR_VISIBLE_PATTERNS = [
    re.compile(r'\bInternal\s+Server\s+Error\b', re.IGNORECASE),
    re.compile(r'\bCannot\s+find\s+module\b', re.IGNORECASE),
    re.compile(r'\bMODULE_NOT_FOUND\b', re.IGNORECASE),
    re.compile(r'\bUnhandled\s+Runtime\s+Error\b', re.IGNORECASE),
    re.compile(r'\bHydration\s+failed\b', re.IGNORECASE),
    re.compile(r'\bApplication\s+error\b', re.IGNORECASE),
]

# Patterns checked inside __NEXT_DATA__ <script> block specifically
_NEXT_DATA_ERROR_PATTERN = re.compile(
    r'__NEXT_DATA__[^{]*\{.*?"statusCode"\s*:\s*(5\d{2})',
    re.DOTALL,
)


def is_server_error_content(body: str) -> Optional[str]:
    """Layer 1: Detect fatal server errors in page body.

    Returns the name of the matched error pattern, or None if clean.
    This is the fast deterministic filter — no LLM call.

    Strategy:
    - Check visible body (scripts/styles stripped) for error patterns.
      This prevents false positives from error text in user content
      (e.g., a troubleshooting guide mentioning "Internal Server Error").
      Error text in <p> tags with surrounding real content is NOT flagged.
    - Check __NEXT_DATA__ script block for statusCode:5xx specifically.
    - The visible body check uses a heuristic: if the ENTIRE visible body
      is dominated by the error pattern (error text is a significant
      fraction of visible content), it's a real error. If the error text
      appears alongside substantial other content, it's user content.

    Args:
        body: Raw HTML body string from curl.

    Returns:
        Name of matched error pattern, or None if no server error detected.
    """
    if not body or not body.strip():
        return None

    # Check 1: __NEXT_DATA__ with statusCode:5xx (checked in <script> blocks)
    match = _NEXT_DATA_ERROR_PATTERN.search(body)
    if match:
        status = match.group(1)
        return f"__NEXT_DATA__ statusCode:{status}"

    # Check 2: Visible body patterns (scripts/styles stripped)
    visible = re.sub(r'<script[^>]*>.*?</script>', ' ', body, flags=re.DOTALL | re.IGNORECASE)
    visible = re.sub(r'<style[^>]*>.*?</style>', ' ', visible, flags=re.DOTALL | re.IGNORECASE)

    # Strip HTML tags to get text content
    text_only = re.sub(r'<[^>]+>', ' ', visible)
    text_only = re.sub(r'\s+', ' ', text_only).strip()

    for pattern in _SERVER_ERROR_VISIBLE_PATTERNS:
        match = pattern.search(visible)
        if match:
            # Heuristic: only flag if the error dominates the visible content.
            # If there's substantial content around the error text, it's
            # likely user-authored content (docs, FAQ, troubleshooting guide).
            # Threshold: if visible text has > 200 chars AND error text is
            # < 30% of the total visible text, skip it.
            matched_text = match.group(0)
            if len(text_only) > 200 and len(matched_text) / len(text_only) < 0.30:
                continue
            return pattern.pattern.replace('\\b', '').replace('\\s+', ' ')

    return None


def llm_content_review(
    body: str,
    route: str,
    expected_description: str = "",
    _mock_response: Optional[dict] = ...,  # Sentinel for test injection
) -> dict:
    """Layer 2: Use LLM to judge if content matches expected URI.

    DEPRECATED: Use score_route_content_alignment() for the heuristic path.
    This function is kept for backward compatibility — it now delegates
    to the heuristic scorer instead of calling an LLM.

    Args:
        body: Raw HTML body string.
        route: The route path (e.g., "/pricing").
        expected_description: What the sitemap says this route should contain.
        _mock_response: For testing — inject a mock response dict. Use the
            sentinel value (...) to indicate no mock (real LLM call).

    Returns:
        dict with: aligned, reason/reasoning, confidence
    """
    # Test injection: return mock if provided
    if _mock_response is not ...:
        if _mock_response is None:
            return {
                "aligned": True,
                "reason": "LLM timeout/fallback — defaulting to aligned",
                "confidence": 0.0,
            }
        return _mock_response

    # Delegate to heuristic scorer (RCA-233 P1 fix)
    result = score_route_content_alignment(body, route, expected_description)
    # Map 'reasoning' → 'reason' for backward compatibility
    return {
        "aligned": result["aligned"],
        "reason": result["reasoning"],
        "confidence": result["confidence"],
    }


# ── Route Keyword Maps ────────────────────────────────────────────────
# Route-derived keywords for content alignment scoring (L2 dimension 3).
# Each route pattern maps to keywords that SHOULD appear in page body.
_ROUTE_KEYWORDS = {
    "/pricing": ["price", "plan", "month", "annual", "tier", "subscription", "free", "$", "starter", "pro", "enterprise"],
    "/about": ["about", "story", "mission", "team", "founded", "company", "history", "values"],
    "/contact": ["contact", "email", "phone", "address", "message", "form", "reach", "support"],
    "/features": ["feature", "capability", "benefit", "integration", "automation", "monitor", "track"],
    "/dashboard": ["dashboard", "analytics", "metric", "chart", "overview", "stat", "review", "activity"],
    "/settings": ["setting", "preference", "account", "profile", "notification", "configuration"],
    "/auth": ["sign", "login", "password", "email", "authenticate", "register", "account"],
    "/blog": ["blog", "post", "article", "read", "published", "author", "category"],
    "/privacy": ["privacy", "data", "collect", "cookie", "consent", "gdpr", "policy"],
    "/terms": ["terms", "service", "agreement", "liability", "govern", "accept", "condition"],
    "/faq": ["faq", "question", "answer", "frequently", "help", "support"],
    "/reviews": ["review", "rating", "star", "feedback", "testimonial", "customer"],
}

# Semantic HTML elements that indicate real page structure
_STRUCTURAL_TAGS = [
    "<h1", "<h2", "<h3", "<section", "<article", "<nav",
    "<main", "<header", "<footer", "<aside", "<figure",
    "<form", "<table", "<ul", "<ol",
]


def score_route_content_alignment(
    body: str,
    route: str,
    expected_description: str = "",
) -> dict:
    """Layer 2: Heuristic content alignment scoring.

    Checks whether the page body is appropriate for the given route
    using fast deterministic heuristics (no LLM call).

    Scoring dimensions (weighted average):
      1. Length adequacy (20%) — body text length vs min threshold
      2. Structural density (25%) — ratio of semantic HTML elements
      3. Route keyword match (35%) — route-derived keywords in body
      4. Content uniqueness (20%) — body has real content, not placeholder

    Args:
        body: Raw HTML body string.
        route: The route path (e.g., "/pricing").
        expected_description: Optional sitemap description for extra keyword hints.

    Returns:
        dict with:
            aligned: bool — True if content matches expectations
            reasoning: str — why it's aligned or not
            confidence: float — 0.0 to 1.0
    """
    if not body or not body.strip():
        return {
            "aligned": False,
            "reasoning": "Empty or whitespace-only body",
            "confidence": 0.0,
        }

    # Strip scripts and styles to get visible content
    visible = re.sub(r'<script[^>]*>.*?</script>', ' ', body, flags=re.DOTALL | re.IGNORECASE)
    visible = re.sub(r'<style[^>]*>.*?</style>', ' ', visible, flags=re.DOTALL | re.IGNORECASE)
    text_only = re.sub(r'<[^>]+>', ' ', visible)
    text_only = re.sub(r'\s+', ' ', text_only).strip()
    text_lower = text_only.lower()

    reasons = []

    # ── Dimension 1: Length Adequacy (weight 0.20) ──────────────────
    # Real pages have substantial text content.
    text_len = len(text_only)
    if text_len < 20:
        length_score = 0.0
        reasons.append(f"Minimal text content ({text_len} chars)")
    elif text_len < 100:
        length_score = 0.3
        reasons.append(f"Short text content ({text_len} chars)")
    elif text_len < 300:
        length_score = 0.6
        reasons.append(f"Moderate text content ({text_len} chars)")
    else:
        length_score = 1.0
        reasons.append(f"Substantial text content ({text_len} chars)")

    # ── Dimension 2: Structural Density (weight 0.25) ──────────────
    # Real pages use semantic HTML elements.
    body_lower = body.lower()
    struct_count = sum(1 for tag in _STRUCTURAL_TAGS if tag in body_lower)
    if struct_count == 0:
        struct_score = 0.1
        reasons.append("No semantic HTML structure detected")
    elif struct_count <= 2:
        struct_score = 0.5
        reasons.append(f"Minimal HTML structure ({struct_count} semantic elements)")
    elif struct_count <= 5:
        struct_score = 0.8
        reasons.append(f"Good HTML structure ({struct_count} semantic elements)")
    else:
        struct_score = 1.0
        reasons.append(f"Rich HTML structure ({struct_count} semantic elements)")

    # ── Dimension 3: Route Keyword Match (weight 0.35) ─────────────
    # The most important dimension — does the content match the route?
    route_clean = route.strip("/").lower()
    # Root page is always flexible
    if route in ("/", ""):
        keyword_score = 0.8
        reasons.append("Root page — flexible keyword matching")
    else:
        # Find matching keyword set
        matched_keywords = []
        for route_pattern, keywords in _ROUTE_KEYWORDS.items():
            pattern_clean = route_pattern.strip("/").lower()
            if pattern_clean in route_clean or route_clean in pattern_clean:
                matched_keywords = keywords
                break

        # If no predefined keywords, derive from route segments
        if not matched_keywords:
            segments = route_clean.split("/")
            matched_keywords = [seg for seg in segments if len(seg) > 2 and not seg.startswith("[")]

        # Add keywords from expected_description if provided
        if expected_description:
            desc_words = [w.lower() for w in expected_description.split() if len(w) > 3]
            matched_keywords.extend(desc_words[:10])

        if not matched_keywords:
            keyword_score = 0.5  # Can't evaluate — neutral
            reasons.append("No keywords derivable from route — neutral score")
        else:
            hits = sum(1 for kw in matched_keywords if kw in text_lower)
            hit_ratio = hits / len(matched_keywords) if matched_keywords else 0
            if hit_ratio >= 0.3:
                keyword_score = min(1.0, 0.5 + hit_ratio)
                reasons.append(f"Route keywords matched: {hits}/{len(matched_keywords)} ({hit_ratio:.0%})")
            elif hit_ratio > 0:
                keyword_score = 0.3
                reasons.append(f"Few route keywords matched: {hits}/{len(matched_keywords)} ({hit_ratio:.0%})")
            else:
                keyword_score = 0.05
                reasons.append(f"No route keywords matched (0/{len(matched_keywords)})")

    # ── Dimension 4: Content Uniqueness (weight 0.20) ──────────────
    # Detect placeholder / default-page fingerprints.
    placeholder_signals = [
        "lorem ipsum", "placeholder", "coming soon", "under construction",
        "todo", "example.com", "test page", "hello world",
    ]
    placeholder_hits = sum(1 for sig in placeholder_signals if sig in text_lower)
    if placeholder_hits >= 2:
        unique_score = 0.1
        reasons.append(f"Multiple placeholder signals detected ({placeholder_hits})")
    elif placeholder_hits == 1:
        unique_score = 0.4
        reasons.append("One placeholder signal detected")
    elif text_len > 50:
        unique_score = 1.0
    else:
        unique_score = 0.5
        reasons.append("Very short content — can't assess uniqueness")

    # ── Weighted Average ───────────────────────────────────────────
    confidence = (
        length_score * 0.20
        + struct_score * 0.25
        + keyword_score * 0.35
        + unique_score * 0.20
    )
    confidence = round(max(0.0, min(1.0, confidence)), 2)

    # Alignment threshold: 0.45 (tuned to pass real pages, fail mismatches)
    aligned = confidence >= 0.45

    return {
        "aligned": aligned,
        "reasoning": "; ".join(reasons) if reasons else "Default assessment",
        "confidence": confidence,
    }


# ── Auth Route Heuristic (Iteration 10) ───────────────────────────────
# Routes matching these patterns are expected to return 3xx redirects
# (e.g., NextAuth /auth/signin redirects to provider). Treating 302 as
# "unreachable" causes unnecessary gate blocks.
_AUTH_ROUTE_PATTERNS = ["/auth/", "/login", "/signin", "/api/auth/", "/signout"]


def is_auth_route(route: str) -> bool:
    """Check if a route is an authentication-related path.

    Auth routes commonly return 3xx redirects (to auth providers, login
    pages, or callback handlers). These should NOT be flagged as
    unreachable.

    Args:
        route: The URL path (e.g., '/auth/signin')

    Returns:
        True if the route matches a known auth pattern.
    """
    route_lower = route.lower()
    return any(pattern in route_lower for pattern in _AUTH_ROUTE_PATTERNS)


def parse_route_expectation(entry) -> tuple:
    """Parse a route entry from verification_sitemap.json.

    Supports two formats:
    1. Plain string: "/dashboard" → path="/dashboard", expect="content"
    2. Dict with expect field: {"path": "/auth/signin", "expect": "redirect"}

    Valid expect values:
    - "content": Must return 200 with real content (default)
    - "redirect": 3xx is valid (auth routes, protected pages)
    - "skip": Don't check this route at all (API callbacks)
    - "content|redirect": Either 200 or 3xx is valid

    Returns:
        Tuple of (path: str, expect: str)
    """
    if isinstance(entry, str):
        return (entry, "content")
    elif isinstance(entry, dict):
        path = entry.get("path", entry.get("route", ""))
        expect = entry.get("expect", "content")
        return (path, expect)
    return (str(entry), "content")


def is_soft_404_content(body: str) -> bool:
    """Detect if page body is a soft-404 (HTTP 200 but 404 content).

    A soft-404 is detected when:
    1. The visible body (excluding <script>/<style> tags) matches known 404 patterns, OR
    2. The visible body has < 50 chars of real text content (near-empty page)

    Important: We strip <script> and <style> tags BEFORE pattern matching because
    Next.js RSC payloads embed the _not-found component definition in the <script>
    tags of EVERY page, causing false positives if we match against the raw HTML.

    Args:
        body: The raw HTML body string from curl.

    Returns:
        True if the page appears to be a soft 404.
    """
    if not body or not body.strip():
        return True

    # Strip <script>...</script> and <style>...</style> content before checking
    # This prevents false positives from Next.js RSC payloads (which embed
    # "404: This page could not be found" in __next_f JS data of every page)
    visible_body = re.sub(r'<script[^>]*>.*?</script>', ' ', body, flags=re.DOTALL | re.IGNORECASE)
    visible_body = re.sub(r'<style[^>]*>.*?</style>', ' ', visible_body, flags=re.DOTALL | re.IGNORECASE)

    # Check for explicit 404 patterns in visible content only
    for pattern in _SOFT_404_PATTERNS:
        if pattern.search(visible_body):
            return True

    # Check for near-empty pages (strip HTML tags to get text content)
    text_only = re.sub(r'<[^>]+>', ' ', visible_body)
    text_only = re.sub(r'\s+', ' ', text_only).strip()
    if len(text_only) < _MIN_CONTENT_LENGTH:
        return True

    return False


def extract_internal_links(project_dir: str) -> list[dict]:
    """Extract all internal <Link href> and <a href> from TSX/JSX files.

    Returns a list of dicts: [{"href": "/path", "file": "src/components/Navbar.tsx", "line": 5}]
    """
    links = []
    src_dir = os.path.join(project_dir, "src")
    if not os.path.isdir(src_dir):
        return links

    for root, _, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith((".tsx", ".jsx", ".ts", ".js")):
                continue
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, project_dir)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        for match in HREF_PATTERN.finditer(line):
                            href = match.group(1).strip()
                            # Skip external links
                            if any(href.startswith(p) for p in EXTERNAL_PREFIXES):
                                continue
                            # Skip empty hrefs
                            if not href or href == "/":
                                # "/" is valid — it's the root page
                                if href == "/":
                                    links.append({
                                        "href": href,
                                        "file": rel_path,
                                        "line": line_num,
                                    })
                                continue
                            # Normalize: remove trailing slash, query params, hash
                            href = href.split("?")[0].split("#")[0].rstrip("/")
                            if not href:
                                continue
                            links.append({
                                "href": href,
                                "file": rel_path,
                                "line": line_num,
                            })
            except (IOError, OSError):
                continue

    return links


def _is_route_group(name: str) -> bool:
    """Check if a directory name is a Next.js route group.

    Route groups are parenthesized directories like (dashboard), (app),
    (marketing). They are layout-only groupings that do NOT appear in URLs.
    """
    return name.startswith("(") and name.endswith(")")


def _route_exists(project_dir: str, href: str) -> bool:
    """Check if a Next.js App Router page file exists for the given href.

    Handles:
    - Static routes: /dashboard → src/app/dashboard/page.tsx
    - Dynamic routes: /audit/123 → src/app/audit/[id]/page.tsx (or [slug], etc.)
    - Route groups: /apply → src/app/(dashboard)/apply/page.tsx
    - Root: / → src/app/page.tsx or src/app/(app)/page.tsx
    """
    app_dir = os.path.join(project_dir, "src", "app")
    if not os.path.isdir(app_dir):
        return False

    if href == "/":
        # Check root page directly
        if any(
            os.path.isfile(os.path.join(app_dir, f"page.{ext}"))
            for ext in ("tsx", "jsx", "ts", "js")
        ):
            return True
        # Also check root page inside route groups: src/app/(app)/page.tsx
        for entry in os.listdir(app_dir):
            if _is_route_group(entry):
                group_dir = os.path.join(app_dir, entry)
                if _has_page_file(group_dir):
                    return True
        return False

    # Split href into segments: /dashboard/outreach → ["dashboard", "outreach"]
    segments = [s for s in href.strip("/").split("/") if s]
    if not segments:
        return False

    # Try exact match first
    route_dir = os.path.join(app_dir, *segments)
    if _has_page_file(route_dir):
        return True

    # Try dynamic segment + route group matching
    return _match_dynamic_route(app_dir, segments, 0)


def _has_page_file(directory: str) -> bool:
    """Check if a directory contains a non-stub page file.

    G-6 (ITR-24): Also rejects page files under _PAGE_MIN_BYTES bytes,
    which are likely scaffold stubs (e.g., `export default function P() {}`).
    """
    if not os.path.isdir(directory):
        return False
    for ext in ("tsx", "jsx", "ts", "js"):
        page_path = os.path.join(directory, f"page.{ext}")
        if os.path.isfile(page_path):
            try:
                if os.path.getsize(page_path) >= _PAGE_MIN_BYTES:
                    return True
            except OSError:
                continue
    return False


def _match_dynamic_route(current_dir: str, segments: list[str], depth: int) -> bool:
    """Recursively match route segments against dynamic [param] and (group) directories.

    Route groups (parenthesized dirs like (dashboard)) are transparent — they
    don't consume a URL segment. Dynamic params ([id], [...slug]) consume one.
    """
    if depth >= len(segments):
        # Check if current dir has a page file
        if _has_page_file(current_dir):
            return True
        # Also check inside route groups at this level
        # (the page might be at (group)/page.tsx instead of ./page.tsx)
        if os.path.isdir(current_dir):
            for entry in os.listdir(current_dir):
                if _is_route_group(entry):
                    group_path = os.path.join(current_dir, entry)
                    if _has_page_file(group_path):
                        return True
        return False

    if not os.path.isdir(current_dir):
        return False

    segment = segments[depth]

    # Try exact match
    exact_dir = os.path.join(current_dir, segment)
    if os.path.isdir(exact_dir):
        if _match_dynamic_route(exact_dir, segments, depth + 1):
            return True

    for entry in os.listdir(current_dir):
        entry_path = os.path.join(current_dir, entry)
        if not os.path.isdir(entry_path):
            continue

        # Route groups: (dashboard), (app), etc. — traverse WITHOUT consuming segment
        if _is_route_group(entry):
            if _match_dynamic_route(entry_path, segments, depth):
                return True

        # Dynamic segment match: [id], [slug], [...slug], [[...slug]]
        elif entry.startswith("[") and entry.endswith("]"):
            if _match_dynamic_route(entry_path, segments, depth + 1):
                return True

    return False


def check_route_reachability(project_dir: str) -> Optional[dict]:
    """Check that all internal <Link href> targets have corresponding page files.

    Returns:
        None if no links found (skip check)
        dict with:
            - has_missing: bool
            - missing_routes: list of href strings that have no page file
            - total_links: int
            - resolved_links: int
    """
    if not os.path.isdir(os.path.join(project_dir, "src", "app")):
        return None

    links = extract_internal_links(project_dir)
    if not links:
        return None

    # Deduplicate hrefs
    unique_hrefs = set(l["href"] for l in links)
    missing = []

    for href in sorted(unique_hrefs):
        if not _route_exists(project_dir, href):
            missing.append(href)
            # Log with source file for debugging
            sources = [l for l in links if l["href"] == href]
            for s in sources[:2]:
                logger.debug(f"Missing route {href} linked from {s['file']}:{s['line']}")

    if not missing:
        return {"has_missing": False, "missing_routes": [], 
                "total_links": len(unique_hrefs), "resolved_links": len(unique_hrefs)}

    return {
        "has_missing": True,
        "missing_routes": missing,
        "total_links": len(unique_hrefs),
        "resolved_links": len(unique_hrefs) - len(missing),
    }


# ── Dev Server Cache Management ────────────────────────────────────────
# 5-Why (Issue #9): Next.js dev server cache becomes stale after rapid
# agent file writes. The route reachability validator reports false 404s.
# Fix: Clear dev cache and restart before declaring a route as failed.


def detect_dev_cache_dirs(project_dir: str) -> list[str]:
    """Detect framework-specific cache directories.

    Returns a list of absolute paths to cache directories that exist.
    Supports: Next.js (.next), Vite (node_modules/.vite).
    """
    candidates = [
        os.path.join(project_dir, ".next"),
        os.path.join(project_dir, "node_modules", ".vite"),
    ]
    return [d for d in candidates if os.path.isdir(d)]


def clear_dev_cache(project_dir: str) -> int:
    """Clear all detected dev server cache directories.

    Returns the number of cache directories successfully removed.
    """
    cache_dirs = detect_dev_cache_dirs(project_dir)
    cleared = 0
    for cache_dir in cache_dirs:
        try:
            shutil.rmtree(cache_dir)
            logger.info(f"Cleared dev cache: {cache_dir}")
            cleared += 1
        except (OSError, PermissionError) as e:
            logger.warning(f"Failed to clear dev cache {cache_dir}: {e}")
    return cleared


def _restart_dev_server(port: int, project_dir: str, timeout: int = 30) -> bool:
    """Restart the dev server on the given port and wait for health.

    Uses subprocess to kill the process on the port, then waits for it
    to come back up. Returns True if the server is healthy after restart.
    """
    # Kill process on port
    try:
        subprocess.run(
            ["bash", "-c", f"kill $(lsof -t -i:{port}) 2>/dev/null || true"],
            capture_output=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    # Brief pause for port release
    time.sleep(1)

    # Poll for health (don't sleep — poll with verifiable state)
    start = time.time()
    while time.time() - start < timeout:
        try:
            proc = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--max-time", "2", f"http://localhost:{port}/"],
                capture_output=True, text=True, timeout=5,
            )
            code = int(proc.stdout.strip()) if proc.stdout.strip() else 0
            if 200 <= code < 500:
                logger.info(f"Dev server on port {port} is healthy (HTTP {code})")
                return True
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
            pass
        time.sleep(2)

    logger.warning(f"Dev server on port {port} did not recover within {timeout}s")
    return False


def _detect_dev_server_port(project_dir: str) -> Optional[int]:
    """Detect the dev server port from package.json scripts or common defaults."""
    pkg_path = os.path.join(project_dir, "package.json")
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path, "r") as f:
                pkg = json.load(f)
            scripts = pkg.get("scripts", {})
            dev_cmd = scripts.get("dev", "")
            # Look for --port or -p flags
            for flag in ["--port", "-p"]:
                if flag in dev_cmd:
                    parts = dev_cmd.split(flag)
                    if len(parts) > 1:
                        port_str = parts[1].strip().split()[0]
                        try:
                            return int(port_str)
                        except ValueError:
                            pass
        except (IOError, json.JSONDecodeError):
            pass
    # Next.js default
    return 3000


def curl_verify_routes(project_dir: str, port: Optional[int] = None,
                        timeout: int = 5) -> Optional[dict]:
    """Curl each route from verification_sitemap.json or extracted links.

    Returns:
        None if not a web project or no routes to check
        dict with:
            - all_reachable: bool
            - results: list of {route, status_code, reachable}
            - unreachable: list of route strings
            - summary: human-readable summary
    """
    if not os.path.isdir(os.path.join(project_dir, "src", "app")):
        return None

    # Get routes from sitemap first, fall back to extracted links
    routes = set()
    sitemap_path = os.path.join(project_dir, "verification_sitemap.json")
    if os.path.isfile(sitemap_path):
        try:
            with open(sitemap_path, "r") as f:
                sitemap = json.load(f)
            for route in sitemap.get("routes", []):
                if isinstance(route, dict):
                    routes.add(route.get("path", route.get("route", "")))
                elif isinstance(route, str):
                    routes.add(route)
        except (IOError, json.JSONDecodeError):
            pass

    # Also add routes from Link extraction
    links = extract_internal_links(project_dir)
    for link in links:
        routes.add(link["href"])

    if not routes:
        return None

    if port is None:
        port = _detect_dev_server_port(project_dir)

    base_url = f"http://localhost:{port}"
    results = []
    unreachable = []
    soft_404s = []
    error_bodies = {}  # route → truncated error body for gate messages

    for route in sorted(routes):
        url = f"{base_url}{route}"

        # ── Route Expectation Check (Iteration 10) ────────────────
        # Auto-detect auth routes and treat 3xx as valid.
        # This prevents gate blocks on routes like /auth/signin
        # which correctly redirect to auth providers.
        route_is_auth = is_auth_route(route)

        try:
            # First: get status code
            proc = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--max-time", str(timeout), url],
                capture_output=True, text=True, timeout=timeout + 2,
            )
            status_code = int(proc.stdout.strip()) if proc.stdout.strip() else 0

            # P0: API-aware route health classification
            # API routes (400-599) are acceptable — the handler exists, just no data.
            # Only connection refused and frontend errors are true failures.
            from python.helpers.quality_gate_api_awareness import classify_route_health
            health = classify_route_health(route, status_code if status_code > 0 else None)
            reachable = health.is_passing()

            # ── Auth redirect heuristic (Iteration 10) ────────────
            # If this is an auth route returning 3xx, mark as valid
            # and SKIP body checks (redirect body is always empty/minimal)
            if route_is_auth and 300 <= status_code < 400:
                results.append({
                    "route": route,
                    "status_code": status_code,
                    "reachable": True,
                    "soft_404": False,
                    "auth_redirect": True,
                })
                logger.info(
                    f"Route {route} returned {status_code} (expected auth redirect) ✅"
                )
                continue

            # Fetch body for: (a) soft-404 detection on 200s, (b) error body capture on non-200s
            is_soft_404 = False
            error_body_str = ""
            try:
                body_proc = subprocess.run(
                    ["curl", "-s", "--max-time", str(timeout), url],
                    capture_output=True, text=True, timeout=timeout + 2,
                )
                body_text = body_proc.stdout or ""

                if reachable and status_code == 200:
                    # Soft-404 detection
                    if body_text and is_soft_404_content(body_text):
                        is_soft_404 = True
                        reachable = False
                        soft_404s.append(route)
                        logger.warning(
                            f"Route {route} returned 200 but body is soft-404 "
                            f"(generic 404 content or near-empty page)"
                        )
                elif not reachable and body_text:
                    # Capture error body for non-200 routes (truncated to 200 chars)
                    error_body_str = body_text.strip()[:200]
                    error_bodies[route] = error_body_str
            except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass  # If body fetch fails, trust the status code

            result_entry = {
                "route": route,
                "status_code": status_code,
                "reachable": reachable,
                "soft_404": is_soft_404,
            }
            if error_body_str:
                result_entry["error_body"] = error_body_str
            results.append(result_entry)
            if not reachable:
                unreachable.append(route)
                if not is_soft_404:
                    logger.warning(f"Route {route} returned {status_code}")
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError) as e:
            results.append({
                "route": route,
                "status_code": 0,
                "reachable": False,
                "soft_404": False,
                "error": str(e),
            })
            unreachable.append(route)
            logger.warning(f"Route {route} failed: {e}")

    total = len(results)
    ok = total - len(unreachable)

    # ── Cache-Aware Retry (5-Why Issue #9) ────────────────────────────
    # If ANY routes are unreachable, clear dev cache and restart before
    # declaring failure. This prevents false 404s from stale .next cache
    # that cause destructive agent "fixes" (moving files out of route groups).
    if unreachable and detect_dev_cache_dirs(project_dir):
        logger.info(
            f"Route reachability: {len(unreachable)} route(s) unreachable. "
            f"Clearing dev cache and retrying before declaring failure..."
        )
        cleared = clear_dev_cache(project_dir)
        if cleared > 0:
            # Restart dev server and wait for health
            _restart_dev_server(port, project_dir, timeout=30)

            # Re-check ONLY the unreachable routes
            retry_unreachable = []
            retry_soft_404s = []
            for route in unreachable:
                url = f"{base_url}{route}"
                route_is_auth = is_auth_route(route)
                try:
                    proc = subprocess.run(
                        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                         "--max-time", str(timeout), url],
                        capture_output=True, text=True, timeout=timeout + 2,
                    )
                    status_code = int(proc.stdout.strip()) if proc.stdout.strip() else 0
                    reachable = 200 <= status_code < 400

                    if route_is_auth and 300 <= status_code < 400:
                        reachable = True

                    if reachable and status_code == 200:
                        # Re-check for soft-404
                        try:
                            body_proc = subprocess.run(
                                ["curl", "-s", "--max-time", str(timeout), url],
                                capture_output=True, text=True, timeout=timeout + 2,
                            )
                            if body_proc.stdout and is_soft_404_content(body_proc.stdout):
                                reachable = False
                                retry_soft_404s.append(route)
                        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                            pass

                    if not reachable:
                        retry_unreachable.append(route)
                    else:
                        logger.info(
                            f"Route {route} now reachable after cache clear (HTTP {status_code}) ✅"
                        )
                        # Update the result entry
                        for r in results:
                            if r["route"] == route:
                                r["status_code"] = status_code
                                r["reachable"] = True
                                r["soft_404"] = False
                                r["cache_retry"] = True
                                break
                except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
                    retry_unreachable.append(route)

            # Update unreachable/soft_404 lists with retry results
            unreachable = retry_unreachable
            soft_404s = [s for s in soft_404s if s in retry_unreachable] + retry_soft_404s
            ok = total - len(unreachable)
            if not unreachable:
                logger.info(
                    "All previously unreachable routes now reachable after cache clear ✅"
                )

    summary = f"{ok}/{total} routes reachable (HTTP 2xx/3xx with real content)"
    if unreachable:
        summary += f". Unreachable: {', '.join(unreachable[:5])}"
    if soft_404s:
        summary += f". Soft-404s: {', '.join(soft_404s[:5])}"

    result_dict = {
        "all_reachable": len(unreachable) == 0,
        "results": results,
        "unreachable": unreachable,
        "soft_404s": soft_404s,
        "error_bodies": error_bodies,
        "summary": summary,
    }

    # ── Evidence Persistence (RCA-233 P1) ──────────────────────────
    # Write route reachability results to .agix.proj/verification/
    # so post-mortem audits can inspect per-route L2 scores.
    try:
        from python.helpers.evidence_persistence import write_evidence
        evidence_data = {
            "routes_checked": total,
            "all_reachable": result_dict["all_reachable"],
            "unreachable": unreachable,
            "soft_404s": soft_404s,
            "per_route_results": results,
            "cache_retry_performed": any(
                r.get("cache_retry", False) for r in results
            ),
        }
        write_evidence(project_dir, "route_reachability_evidence", evidence_data)
        logger.info(
            f"Route reachability evidence persisted to "
            f".agix.proj/verification/route_reachability_evidence.json"
        )
    except Exception as ev_err:
        logger.warning(f"Failed to persist route evidence (non-fatal): {ev_err}")

    return result_dict


def get_verification_evidence(project_dir: str, port: Optional[int] = None) -> Optional[dict]:
    """Generate a complete verification evidence report.

    Combines static route reachability + live curl checks into a single
    evidence dict the orchestrator can validate against the architect's plan.

    Returns:
        None if not a web project
        dict with:
            - static_check: route_reachability result
            - live_check: curl_verify_routes result (or None if dev server not running)
            - all_passed: bool
            - evidence_summary: human-readable summary
    """
    if not os.path.isdir(os.path.join(project_dir, "src", "app")):
        return None

    static = check_route_reachability(project_dir)
    live = curl_verify_routes(project_dir, port=port)

    # Determine overall pass/fail
    static_ok = static is None or not static.get("has_missing", False)
    live_ok = live is None or live.get("all_reachable", False)
    all_passed = static_ok and live_ok

    parts = []
    if static and static.get("has_missing"):
        parts.append(f"Static: {len(static['missing_routes'])} missing page files")
    elif static:
        parts.append(f"Static: {static['total_links']} routes resolved ✅")
    if live:
        parts.append(f"Live: {live['summary']}")
    else:
        parts.append("Live: dev server not reachable (skipped)")

    return {
        "static_check": static,
        "live_check": live,
        "all_passed": all_passed,
        "evidence_summary": " | ".join(parts),
    }


# ── Build Freshness Check ──────────────────────────────────────────────
# 5-Why: Agent adds files after `npm run build` → pages exist in src/ but
# return 404 from production server because the build output is stale.

def check_build_freshness(project_dir: str) -> Optional[dict]:
    """Check if the production build is newer than all source files.

    Returns:
        None if not a web project or no build exists.
        dict with:
            - stale: bool (True if source files are newer than build)
            - newer_files: list of source files newer than the build
            - build_mtime: float (mtime of newest build file)
            - newest_source_mtime: float (mtime of newest source file)
    """
    src_dir = os.path.join(project_dir, "src", "app")
    if not os.path.isdir(src_dir):
        return None

    # Find build output directory (Next.js: .next/server/app, Vite: dist/)
    build_dirs = [
        os.path.join(project_dir, ".next", "server", "app"),
        os.path.join(project_dir, "dist"),
        os.path.join(project_dir, "build"),
    ]
    build_dir = None
    for d in build_dirs:
        if os.path.isdir(d):
            build_dir = d
            break

    if build_dir is None:
        return None

    # Get newest build file mtime
    build_mtime = 0
    for root, _, files in os.walk(build_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                mt = os.path.getmtime(fpath)
                build_mtime = max(build_mtime, mt)
            except OSError:
                pass

    if build_mtime == 0:
        return None

    # Find source files newer than the build
    newer_files = []
    newest_source = 0
    for root, _, files in os.walk(src_dir):
        for fname in files:
            if fname.endswith(('.tsx', '.ts', '.jsx', '.js', '.css')):
                fpath = os.path.join(root, fname)
                try:
                    mt = os.path.getmtime(fpath)
                    newest_source = max(newest_source, mt)
                    if mt > build_mtime:
                        rel = os.path.relpath(fpath, project_dir)
                        newer_files.append(rel)
                except OSError:
                    pass

    return {
        "stale": len(newer_files) > 0,
        "newer_files": newer_files,
        "build_mtime": build_mtime,
        "newest_source_mtime": newest_source,
    }


# ── M-7 Fix: Navigation Existence Check ──────────────────────────────
# Multi-page apps with zero navigation components are unusable.
# The old code returned None (vacuous pass) when zero links existed.

def check_nav_existence(project_dir: str) -> Optional[dict]:
    """Check that multi-page apps have a navigation component.

    M-7 Fix: Route reachability returned None (vacuous pass) when zero
    links existed. For multi-page apps, zero navigation links means the
    user can't navigate between pages — this is a hard failure, not a pass.

    Returns:
        None if single-page app or nav exists
        dict with:
            - has_nav: bool
            - page_count: int
            - message: str
    """
    app_dir = os.path.join(project_dir, "src", "app")
    if not os.path.isdir(app_dir):
        return None

    # Count page files to determine if multi-page
    page_count = 0
    for root, dirs, files in os.walk(app_dir):
        for fname in files:
            if fname.startswith("page.") and fname.endswith((".tsx", ".jsx", ".ts", ".js")):
                page_count += 1

    if page_count <= 1:
        return None  # Single-page app — nav is optional

    # Multi-page: check for nav-related components
    links = extract_internal_links(project_dir)
    nav_file_patterns = ["nav", "sidebar", "header", "menu", "topbar", "appbar"]
    has_nav_component = False

    src_dir = os.path.join(project_dir, "src")
    if os.path.isdir(src_dir):
        for root, _, files in os.walk(src_dir):
            for fname in files:
                fname_lower = fname.lower()
                if any(p in fname_lower for p in nav_file_patterns):
                    has_nav_component = True
                    break
            if has_nav_component:
                break

    if links and has_nav_component:
        return None  # Navigation exists

    return {
        "has_nav": False,
        "page_count": page_count,
        "link_count": len(links),
        "has_nav_component": has_nav_component,
        "message": (
            f"Multi-page app ({page_count} pages) but no navigation component detected. "
            f"Found {len(links)} internal links, nav component: {has_nav_component}. "
            f"Users cannot navigate between pages without a navbar/sidebar. "
            f"Create a shared navigation component in layout.tsx."
        ),
    }


# ── Nav-Link Consistency Check ──────────────────────────────────────────
# 5-Why: Agent creates sidebar with links to /dashboard/prospects etc.
# but never creates the page files. Gate only checked sitemap routes,
# not the actual href values in layout components.

def check_nav_link_consistency(project_dir: str) -> dict:
    """Cross-check href values in layout components against page files.

    Parses all href="..." from layout components (navbar, sidebar, header,
    footer) and verifies each internal route has a corresponding page file
    in the Next.js App Router structure.

    Returns:
        dict with:
            - missing_pages: list of routes that have nav links but no page file
            - all_links: list of all extracted internal hrefs
            - matched_links: list of hrefs that have page files
    """
    layout_dir = os.path.join(project_dir, "src", "components", "layout")
    app_dir = os.path.join(project_dir, "src", "app")

    all_links = set()

    # Scan layout components for href values
    scan_dirs = [layout_dir]
    # Also check src/components for common nav patterns
    components_dir = os.path.join(project_dir, "src", "components")
    if os.path.isdir(components_dir):
        scan_dirs.append(components_dir)

    for scan_dir in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        for root, _, files in os.walk(scan_dir):
            for fname in files:
                if fname.endswith(('.tsx', '.jsx', '.ts', '.js')):
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        for match in HREF_PATTERN.finditer(content):
                            href = match.group(1)
                            all_links.add(href)
                    except IOError:
                        pass

    # Filter to internal routes only (exclude anchors, external, etc.)
    internal_links = set()
    for href in all_links:
        if href.startswith(EXTERNAL_PREFIXES):
            continue
        if href == "#" or href.startswith("#"):
            continue
        if href.startswith("/"):
            internal_links.add(href)

    # For each internal link, check if a page file exists
    # Build a map of existing page routes
    existing_routes = set()
    dynamic_segments = []  # Track dynamic route patterns like [slug]
    if os.path.isdir(app_dir):
        for root, dirs, files in os.walk(app_dir):
            for fname in files:
                if fname in ("page.tsx", "page.ts", "page.jsx", "page.js"):
                    rel = os.path.relpath(root, app_dir)
                    if rel == ".":
                        existing_routes.add("/")
                    else:
                        # Strip route groups from path: (dashboard)/apply → apply
                        parts = rel.replace(os.sep, "/").split("/")
                        parts = [p for p in parts if not _is_route_group(p)]
                        if not parts:
                            existing_routes.add("/")
                        else:
                            route = "/" + "/".join(parts)
                            existing_routes.add(route)
                            # Track dynamic segments
                            if "[" in route:
                                dynamic_segments.append(route)

    missing_pages = []
    matched_links = []

    for link in sorted(internal_links):
        # Exact match?
        if link in existing_routes:
            matched_links.append(link)
            continue

        # Dynamic route match? e.g. /audit/test-business matches /audit/[slug]
        matched = False
        for dynamic_route in dynamic_segments:
            # Convert /audit/[slug] to regex /audit/[^/]+
            pattern = re.sub(r'\[[^\]]+\]', '[^/]+', dynamic_route)
            if re.fullmatch(pattern, link):
                matched = True
                matched_links.append(link)
                break

        if not matched:
            missing_pages.append(link)

    return {
        "missing_pages": missing_pages,
        "all_links": sorted(internal_links),
        "matched_links": matched_links,
    }


# ── API Response Quality Check ──────────────────────────────────────────
# 5-Why: Agent generates API routes that return "undefined" or null values
# because request body isn't validated. Gate only checked HTTP status codes.

def check_api_response_quality(response_body: str, route: str) -> list:
    """Check API response body for quality issues.

    Flags:
    - "undefined" appearing in JSON string values (LLM hallucination artifact)
    - Excessive null values (> 50% of fields are null)
    - Non-JSON response for API routes that should return JSON

    Args:
        response_body: Raw response body string.
        route: API route path (for context in messages).

    Returns:
        List of issue strings. Empty list = clean response.
    """
    issues = []
    if not response_body or not response_body.strip():
        return issues  # Empty is not inherently bad for POST responses

    # Try to parse as JSON
    try:
        data = json.loads(response_body)
    except (json.JSONDecodeError, ValueError):
        # Not JSON — could be valid (HTML page, etc.) or could indicate error
        return issues

    # Skip error responses (these are intentional)
    if isinstance(data, dict) and "error" in data:
        return issues

    # Skip empty arrays (valid — no data yet)
    if isinstance(data, list) and len(data) == 0:
        return issues

    # Check for "undefined" in string values (LLM artifact)
    def _check_undefined(obj, path=""):
        if isinstance(obj, str):
            if "undefined" in obj.lower():
                issues.append(
                    f"Route {route}: 'undefined' in value at {path}: "
                    f"'{obj[:80]}'"
                )
        elif isinstance(obj, dict):
            null_count = 0
            total = len(obj)
            for k, v in obj.items():
                _check_undefined(v, f"{path}.{k}")
                if v is None:
                    null_count += 1
            # Flag excessive nulls (> 50% of fields, min 2 null fields)
            if total > 0 and null_count >= 2 and null_count / total > 0.5:
                issues.append(
                    f"Route {route}: {null_count}/{total} fields are null at {path}"
                )
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _check_undefined(item, f"{path}[{i}]")

    _check_undefined(data)
    return issues


# ── Architect Plan vs Implementation Coverage ──────────────────────────
# 5-Why Root Cause: The architect's plan is ephemeral — produced as chat
# output and never persisted. E2E gates validate what EXISTS in the
# filesystem (circular), not what was PLANNED. An agent can build 4 of 7
# planned pages and the gate says "all good."
#
# Fix: The architect persists its plan as architect_plan.json. This
# validator cross-checks planned_routes against actual page.tsx files.


def check_plan_vs_implementation(project_dir: str):
    """Cross-check architect_plan.json routes vs actual page files.

    Reads the architect's persisted plan and verifies that every planned
    route has a corresponding page.tsx file in the Next.js App Router tree.

    Args:
        project_dir: Root directory of the project

    Returns:
        None if no architect_plan.json exists or has no planned_routes
        Dict with:
            - planned_count: int
            - implemented_count: int
            - missing_routes: list of missing route paths
            - coverage_ratio: float (0.0 - 1.0)
    """
    plan_path = _planning_path(project_dir, "architect_plan")
    if not os.path.isfile(plan_path):
        # Fallback 1: try root-level architect_plan.json (legacy / unit test)
        plan_path = os.path.join(project_dir, "architect_plan.json")
    if not os.path.isfile(plan_path):
        # Fallback 2: try root-level architect-plan.json
        plan_path = os.path.join(project_dir, "architect-plan.json")
    if not os.path.isfile(plan_path):
        return None

    try:
        with open(plan_path, "r") as f:
            plan = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return None

    planned_routes = plan.get("planned_routes", [])
    if not planned_routes:
        return None

    src_app = os.path.join(project_dir, "src", "app")
    implemented = []
    missing = []

    for route in planned_routes:
        # Normalize route to filesystem path
        # "/" → src/app/page.tsx
        # "/dashboard" → src/app/dashboard/page.tsx
        # "/reviews/[id]" → src/app/reviews/[id]/page.tsx
        if route == "/":
            page_dir = src_app
        else:
            route_path = route.lstrip("/")
            page_dir = os.path.join(src_app, route_path)

        # Check for page file in any supported extension
        found = False
        for ext in ("page.tsx", "page.jsx", "page.js", "page.ts"):
            if os.path.isfile(os.path.join(page_dir, ext)):
                found = True
                break

        if found:
            implemented.append(route)
        else:
            missing.append(route)

    planned_count = len(planned_routes)
    implemented_count = len(implemented)

    return {
        "planned_count": planned_count,
        "implemented_count": implemented_count,
        "missing_routes": missing,
        "coverage_ratio": round(
            implemented_count / planned_count, 2
        ) if planned_count > 0 else 1.0,
    }


