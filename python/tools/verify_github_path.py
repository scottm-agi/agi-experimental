"""
verify_github_path Tool

Prevents path hallucinations when agents construct GitHub file URLs.
Validates that a file path actually exists in a GitHub repository by
querying the GitHub Contents API, and sanitizes common path errors
(doubled segments, wrong prefixes, etc.).

Usage:
    tool_name: verify_github_path
    tool_args:
        path: "docs/main/docs/mockups/dashboard-abc123.png"
        owner: "your-bot-username"
        repo: "my-repo"
        branch: "main"   # optional, defaults to "main"
"""
from __future__ import annotations

import logging
import os
import re
import requests
from typing import Any, Dict, Optional, TYPE_CHECKING

from python.helpers.tool import Tool, Response
from python.helpers.credentials import get_github_credentials as _get_github_creds

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger("verify_github_path")

# ── Known valid top-level directories for mockup assets ──────────────────────
# These are the canonical prefixes used by webhook_handler.upload_attachments_to_github.
CANONICAL_MOCKUP_PREFIX = "docs/mockups/"

# Common hallucination patterns: doubled or tripled path segments
# e.g. "docs/main/docs/mockups/" → should be "docs/mockups/"
# e.g. "docs/docs/mockups/"      → should be "docs/mockups/"
HALLUCINATION_PATTERNS = [
    # Pattern: docs/<anything>/docs/mockups/  →  docs/mockups/
    (re.compile(r"^docs/[^/]+/docs/mockups/"), CANONICAL_MOCKUP_PREFIX),
    # Pattern: docs/docs/mockups/  →  docs/mockups/
    (re.compile(r"^docs/docs/mockups/"), CANONICAL_MOCKUP_PREFIX),
    # Pattern: main/docs/mockups/  →  docs/mockups/
    (re.compile(r"^main/docs/mockups/"), CANONICAL_MOCKUP_PREFIX),
    # Pattern: docs/main/mockups/  →  docs/mockups/
    (re.compile(r"^docs/main/mockups/"), CANONICAL_MOCKUP_PREFIX),
    # Pattern: mockups/  →  docs/mockups/  (missing parent)
    (re.compile(r"^mockups/"), CANONICAL_MOCKUP_PREFIX),
]


def sanitize_github_path(path: str) -> str:
    """
    Sanitize a GitHub file path by fixing common hallucination patterns.

    Args:
        path: The proposed file path (e.g. "docs/main/docs/mockups/img.png")

    Returns:
        The sanitized path (e.g. "docs/mockups/img.png")
    """
    if not path:
        return path

    # Strip leading/trailing whitespace and slashes
    path = path.strip().strip("/")

    # Apply hallucination pattern fixes
    for pattern, replacement in HALLUCINATION_PATTERNS:
        if pattern.match(path):
            path = pattern.sub(replacement, path)
            logger.info(f"[sanitize_github_path] Fixed hallucinated path → {path}")
            break

    # Remove any double slashes
    while "//" in path:
        path = path.replace("//", "/")

    return path


def sanitize_github_url(url: str) -> str:
    """
    Sanitize a full GitHub URL by extracting and fixing the path component.

    Handles URLs like:
        https://github.com/owner/repo/raw/main/docs/main/docs/mockups/file.png
        https://raw.githubusercontent.com/owner/repo/main/docs/docs/mockups/file.png

    Args:
        url: The full GitHub URL

    Returns:
        The sanitized URL with corrected path
    """
    if not url:
        return url

    # Pattern: github.com/{owner}/{repo}/raw/{branch}/{path}
    gh_match = re.match(
        r"^(https?://github\.com/[^/]+/[^/]+/raw/[^/]+/)(.+)$", url
    )
    if gh_match:
        prefix, file_path = gh_match.groups()
        sanitized = sanitize_github_path(file_path)
        return prefix + sanitized

    # Pattern: raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}
    raw_match = re.match(
        r"^(https?://raw\.githubusercontent\.com/[^/]+/[^/]+/[^/]+/)(.+)$", url
    )
    if raw_match:
        prefix, file_path = raw_match.groups()
        sanitized = sanitize_github_path(file_path)
        return prefix + sanitized

    return url


