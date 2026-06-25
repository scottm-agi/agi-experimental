"""
Universal Hashing Helper — Dedup & Fingerprint (MD5)

Provides centralized, fast MD5-based hashing for all non-security
dedup/fingerprint/cache-key use cases across the system.

For SECURITY hashing (passwords, HMAC, webhook signatures), use SHA256
directly via hashlib — those are NOT handled here.

Usage:
    from python.helpers.hashing import dedup_hash, dedup_hash_short, normalized_tool_hash

    # General dedup hash (any input type)
    h = dedup_hash({"tool": "search", "args": {"query": "test"}})

    # Short hash for compact storage
    h = dedup_hash_short("some content", length=16)

    # Tool call dedup with timestamp/UUID normalization
    h = normalized_tool_hash("search_engine", {"query": "Salesforce AI"})
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional


def dedup_hash(value: Any) -> str:
    """
    General-purpose MD5 dedup hash. Accepts any JSON-serializable input.
    
    Returns full 32-char hex digest. Dict keys are sorted for determinism.
    """
    serialized = _stable_serialize(value)
    # Safe: _stable_serialize() uses json.dumps(ensure_ascii=True) which escapes
    # surrogates to \\ud800 ASCII sequences — no raw surrogate codepoints survive.
    # If you change _stable_serialize to use ensure_ascii=False, you MUST add
    # errors='replace' here to prevent UnicodeEncodeError on surrogate input.
    return hashlib.md5(serialized.encode("utf-8", errors="replace")).hexdigest()


def dedup_hash_short(value: Any, length: int = 16) -> str:
    """
    Truncated MD5 hash for compact storage (default 16 chars).
    
    Use when full 32-char hash is overkill (e.g., log messages, progress dicts).
    """
    return dedup_hash(value)[:length]


def content_hash(value: str) -> str:
    """
    MD5 hash of a raw string. Returns full 32-char hex digest.
    
    Unlike dedup_hash(), this does NOT JSON-serialize the input —
    it hashes the string directly, producing the same result as
    ``hashlib.md5(value.encode("utf-8", errors="replace")).hexdigest()``.
    
    Use this for content fingerprinting, file hashing, and anywhere
    the existing code does ``hashlib.md5(x.encode()).hexdigest()``.
    """
    return hashlib.md5(value.encode("utf-8", errors="replace")).hexdigest()


def content_hash_short(value: str, length: int = 12) -> str:
    """
    Truncated MD5 hash of a raw string (default 12 chars).
    
    Drop-in replacement for ``hashlib.md5(x.encode()).hexdigest()[:N]``.
    """
    return content_hash(value)[:length]


def normalized_tool_hash(tool_name: str, tool_args: Any) -> str:
    """
    Tool call dedup hash with timestamp/UUID normalization.
    
    Strips volatile values (timestamps, UUIDs) from args before hashing,
    so calls that differ only in ephemeral metadata produce the same hash.
    
    This is the function all tool-level dedup should use.
    """
    args_str = _stable_serialize(tool_args)
    
    # Normalize: strip timestamps (ISO 8601)
    args_str = re.sub(
        r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s"]*',
        '[TS]',
        args_str,
    )
    # Normalize: strip UUIDs
    args_str = re.sub(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        '[UUID]',
        args_str,
    )
    
    call_sig = f"{tool_name}:{args_str}"
    return hashlib.md5(call_sig.encode("utf-8", errors="replace")).hexdigest()


def _stable_serialize(value: Any) -> str:
    """
    Deterministic JSON serialization. Dict keys sorted, fallback to str().
    """
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


# ============================================================================
# CODE SYNC VALIDATOR — MD5-based drift detection for duplicated functions
# Issue #1112: Prevents silent drift in code that must stay in sync
# ============================================================================

import ast
import textwrap
from dataclasses import dataclass, field
from typing import List, Dict


def extract_function_source(filepath: str, function_name: str) -> str:
    """Extract a function's source code from a Python file using AST.
    
    Returns the normalized source text (dedented, stripped).
    Searches top-level functions and methods inside classes.
    """
    with open(filepath, "r") as f:
        source = f.read()
    
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            start_line = node.lineno - 1  # 0-indexed
            if hasattr(node, 'end_lineno') and node.end_lineno:
                end_line = node.end_lineno
            else:
                end_line = len(lines)
                indent = len(lines[start_line]) - len(lines[start_line].lstrip())
                for i in range(start_line + 1, len(lines)):
                    stripped = lines[i].lstrip()
                    if stripped and not stripped.startswith('#'):
                        line_indent = len(lines[i]) - len(stripped)
                        if line_indent <= indent and (stripped.startswith('def ') or stripped.startswith('class ')):
                            end_line = i
                            break
            
            func_source = "".join(lines[start_line:end_line])
            return textwrap.dedent(func_source).strip()
    
    raise ValueError(f"Function '{function_name}' not found in {filepath}")


def md5_of_source(source: str) -> str:
    """MD5 hash of normalized source code."""
    return hashlib.md5(source.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class SyncPair:
    """A pair of functions that must stay in sync."""
    source_file: str
    source_function: str
    copy_file: str
    copy_function: str
    label: str = ""


class CodeSyncValidator:
    """Registry-based MD5 drift detection for duplicated code.
    
    Register pairs of (source_file, function) → (copy_file, function).
    Call validate_all() to detect drift. Returns a list of drift reports.
    
    Usage:
        validator = CodeSyncValidator()
        validator.register(
            "python/helpers/mcp_handler.py", "_normalize_mcp_args",
            "tests/test_mcp_normalization.py", "_normalize_mcp_args",
        )
        drifts = validator.validate_all()
        assert len(drifts) == 0, f"Code sync drift detected: {drifts}"
    """
    
    def __init__(self):
        self._pairs: List[SyncPair] = []
    
    def register(
        self,
        source_file: str,
        source_function: str,
        copy_file: str,
        copy_function: str,
        label: str = "",
    ) -> None:
        """Register a sync pair. Label is optional human-readable description."""
        if not label:
            label = f"{source_function} in {source_file} ↔ {copy_file}"
        self._pairs.append(SyncPair(
            source_file=source_file,
            source_function=source_function,
            copy_file=copy_file,
            copy_function=copy_function,
            label=label,
        ))
    
    def validate_all(self) -> List[Dict[str, Any]]:
        """Validate all registered pairs. Returns list of drift reports.
        
        Empty list = all pairs in sync. Each drift report contains:
        - label: human-readable description
        - function: function name
        - source_file, copy_file: file paths
        - source_hash, copy_hash: MD5 hashes
        - drifted: True
        """
        drifts: List[Dict[str, Any]] = []
        
        for pair in self._pairs:
            try:
                source_body = extract_function_source(pair.source_file, pair.source_function)
                copy_body = extract_function_source(pair.copy_file, pair.copy_function)
                
                source_hash = md5_of_source(source_body)
                copy_hash = md5_of_source(copy_body)
                
                if source_hash != copy_hash:
                    drifts.append({
                        "label": pair.label,
                        "function": pair.source_function,
                        "source_file": pair.source_file,
                        "copy_file": pair.copy_file,
                        "source_hash": source_hash,
                        "copy_hash": copy_hash,
                        "drifted": True,
                    })
            except Exception as e:
                drifts.append({
                    "label": pair.label,
                    "function": pair.source_function,
                    "source_file": pair.source_file,
                    "copy_file": pair.copy_file,
                    "error": str(e),
                    "drifted": True,
                })
        
        return drifts
    
    def validate_or_raise(self) -> None:
        """Validate all pairs and raise AssertionError if any drift detected."""
        drifts = self.validate_all()
        if drifts:
            details = "\n".join(
                f"  - {d['label']}: source={d.get('source_hash','?')[:8]} copy={d.get('copy_hash','?')[:8]}"
                for d in drifts
            )
            raise AssertionError(
                f"Code sync drift detected in {len(drifts)} pair(s):\n{details}\n"
                "Update the copy to match the source, or update the registry."
            )
