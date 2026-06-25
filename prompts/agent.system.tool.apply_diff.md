### tool name: apply_diff

Apply targeted changes to an existing file using SEARCH/REPLACE blocks. This is the **preferred tool for modifying existing files** — it preserves all content you don't explicitly change.

**🔴 FIRST CHOICE for modifying existing files.** Use `apply_diff` or `replace_in_file` instead of `write_to_file` for ALL changes to existing files. `write_to_file` regenerates the entire file from memory, causing silent content loss under token pressure.

**When to use**:
- Modifying any existing file (any size)
- Making multiple non-adjacent changes in one call
- Adding imports, inserting functions, replacing blocks

**Arguments**:
- `path` (required): File path (relative to project or absolute)
- `diff` (required): One or more SEARCH/REPLACE blocks

**Format**:
Each change is a SEARCH/REPLACE block. The SEARCH text must match the existing file content **exactly** (including whitespace and indentation):

```
<<<<<<< SEARCH
exact text to find in the file
=======
replacement text
>>>>>>> REPLACE
```

**Multiple changes** — use multiple blocks in a single `diff` argument:
```
<<<<<<< SEARCH
import React from 'react';
=======
import React from 'react';
import { useState } from 'react';
>>>>>>> REPLACE

<<<<<<< SEARCH
export default function Page() {
=======
export const dynamic = 'force-dynamic';

export default function Page() {
>>>>>>> REPLACE
```

**Rules**:
- The SEARCH text must match EXACTLY — including indentation and whitespace
- Always `read_file` first to see current content before editing
- Each SEARCH block replaces only the first occurrence
- If a SEARCH block is not found, the entire operation fails with an error

**Example**:
```json
{
    "tool_name": "apply_diff",
    "tool_args": {
        "path": "src/app/page.tsx",
        "diff": "<<<<<<< SEARCH\nexport default function Page() {\n  return <div>Hello</div>;\n}\n=======\nexport default function Page() {\n  return (\n    <main className=\"container\">\n      <h1>Welcome</h1>\n    </main>\n  );\n}\n>>>>>>> REPLACE"
    }
}
```