class VerifyGithubPath(Tool):
    """
    Tool to verify and sanitize GitHub file paths before they are used
    in issue bodies, comments, or markdown references.

    Prevents path hallucinations by:
    1. Sanitizing known doubled-path patterns
    2. Verifying the file actually exists via GitHub Contents API
    3. Returning the correct raw URL for embedding
    """

    async def execute(self, **kwargs) -> Response:
        path = self.args.get("path", "")
        url = self.args.get("url", "")
        owner = self.args.get("owner", "")
        repo = self.args.get("repo", "")
        branch = self.args.get("branch", "main")

        # ── Resolve credentials ──────────────────────────────────────────
        try:
            creds = _get_github_creds(context=self.agent.context, params=self.args)
            if not owner:
                owner = creds.owner
            if not repo:
                repo = creds.repo
        except Exception as e:
            logger.warning(f"[VerifyGithubPath] Could not load credentials: {e}")

        if not owner or not repo:
            return Response(
                message="ERROR: owner and repo are required (pass explicitly or configure GitHub credentials).",
                break_loop=False,
            )

        # ── If a URL was provided, extract path from it ──────────────────
        if url and not path:
            extracted = self._extract_path_from_url(url, owner, repo)
            if extracted:
                path = extracted

        if not path:
            return Response(
                message="ERROR: Either 'path' or 'url' is required.",
                break_loop=False,
            )

        # ── Step 1: Sanitize the path ────────────────────────────────────
        original_path = path
        sanitized_path = sanitize_github_path(path)
        was_fixed = sanitized_path != original_path

        # ── Step 2: Verify via GitHub API ────────────────────────────────
        verified, exists = await self._verify_path_exists(
            owner, repo, sanitized_path, branch
        )

        # ── Step 3: Build corrected URL ──────────────────────────────────
        from urllib.parse import quote as url_quote
        encoded = url_quote(sanitized_path, safe="/")
        corrected_url = f"https://github.com/{owner}/{repo}/raw/{branch}/{encoded}"

        # ── Log for observability ────────────────────────────────────────
        self.log = self.agent.context.log.log(
            type="info",
            heading=f"{self.agent.agent_name}: Verify GitHub Path",
            content=(
                f"Original: {original_path}\n"
                f"Sanitized: {sanitized_path}\n"
                f"Fixed: {was_fixed}\n"
                f"Exists: {exists}\n"
                f"URL: {corrected_url}"
            ),
        )

        # ── Return structured result ─────────────────────────────────────
        if exists:
            msg = (
                f"✅ **Path verified**: `{sanitized_path}`\n"
                f"**URL**: {corrected_url}\n"
            )
            if was_fixed:
                msg += f"⚠️ **Path was corrected** from `{original_path}` → `{sanitized_path}`\n"
            return Response(message=msg, break_loop=False)
        else:
            # File doesn't exist — but sanitized path may still be correct
            # (file might not have been uploaded yet)
            msg = (
                f"⚠️ **Path not found in repo**: `{sanitized_path}`\n"
                f"**Expected URL**: {corrected_url}\n"
            )
            if was_fixed:
                msg += f"🔧 **Path was sanitized** from `{original_path}` → `{sanitized_path}`\n"
            msg += (
                f"\n**Recommendation**: Use the sanitized path `{sanitized_path}` — "
                f"the canonical mockup directory is `docs/mockups/`."
            )
            return Response(message=msg, break_loop=False)

    def _extract_path_from_url(self, url: str, owner: str, repo: str) -> str:
        """Extract the file path from a GitHub URL."""
        # github.com/{owner}/{repo}/raw/{branch}/{path}
        pattern = rf"https?://github\.com/{re.escape(owner)}/{re.escape(repo)}/raw/[^/]+/(.+)"
        m = re.match(pattern, url)
        if m:
            return m.group(1)

        # raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}
        pattern2 = rf"https?://raw\.githubusercontent\.com/{re.escape(owner)}/{re.escape(repo)}/[^/]+/(.+)"
        m2 = re.match(pattern2, url)
        if m2:
            return m2.group(1)

        return ""

    async def _verify_path_exists(
        self, owner: str, repo: str, path: str, branch: str
    ) -> tuple:
        """Check if a file exists in the GitHub repo via Contents API."""
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        headers = {"Accept": "application/vnd.github.v3+json"}

        # Try to get token
        try:
            creds = _get_github_creds(context=self.agent.context, params=self.args)
            if creds.token:
                headers["Authorization"] = f"token {creds.token}"
        except Exception:
            pass

        try:
            resp = requests.get(
                api_url, headers=headers, params={"ref": branch}, timeout=10
            )
            if resp.status_code == 200:
                return path, True
            elif resp.status_code == 404:
                return path, False
            else:
                logger.warning(
                    f"[_verify_path_exists] GitHub API returned {resp.status_code} for {path}"
                )
                return path, False
        except Exception as e:
            logger.error(f"[_verify_path_exists] Request failed: {e}")
            return path, False
