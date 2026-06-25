### write_to_file

Write content directly to a file in the project sandbox. **This tool is for CREATING NEW FILES ONLY.**

**🔴 NEVER use `write_to_file` to modify existing files.** Using `write_to_file` on existing files causes **silent content loss** — you must regenerate the ENTIRE file from memory, and under token pressure you WILL silently drop sections (typically from the bottom of the file).

**For ALL modifications to existing files, use surgical edit tools:**
1. **`replace_in_file`** — targeted search/replace on specific sections (PREFERRED)
2. **`apply_diff`** — SEARCH/REPLACE blocks for multi-section changes

**Example — adding `export const dynamic = 'force-dynamic';` to a file:**
- ✅ `replace_in_file` → insert at the right location, preserving everything else
- ❌ `write_to_file` → regenerate entire file from memory → CONTENT LOSS

Arguments:
- `path` (required): File path (relative to project or absolute starting with `/agix/usr/projects/`)
- `content` (required): Full file content to write
- `overwrite` (optional, default true): Whether to overwrite if file exists
- `overwrite_force` (optional, default false): Bypass content regression guard (only if content reduction is truly intentional)

**🔴 CRITICAL — ALWAYS USE write_to_file INSTEAD OF heredoc/cat FOR FILE CREATION:**
- NEVER use `code_execution_tool` with `cat <<EOF > file` or heredoc syntax to create files
- Heredoc via `code_execution_tool` consumes model output tokens for BOTH the tool call AND the file content, which can cause truncation on very large files
- `write_to_file` sends content directly to disk without consuming output tokens

**🔴 FILE SIZE LIMIT (1500 LINES MAXIMUM):**
- You must **NEVER** author a single file that exceeds 1500 lines. 
- If a component or service is growing too large, you must **MODULARIZE the architecture** by extracting logic, helpers, or sub-components into separate new files. Do NOT attempt to write massive monolithic files.

**🔴 ALWAYS VERIFY FILE DOESN'T EXIST BEFORE CALLING:**
After scaffolding with `create-next-app`, many files ALREADY EXIST: `.gitignore`, `package.json`, `tsconfig.json`, `next.config.*`, `src/app/layout.tsx`, `src/app/page.tsx`, `src/app/globals.css`. If you need to modify ANY of these, use `replace_in_file` — NOT `write_to_file`. If unsure whether a file exists, check with `read_file` first.

**When to use write_to_file vs other tools:**
- Creating new files → `write_to_file` ✅
- Modifying existing files → `replace_in_file` or `apply_diff` ✅
- Running commands, installing packages → `code_execution_tool` ✅
- File search, directory listing → `code_execution_tool` ✅

usage:

~~~json
{
    "thoughts": [
        "Need to create new React component...",
        "Using write_to_file since this is a NEW file"
    ],
    "headline": "Creating landing page component",
    "tool_name": "write_to_file",
    "tool_args": {
        "path": "src/pages/LandingPage.tsx",
        "content": "import React from 'react';\n\nexport default function LandingPage() {\n  return <div>Landing</div>;\n}"
    }
}
~~~
