### tool name: replace_in_file

Make targeted edits to an existing file using search-and-replace. Use this when you need to modify specific sections without rewriting the entire file.

**When to use**: Modifying ANY existing file, regardless of how many lines change. This is the ONLY safe way to edit existing files — it preserves all content you don't explicitly change.

**🔴 NEVER use `write_to_file` to modify existing files.** Using `write_to_file` on existing files causes **silent content loss** — you must regenerate the ENTIRE file from memory, and under token pressure you WILL drop sections. Use `replace_in_file` or `apply_diff` instead.

**Arguments**:
- `path`: Absolute path to the file to edit
- `replacements`: Array of search/replace pairs. Each pair contains:
  - `search`: The exact text to find (must match verbatim, including whitespace)
  - `replace`: The replacement text

**Rules**:
- The `search` text must match EXACTLY — including indentation and whitespace
- Each replacement is applied independently and sequentially
- If a search string is not found, the replacement is skipped with a warning
- Always `read_file` first to see the current content before editing
- For large changes, use MULTIPLE `replace_in_file` calls — one per section. This is safer than `write_to_file` because each call only touches the specific section you're changing

**Example**:
```json
{
    "tool_name": "replace_in_file",
    "tool_args": {
        "path": "/path/to/file.py",
        "replacements": [
            {
                "search": "old_function_name()",
                "replace": "new_function_name()"
            }
        ]
    }
}
```
