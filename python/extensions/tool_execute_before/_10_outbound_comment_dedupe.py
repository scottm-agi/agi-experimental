"""
UNIVERSAL OUTBOUND COMMENT DEDUPLICATION

Intercepts ALL outbound comment calls — both raw MCP (github.add_issue_comment,
forgejo.create_issue_comment) AND any other pathway — and ensures:

1. Queries existing comments on the target issue via API
2. Generates a hash from the comment content
3. If a comment with the same hash already exists → SKIPS the post
4. If not → auto-appends `<!-- agix-id: HASH -->` tag to the body

This is the SAME dedup logic as `comment_github()` in github.py, but applied
universally at the extension layer so ALL outbound paths are covered.

Design Philosophy:
- Deduplication at ALL times from ALL systems — full stop.
- The choice of tool (raw MCP vs repository_automation) is irrelevant.
- Users and agents can use whatever tool they want; dedupe happens regardless.

Fixes:
- Issue #704: Duplicate TDD comments from build agents using raw MCP
- Webhook feedback loops: auto-tagged comments are recognized as self-generated
"""

from __future__ import annotations
import logging
import time
from typing import Any, Dict, Optional, Tuple

import requests

from python.helpers.extension import Extension

logger = logging.getLogger("extensions.outbound_dedupe")

# ─────────────────────────────────────────────────────────────────────────────
# TOOL → PROVIDER MAPPING
# ─────────────────────────────────────────────────────────────────────────────

COMMENT_TOOLS: Dict[str, Dict[str, str]] = {
    # GitHub MCP
    "github.add_issue_comment": {
        "body_key": "body",
        "issue_key": "issue_number",
        "owner_key": "owner",
        "repo_key": "repo",
        "provider": "github",
    },
    # Forgejo MCP
    "forgejo.create_issue_comment": {
        "body_key": "body",
        "issue_key": "index",
        "owner_key": "owner",
        "repo_key": "repo",
        "provider": "forgejo",
    },
}

# The agix-id tag format (must match dedup_service.py and comment_github)
AGIX_TAG_PATTERN = "<!-- agix-id:"


# ─────────────────────────────────────────────────────────────────────────────
# IN-MEMORY CACHE (fast path for rapid-fire dupes within same process)
# ─────────────────────────────────────────────────────────────────────────────

_recent_hashes: Dict[str, float] = {}  # hash_key → timestamp
_CACHE_TTL = 1800  # 30 minutes

# Pending hashes: tagged in before-hook but not yet confirmed by after-hook.
# Keyed by cache_key → timestamp. Cleared after success OR after TTL.
_pending_hashes: Dict[str, float] = {}


def _cache_key(owner: str, repo: str, issue_id: Any, hash_id: str) -> str:
    return f"{owner}/{repo}#{issue_id}:{hash_id}"


def _check_cache(key: str) -> bool:
    """Return True if this hash was recently posted (within TTL)."""
    now = time.time()
    # Prune expired entries (lightweight, only when checking)
    expired = [k for k, ts in _recent_hashes.items() if now - ts > _CACHE_TTL]
    for k in expired:
        del _recent_hashes[k]
    return key in _recent_hashes


def _add_to_cache(key: str):
    _recent_hashes[key] = time.time()


def _mark_pending(key: str):
    """Mark a hash as pending (tagged but not yet confirmed)."""
    _pending_hashes[key] = time.time()


def _confirm_pending(key: str):
    """Confirm a pending hash — move to confirmed cache."""
    if key in _pending_hashes:
        del _pending_hashes[key]
    _add_to_cache(key)


def _cancel_pending(key: str):
    """Cancel a pending hash — the MCP call failed, allow retry."""
    _pending_hashes.pop(key, None)


# ─────────────────────────────────────────────────────────────────────────────
# HASH GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def _generate_hash(issue_id: Any, body: str) -> str:
    """Generate dedup hash — same algorithm as comment_github() in github.py."""
    from python.helpers.hashing import content_hash_short
    raw = f"{issue_id}:{body[:100]}"
    return content_hash_short(raw, length=12)


def _body_has_agix_tag(body: str) -> bool:
    return AGIX_TAG_PATTERN in body


# ─────────────────────────────────────────────────────────────────────────────
# API-LEVEL DUPLICATE CHECK
# ─────────────────────────────────────────────────────────────────────────────

def _get_api_config(provider: str, owner: str, repo: str) -> Optional[Tuple[str, Dict[str, str]]]:
    """
    Build the API URL and headers for checking existing comments.
    Uses credentials.py to resolve tokens (same as the rest of the system).
    Returns (base_url, headers) or None if credentials unavailable.
    """
    try:
        if provider == "github":
            from python.helpers.credentials import get_github_credentials
            creds = get_github_credentials(params={"owner": owner, "repo": repo})
            if not creds.token:
                logger.warning("[OutboundDedupe] No GitHub token available for API check")
                return None
            base_url = "https://api.github.com"
            headers = {
                "Authorization": f"token {creds.token}",
                "Accept": "application/vnd.github.v3+json",
            }
            return base_url, headers

        elif provider == "forgejo":
            from python.helpers.credentials import get_forgejo_credentials
            creds = get_forgejo_credentials(params={"owner": owner, "repo": repo})
            if not creds.token or not creds.url:
                logger.warning("[OutboundDedupe] No Forgejo token/URL available for API check")
                return None
            base_url = f"{creds.url}/api/v1"
            headers = {
                "Authorization": f"token {creds.token}",
                "Accept": "application/json",
            }
            return base_url, headers

    except Exception as e:
        logger.error(f"[OutboundDedupe] Failed to get credentials for {provider}: {e}")

    return None


