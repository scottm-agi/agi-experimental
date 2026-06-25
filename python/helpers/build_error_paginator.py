"""Build error paginator — exposes build error history via a callable interface.

RCA-365 F-12: When truncation loses important build error details, agents need
a way to retrieve the full, un-truncated error output. This module provides
a simple in-memory store with pagination support.

Functions:
    store_error(project_dir, error_text) — store a new error
    get_latest_full_error(project_dir) — get the most recent un-truncated error
    get_error_count(project_dir) — count of stored errors
    get_page(project_dir, error_index, offset, length) — paginate through error text
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional


# ── Module-level error store ────────────────────────────────────────────
# Keyed by normalized project_dir → list of full error strings.
# Kept in memory (same lifetime as the agent process).
_error_store: dict[str, list[str]] = defaultdict(list)

# Max errors to retain per project (prevent unbounded memory growth)
_MAX_ERRORS_PER_PROJECT = 50


def store_error(project_dir: str, error_text: str) -> None:
    """Store a full (un-truncated) build error for later retrieval.

    Args:
        project_dir: The project directory the error belongs to.
        error_text: The complete error output string.
    """
    key = _normalize_key(project_dir)
    _error_store[key].append(error_text)
    # Evict oldest if over limit
    if len(_error_store[key]) > _MAX_ERRORS_PER_PROJECT:
        _error_store[key] = _error_store[key][-_MAX_ERRORS_PER_PROJECT:]


def get_latest_full_error(project_dir: str) -> Optional[str]:
    """Return the most recent un-truncated error for a project.

    Returns:
        The full error text, or None if no errors stored.
    """
    key = _normalize_key(project_dir)
    errors = _error_store.get(key, [])
    if not errors:
        return None
    return errors[-1]


def get_error_count(project_dir: str) -> int:
    """Return the number of stored errors for a project.

    Returns:
        Integer count of stored errors.
    """
    key = _normalize_key(project_dir)
    return len(_error_store.get(key, []))


def get_page(
    project_dir: str,
    error_index: int = 0,
    offset: int = 0,
    length: int = 2000,
) -> Optional[str]:
    """Return a page (substring) of a specific error's output.

    Useful for agents to paginate through large error output that was
    truncated in the original response.

    Args:
        project_dir: The project directory.
        error_index: Which error to page through (0 = oldest, -1 = newest).
        offset: Character offset to start from.
        length: Number of characters to return.

    Returns:
        The requested substring, or None if error_index is out of range.
    """
    key = _normalize_key(project_dir)
    errors = _error_store.get(key, [])
    if not errors:
        return None
    try:
        error_text = errors[error_index]
    except IndexError:
        return None
    return error_text[offset : offset + length]


def clear_errors(project_dir: str) -> None:
    """Clear all stored errors for a project (e.g., after a successful build).

    Args:
        project_dir: The project directory to clear.
    """
    key = _normalize_key(project_dir)
    _error_store.pop(key, None)


def _normalize_key(project_dir: str) -> str:
    """Normalize project directory path for consistent keying."""
    return project_dir.rstrip("/")
