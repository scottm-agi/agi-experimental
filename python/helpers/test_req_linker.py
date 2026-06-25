"""Test-Requirement Linker — scans test files for [REQ-xxxxx] tags.

Completes the P0-3 traceability triangle: REQ → file (TraceabilityIndex)
and REQ → BDD (bdd_generator) already exist. This adds REQ → test.

The tdd_generator.py embeds REQ-IDs in test descriptions like:
    it('[REQ-a1b2c]: User can login', () => {
This module scans test files and extracts those mappings.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Set

_REQ_PATTERN = re.compile(r'\[REQ-([a-f0-9]{5,})\]')


def scan_test_files_for_reqs(
    project_dir: str,
    test_dirs: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    """Scan test files for [REQ-xxxxx] patterns.

    Args:
        project_dir: Project root
        test_dirs: Directories to scan. Defaults to ['__tests__', 'tests', 'test', 'src/__tests__']

    Returns:
        {req_id: [test_file_paths]} mapping where paths are relative to project_dir.
    """
    if test_dirs is None:
        test_dirs = ['__tests__', 'tests', 'test', 'src/__tests__']

    req_map: Dict[str, List[str]] = {}

    for test_dir_name in test_dirs:
        test_dir = os.path.join(project_dir, test_dir_name)
        if not os.path.isdir(test_dir):
            continue
        for root, _, files in os.walk(test_dir):
            for fname in files:
                if not _is_test_file(fname):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                except IOError:
                    continue
                for match in _REQ_PATTERN.finditer(content):
                    req_id = f"REQ-{match.group(1)}"
                    if req_id not in req_map:
                        req_map[req_id] = []
                    rel_path = os.path.relpath(fpath, project_dir)
                    if rel_path not in req_map[req_id]:
                        req_map[req_id].append(rel_path)

    return req_map


def _is_test_file(fname: str) -> bool:
    """Check if filename matches known test file patterns."""
    # JavaScript/TypeScript test patterns
    test_suffixes = (
        '.test.ts', '.test.tsx', '.test.js', '.test.jsx',
        '.spec.ts', '.spec.tsx', '.spec.js', '.spec.jsx',
    )
    if fname.endswith(test_suffixes):
        return True
    # Python test patterns
    if fname.endswith('.py') and (fname.startswith('test_') or fname.endswith('_test.py')):
        return True
    return False


def get_unlinked_reqs(
    all_req_ids: List[str],
    req_map: Dict[str, List[str]],
) -> List[str]:
    """Return REQ-IDs that have NO linked test files."""
    return [r for r in all_req_ids if r not in req_map]