def _check_existing_comments(
    provider: str,
    owner: str,
    repo: str,
    issue_id: Any,
    hash_id: str,
) -> bool:
    """
    Query the issue's existing comments via REST API and check if a comment
    with the same agix-id hash already exists.

    Returns True if a duplicate is found (should skip posting).
    This is the SAME logic as comment_github() but provider-agnostic.
    """
    api_config = _get_api_config(provider, owner, repo)
    if not api_config:
        # Can't check — allow the post but still tag it
        return False

    base_url, headers = api_config
    hash_tag_needle = f"agix-id: {hash_id}"

    try:
        url = f"{base_url}/repos/{owner}/{repo}/issues/{issue_id}/comments?per_page=30"
        resp = requests.get(url, headers=headers, timeout=10)

        if resp.status_code != 200:
            logger.warning(
                f"[OutboundDedupe] API check returned {resp.status_code} for "
                f"{owner}/{repo}#{issue_id} — allowing post"
            )
            return False

        comments = resp.json()
        for c in comments:
            c_body = c.get("body", "")
            if hash_tag_needle in c_body:
                logger.info(
                    f"[OutboundDedupe] DUPLICATE DETECTED — comment with "
                    f"agix-id:{hash_id} already exists on {owner}/{repo}#{issue_id}"
                )
                return True

    except requests.Timeout:
        logger.warning("[OutboundDedupe] API check timed out — allowing post")
    except Exception as e:
        logger.error(f"[OutboundDedupe] API check failed: {e} — allowing post")

    return False


# ─────────────────────────────────────────────────────────────────────────────
# EXTENSION
# ─────────────────────────────────────────────────────────────────────────────

class OutboundCommentDedupe(Extension):
    # Context-aware: only for comment tools (GitHub/Forgejo MCP)
    TOOLS = frozenset({"add_issue_comment", "create_issue_comment", "edit_issue_comment", "add_comment_to_pending_review"})

    """
    Universal outbound comment deduplication.

    Intercepts MCP comment tool calls and:
    1. Generates a content hash
    2. Checks in-memory cache for rapid-fire duplicates
    3. Queries the issue's existing comments via API for hash matches
    4. If duplicate found → raises exception to block the post
    5. If no duplicate → auto-appends <!-- agix-id: HASH --> tag

    This ensures dedup regardless of whether the agent uses
    repository_automation or raw MCP tools.
    """

    async def execute(self, **kwargs):
        tool_name = kwargs.get("tool_name", "")
        tool_args = kwargs.get("tool_args")

        if not tool_name or not tool_args or not isinstance(tool_args, dict):
            return

        # Normalize tool name for lookup
        normalized = tool_name.strip().lower()

        # Check if this is a comment tool we intercept
        tool_config = COMMENT_TOOLS.get(normalized)
        if not tool_config:
            return

        # Extract fields
        body_key = tool_config["body_key"]
        issue_key = tool_config["issue_key"]
        owner_key = tool_config["owner_key"]
        repo_key = tool_config["repo_key"]
        provider = tool_config["provider"]

        body = tool_args.get(body_key, "")
        issue_id = tool_args.get(issue_key, "unknown")
        owner = tool_args.get(owner_key, "")
        repo = tool_args.get(repo_key, "")

        if not body:
            return

        # If body already has a agix-id tag, dedup was already handled upstream
        # (e.g., by comment_github() in repository_automation). Don't double-process.
        if _body_has_agix_tag(body):
            logger.debug(
                f"[OutboundDedupe] Comment to {owner}/{repo}#{issue_id} already "
                f"has agix-id tag — skipping dedupe (already handled upstream)."
            )
            return

        # Generate hash
        hash_id = _generate_hash(issue_id, body)
        cache_key = _cache_key(owner, repo, issue_id, hash_id)

        # ── Layer 1: In-memory cache (fast, catches rapid-fire dupes) ──
        if _check_cache(cache_key):
            logger.info(
                f"[OutboundDedupe] BLOCKED (cache hit) — duplicate comment to "
                f"{owner}/{repo}#{issue_id} with hash {hash_id}"
            )
            raise Exception(
                f"DUPLICATE COMMENT BLOCKED: A comment with the same content "
                f"was already posted to {owner}/{repo}#{issue_id} recently. "
                f"The comment has been skipped to prevent duplicates."
            )

        # ── Layer 2: API check (robust, catches cross-process/restart dupes) ──
        if owner and repo:
            is_dup = _check_existing_comments(provider, owner, repo, issue_id, hash_id)
            if is_dup:
                # Add to cache so future rapid-fire attempts also get caught
                _add_to_cache(cache_key)
                raise Exception(
                    f"DUPLICATE COMMENT BLOCKED: A comment with the same content "
                    f"already exists on {owner}/{repo}#{issue_id}. "
                    f"The comment has been skipped to prevent duplicates."
                )

        # ── No duplicate found → tag and allow ──
        hash_tag = f"\n\n<!-- agix-id: {hash_id} -->"
        tool_args[body_key] = body + hash_tag
        # CRITICAL FIX: Do NOT add to confirmed cache yet.
        # Mark as pending — the after-hook will confirm if MCP call succeeded.
        _mark_pending(cache_key)

        logger.info(
            f"[OutboundDedupe] Tagged comment to {owner}/{repo}#{issue_id} "
            f"with agix-id: {hash_id} (tool={normalized}) [PENDING confirmation]"
        )
