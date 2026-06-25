### replace_in_deliverable
Surgically edit an existing deliverable document by finding and replacing specific text.
This tool **only** works on files within the project's `deliverables/` directory and only on document file types (`.md`, `.txt`, `.json`, `.yaml`, `.docx`, etc.) — it will **never** touch source code files.

Use this when you need to **update a specific section** of a deliverable you previously saved (e.g., update version numbers, revise a paragraph, fix a table). For creating new deliverables, use `save_deliverable` instead.

**Arguments:**
- `path` (required): Path to the deliverable file. Can be:
  - A filename relative to `deliverables/` (e.g., `"architect_20260514_120000.md"`)
  - An absolute path within the project's `deliverables/` directory
- `search_string` (required unless `replacements` is provided): The exact text to find in the deliverable
- `replace_string` (required unless `replacements` is provided): The text to replace the search string with

**Batch format** (multiple replacements in one call):
- `replacements` (array): Array of `{"search": "...", "replace": "..."}` objects

**Usage (single replacement):**
~~~json
{
    "thoughts": ["I need to update the tech stack version in my architecture spec."],
    "tool_name": "replace_in_deliverable",
    "tool_args": {
        "path": "architect_20260514_120000.md",
        "search_string": "- **Next.js**: 14.2.15",
        "replace_string": "- **Next.js**: 15.0.0"
    }
}
~~~

**Usage (batch replacements):**
~~~json
{
    "thoughts": ["I need to update multiple version numbers in the architecture spec."],
    "tool_name": "replace_in_deliverable",
    "tool_args": {
        "path": "architect_20260514_120000.md",
        "replacements": [
            {"search": "- **Next.js**: 14.2.15", "replace": "- **Next.js**: 15.0.0"},
            {"search": "- **Prisma**: 5.22.0", "replace": "- **Prisma**: 6.0.0"}
        ]
    }
}
~~~

**🔴 IMPORTANT**: This tool is for deliverable documents ONLY. It will refuse to edit source code files (`.py`, `.ts`, `.jsx`, etc.). For code edits, delegate to a `code` profile subordinate.
