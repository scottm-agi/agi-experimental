"""Scaffold cleanup checker — L1 deterministic tool.

Detects leftover scaffold boilerplate that should be replaced
with project-specific content. Universal — works for any framework.

Complements the existing boilerplate_detector.py with a lighter-weight,
dependency-free API designed for pre-build cleanup verification.
No external dependencies (no evidence_persistence, no logging setup).

Root Cause (F-6, Iteration 3 audit):
    Scaffold boilerplate (e.g., Create Next App default pages, README
    with framework defaults) persists into the final build because
    there's no cleanup phase step. This causes false content that
    doesn't match the user's prompt.
"""

import os
import re
from typing import Optional

# Universal boilerplate signatures (framework-agnostic)
BOILERPLATE_SIGNATURES = {
    'nextjs': [
        'Create Next App',
        'create-next-app',
        'Get started by editing',
        'app/page.tsx',
        'Learn More',
        'Deploy on Vercel',
        'next/font/google',  # Only in default template
    ],
    'vite': [
        'Vite + React',
        'Click on the Vite and React logos',
        'Edit src/App.tsx and save to test HMR',
    ],
    'create-react-app': [
        'Learn React',
        'Edit <code>src/App.js</code>',
    ],
    'generic': [
        'This project was bootstrapped with',
        'Available Scripts',
        '# Getting Started with',
        'logo512.png',
    ]
}


def detect_scaffold_boilerplate(project_dir: str, planning_only: bool = False) -> dict:
    """Scan project for leftover scaffold boilerplate.

    Args:
        project_dir: Absolute path to the project root directory.
        planning_only: If True, skip scaffold checks (no code generated yet).

    Returns:
        dict with:
            has_boilerplate: bool — True if scaffold boilerplate detected
            framework: str or None — detected framework ('nextjs', 'vite', etc.)
            files: list[str] — relative paths of files with boilerplate
            signatures_found: list[dict] — each with file, signature, framework
    """
    if planning_only:
        return {
            "has_boilerplate": False,
            "framework": None,
            "files": [],
            "skipped": True,
            "reason": "Scaffold check skipped during planning-only phase",
        }

    if not project_dir or not os.path.isdir(project_dir):
        return {'has_boilerplate': False, 'framework': None, 'files': [], 'signatures_found': []}

    results = {'has_boilerplate': False, 'framework': None, 'files': [], 'signatures_found': []}

    # Check key files for boilerplate
    check_files = [
        'README.md',
        'src/app/page.tsx', 'src/app/page.jsx', 'app/page.tsx', 'app/page.jsx',
        'src/App.tsx', 'src/App.jsx', 'src/App.js',
        'index.html',
    ]

    for rel_path in check_files:
        full_path = os.path.join(project_dir, rel_path)
        if not os.path.isfile(full_path):
            continue
        try:
            with open(full_path, 'r', errors='ignore') as f:
                content = f.read(4096)  # Only check first 4KB
        except IOError:
            continue

        for framework, sigs in BOILERPLATE_SIGNATURES.items():
            for sig in sigs:
                if sig in content:
                    results['has_boilerplate'] = True
                    # Set framework to the first detected framework
                    if results['framework'] is None:
                        results['framework'] = framework
                    if rel_path not in results['files']:
                        results['files'].append(rel_path)
                    results['signatures_found'].append({
                        'file': rel_path,
                        'signature': sig,
                        'framework': framework,
                    })

    return results


def build_cleanup_advisory(detection_result: dict) -> Optional[str]:
    """Build an advisory message for scaffold cleanup.

    Args:
        detection_result: dict from detect_scaffold_boilerplate().

    Returns:
        Advisory message string, or None if no boilerplate detected.
    """
    if not detection_result.get('has_boilerplate'):
        return None

    framework = detection_result.get('framework', 'unknown')
    files = detection_result.get('files', [])
    files_str = ', '.join(f'`{f}`' for f in files)

    return (
        f"## ⚠️ SCAFFOLD BOILERPLATE DETECTED ({framework})\n\n"
        f"The following files still contain default scaffold content: {files_str}\n\n"
        f"You MUST replace this boilerplate with project-specific content:\n"
        f"- Replace default page text with the user's requested content\n"
        f"- Update README.md with project-specific documentation\n"
        f"- Remove 'Get started by editing' and similar placeholder text\n"
    )
