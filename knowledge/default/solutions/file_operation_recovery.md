# File Operation Recovery Patterns

## Problem
When `read_file` or `write_file` fails with "file not found", agents retry the exact same path multiple times instead of adapting.

## Wrong (common mistake)
```
read_file → "not found" → read_file (same path) → "not found" → read_file (same path) ...
```
Retrying the identical operation will never produce a different result.

## Correct Recovery Pattern
```
read_file → "not found" 
  → list_dir on the parent directory FIRST
  → identify the actual filename/path from the listing
  → read_file with the corrected path
```

## Rules
1. If `read_file` fails with "not found", run `list_dir` on the parent directory BEFORE retrying
2. If a build command fails, read the FULL error output before retrying
3. Never retry the exact same command with the exact same arguments more than ONCE
4. If a tool fails twice with the same error, change your approach entirely:
   - Try a different path
   - Search for the file using `find` or `grep`
   - Check if the file needs to be created first

## When This Appears
- Working with generated project structures where filenames may differ from expectations
- After scaffolding tools (create-next-app, vite, etc.) produce slightly different layouts
- When file extensions are ambiguous (.js vs .jsx vs .ts vs .tsx)
