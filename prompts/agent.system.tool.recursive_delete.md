### recursive_delete
Safely delete a directory within a project scope. This tool enforces project-level boundaries and only allows deletion of known-safe build/cache directories.

**ALWAYS use this tool instead of `rm -rf` via terminal.** Terminal `rm -rf` commands are blocked by the system guard.

**Allowed directories** (programmatic allowlist):
- `.next`, `.turbo`, `.cache`, `.parcel-cache`, `.nuxt`, `.output`, `.svelte-kit`
- `dist`, `build`, `out`, `tmp`, `coverage`, `.coverage`
- `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`
- `node_modules/.cache`, `node_modules/.vite`, `node_modules/.tmp`

**Blocked by default**:
- Project root directories (users delete these via UI)
- Source code directories (`src/`, `app/`, `pages/`, etc.)
- System paths (`/agix/`, `/agix/`, etc.)
- `node_modules/` root (use `build_health_guard` pattern instead)
- Any directory not on the allowlist

usage:
~~~json
{
    "thoughts": [
        "I need to clean up stale build artifacts...",
        "The .next cache is corrupted, I should delete it before rebuilding...",
    ],
    "headline": "Cleaning up stale .next build cache",
    "tool_name": "recursive_delete",
    "tool_args": {
        "path": "/agix/usr/projects/my_app/.next"
    }
}
~~~
